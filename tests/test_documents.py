import pytest

from local_private_ollama_mcp.documents import DocumentError, DocumentStore, convert_pdf

from .conftest import make_pdf


class CountingConverter:
    def __init__(self):
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        return convert_pdf(path)


def test_supported_types_only(settings, docs):
    (docs / "a.md").write_text("# A\ntext")
    (docs / "b.txt").write_text("plain")
    (docs / "c.docx").write_bytes(b"PK")
    store = DocumentStore(settings)
    assert [p.name for p in store.list()] == ["a.md", "b.txt"]
    with pytest.raises(DocumentError, match="Unsupported"):
        store.load("c.docx")


def test_path_traversal_and_missing(settings, docs, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    store = DocumentStore(settings)
    with pytest.raises(DocumentError, match="inside the documents folder"):
        store.load("../secret.txt")
    with pytest.raises(DocumentError, match="No such document"):
        store.load("nope.md")


def test_empty_file_rejected(settings, docs):
    (docs / "empty.md").write_text("  \n")
    with pytest.raises(DocumentError, match="empty"):
        DocumentStore(settings).load("empty.md")


def test_pdf_conversion_is_cached_on_disk_by_hash(settings, docs):
    pdf = docs / "p.pdf"
    make_pdf(pdf, [("Introduction", "Robots in fields."), ("Methods", "We navigate without GPS.")], title="My Proposal")
    conv = CountingConverter()
    doc = DocumentStore(settings, converter=conv).load("p.pdf")
    assert conv.calls == 1 and not doc.from_cache
    assert "Robots in fields" in doc.text
    assert doc.pages == 2 and doc.title == "My Proposal"
    assert [t for _, t in doc.outline] == ["Introduction", "Methods"]
    assert list(settings.cache_dir.glob(f"{doc.sha256}-*.md"))

    # A fresh store (new server process) reuses the disk cache: no conversion.
    conv2 = CountingConverter()
    doc2 = DocumentStore(settings, converter=conv2).load("p.pdf")
    assert conv2.calls == 0 and doc2.from_cache
    assert doc2.text == doc.text and doc2.outline == doc.outline and doc2.pages == 2

    # Changing the file changes the hash, so it is converted again.
    make_pdf(pdf, [("Introduction", "Completely different content.")])
    conv3 = CountingConverter()
    doc3 = DocumentStore(settings, converter=conv3).load("p.pdf")
    assert conv3.calls == 1 and doc3.sha256 != doc.sha256
    assert "Completely different" in doc3.text


def test_broken_pdf_gives_clear_error(settings, docs):
    (docs / "bad.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")

    def boom(path):
        raise RuntimeError("parser exploded")

    with pytest.raises(DocumentError, match="Could not convert bad.pdf"):
        DocumentStore(settings, converter=boom).load("bad.pdf")


def test_scanned_pdf_without_text(settings, docs):
    make_pdf(docs / "scan.pdf", [("x", "y")])
    with pytest.raises(DocumentError, match="scanned PDF"):
        DocumentStore(settings, converter=lambda p: "   ").load("scan.pdf")
