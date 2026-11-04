"""Settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_output_dir() -> Path:
    """~/Downloads, or ~/Download if that is the folder that exists on this machine."""
    home = Path.home()
    for cand in ("Downloads", "Download"):
        if (home / cand).is_dir():
            return home / cand
    return home / "Downloads"


def _path(env: str, default: Path | str) -> Path:
    return Path(os.environ.get(env, str(default))).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    doc_root: Path
    output_dir: Path
    cache_dir: Path
    audit_log: Path
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_api_key: str = ""
    model: str = "qwen3:14b"
    num_ctx: int = 8192
    # Conservative characters-per-token estimate. Ollama silently drops the start of an
    # over-long prompt, so it is better to under-fill the context window.
    chars_per_token: float = 3.2
    request_timeout: float = 900.0
    max_condense_rounds: int = 4
    # Seconds a tool call waits for its result before reporting that the job is still running
    # (MCP clients such as Claude give up after about 60 s). 0 waits without limit.
    tool_time_budget: float = 50.0

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            doc_root=_path("DOC_ROOT", "~/local-llm/docs"),
            output_dir=_path("OUTPUT_DIR", _default_output_dir()),
            cache_dir=_path("CACHE_DIR", "~/local-llm/cache"),
            audit_log=_path("AUDIT_LOG", "~/local-llm/audit.jsonl"),
            ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_api_key=os.environ.get("OLLAMA_API_KEY", "").strip(),
            model=os.environ.get("OLLAMA_MODEL", "qwen3:14b"),
            num_ctx=int(os.environ.get("NUM_CTX", "8192")),
            chars_per_token=float(os.environ.get("CHARS_PER_TOKEN", "3.2")),
            request_timeout=float(os.environ.get("OLLAMA_TIMEOUT", "900")),
            tool_time_budget=float(os.environ.get("TOOL_TIME_BUDGET", "50")),
        )

    def ensure_dirs(self) -> None:
        for d in (self.doc_root, self.output_dir, self.audit_log.parent):
            d.mkdir(parents=True, exist_ok=True)
        # The cache holds converted document text: keep it private to the user.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.chmod(0o700)

    # Derived budgets ---------------------------------------------------------
    @property
    def doc_budget_chars(self) -> int:
        """Document characters per model call: ~55% of the window, leaving room for
        the instructions and the answer."""
        return int(self.num_ctx * 0.55 * self.chars_per_token)

    @property
    def note_tokens(self) -> int:
        return max(256, self.num_ctx // 5)

    @property
    def answer_tokens(self) -> int:
        return max(512, self.num_ctx // 3)

    @property
    def ref_chunk_chars(self) -> int:
        """Reference extraction returns only line numbers, so input can use ~50% of the window."""
        return int(self.num_ctx * 0.5 * self.chars_per_token)

    @property
    def ref_output_tokens(self) -> int:
        return int(self.num_ctx * 0.3)
