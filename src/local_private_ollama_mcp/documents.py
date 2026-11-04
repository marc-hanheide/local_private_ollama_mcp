"""Loading documents from the sandbox folder, with a persistent markitdown cache.

PDFs are converted to markdown with markitdown, locally. The converted text is cached
on disk under CACHE_DIR, keyed by the SHA-256 of the original file (and the markitdown
version), so it is reused until the file's content changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf": "pdf", ".md": "markdown", ".markdown": "markdown", ".txt": "text"}
CACHE_FORMAT = 1


class DocumentError(ValueError):
    """A user-facing problem with the requested document."""


@dataclass
class Document:
    name: str
    path: Path
    file_type: str  # "pdf" | "markdown" | "text"
    sha256: str
    size_bytes: int
    text: str
    pages: int | None = None
    title: str | None = None
    # PDF bookmarks as (level, title), level starting at 1.
    outline: list[tuple[int, str]] = field(default_factory=list)
    from_cache: bool = False


def _markitdown_version() -> str:
    try:
        return version("markitdown")
    except PackageNotFoundError:  # pragma: no cover
        return "unknown"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _decode_pdf_string(v: object) -> str | None:
    if isinstance(v, bytes):
        for enc in ("utf-16", "utf-8", "latin-1"):
            try:
                s = v.decode(enc)
                if s.strip():
                    return s.strip().strip("\x00")
            except UnicodeDecodeError:
                continue
        return None
    if isinstance(v, str):
        return v.strip() or None
    return None


def read_pdf_metadata(path: Path) -> tuple[int | None, str | None, list[tuple[int, str]]]:
    """Page count, title and bookmark outline of a PDF (best effort, never raises)."""
    try:
        from pdfminer.pdfdocument import PDFDocument, PDFNoOutlines
        from pdfminer.pdfpage import PDFPage
        from pdfminer.pdfparser import PDFParser
    except ImportError:  # pragma: no cover
        return None, None, []
    pages: int | None = None
    title: str | None = None
    outline: list[tuple[int, str]] = []
    try:
        with path.open("rb") as f:
            doc = PDFDocument(PDFParser(f))
            pages = sum(1 for _ in PDFPage.create_pages(doc))
            if doc.info:
                title = _decode_pdf_string(doc.info[0].get("Title"))
            try:
                for level, t, *_ in doc.get_outlines():
                    t = _decode_pdf_string(t)
                    if t:
                        outline.append((int(level), " ".join(t.split())))
            except PDFNoOutlines:
                pass
    except Exception as e:  # malformed PDFs should not break conversion
        log.warning("Could not read PDF metadata from %s: %s", path.name, e)
    return pages, title, outline


def convert_pdf(path: Path) -> str:
    """Convert a PDF to markdown locally (convert_local never fetches URLs; no plugins)."""
    from markitdown import MarkItDown

    return MarkItDown(enable_plugins=False).convert_local(str(path)).text_content or ""


class DocumentStore:
    def __init__(self, settings: Settings, converter=convert_pdf):
        self.settings = settings
        self.converter = converter
        self._memory: dict[str, Document] = {}  # sha256 -> Document

    # -- paths -----------------------------------------------------------------
    def resolve(self, name: str) -> Path:
        root = self.settings.doc_root
        if not root.is_dir():
            raise DocumentError(f"The documents folder {root} does not exist; check DOC_ROOT.")
        if "\x00" in name:
            raise DocumentError("Invalid file name.")
        p = (root / name).resolve()
        if root not in p.parents:
            raise DocumentError("The file must be inside the documents folder.")
        if not p.is_file():
            raise DocumentError(f"No such document: {name}")
        if p.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise DocumentError(
                f"Unsupported file type '{p.suffix}'. Supported: PDF (.pdf), markdown (.md) and text (.txt)."
            )
        return p

    def list(self) -> list[Path]:
        root = self.settings.doc_root
        return sorted(
            p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        )

    # -- cache -----------------------------------------------------------------
    def _cache_paths(self, sha: str) -> tuple[Path, Path]:
        stem = f"{sha}-markitdown-{_markitdown_version()}"
        return self.settings.cache_dir / f"{stem}.md", self.settings.cache_dir / f"{stem}.json"

    def _read_cache(self, sha: str) -> tuple[str, dict] | None:
        md, meta = self._cache_paths(sha)
        if not (md.is_file() and meta.is_file()):
            return None
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if info.get("format") != CACHE_FORMAT or info.get("sha256") != sha:
                return None
            return md.read_text(encoding="utf-8"), info
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, sha: str, text: str, info: dict) -> None:
        md, meta = self._cache_paths(sha)
        try:
            self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
            self.settings.cache_dir.chmod(0o700)
            md.write_text(text, encoding="utf-8")
            meta.write_text(json.dumps({"format": CACHE_FORMAT, "sha256": sha, **info}), encoding="utf-8")
            md.chmod(0o600)
            meta.chmod(0o600)
        except OSError as e:  # a failed cache write must not break the tool call
            log.warning("Could not write cache for %s: %s", sha[:12], e)

    # -- loading ---------------------------------------------------------------
    def load(self, name: str) -> Document:
        path = self.resolve(name)
        file_type = SUPPORTED_SUFFIXES[path.suffix.lower()]
        try:
            sha = sha256_file(path)
            size = path.stat().st_size
        except OSError as e:
            raise DocumentError(f"Cannot read {name}: {e.strerror or type(e).__name__}.") from e
        rel = str(path.relative_to(self.settings.doc_root))

        if sha in self._memory:
            d = self._memory[sha]
            return Document(**{**d.__dict__, "name": rel, "path": path, "from_cache": True})

        if file_type != "pdf":
            text = path.read_text(encoding="utf-8", errors="replace")
            if not text.strip():
                raise DocumentError(f"{rel} is empty.")
            doc = Document(rel, path, file_type, sha, size, text)
        else:
            cached = self._read_cache(sha)
            if cached is not None:
                text, info = cached
                doc = Document(
                    rel, path, file_type, sha, size, text,
                    pages=info.get("pages"), title=info.get("title"),
                    outline=[(int(lv), str(t)) for lv, t in info.get("outline", [])],
                    from_cache=True,
                )
            else:
                try:
                    text = self.converter(path)
                except Exception as e:  # markitdown/pdfminer raise many types on broken files
                    log.exception("PDF conversion failed for %s", rel)
                    raise DocumentError(
                        f"Could not convert {rel} to text ({type(e).__name__}). The PDF may be damaged, "
                        "encrypted or password-protected."
                    ) from e
                if not text.strip():
                    raise DocumentError(
                        f"No text could be extracted from {rel}; it may be a scanned PDF without a "
                        "text layer. Run OCR on it first (e.g. ocrmypdf)."
                    )
                pages, title, outline = read_pdf_metadata(path)
                self._write_cache(sha, text, {"pages": pages, "title": title, "outline": outline})
                doc = Document(rel, path, file_type, sha, size, text, pages, title, outline)

        if len(self._memory) >= 16:
            self._memory.pop(next(iter(self._memory)))
        self._memory[sha] = doc
        return doc
