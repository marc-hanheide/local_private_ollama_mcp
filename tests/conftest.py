from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from local_private_ollama_mcp.config import Settings
from local_private_ollama_mcp.documents import DocumentStore
from local_private_ollama_mcp.server import REF_SYSTEM, SYSTEM_NOTES, create_server


@dataclass
class Call:
    system: str
    user: str
    schema: dict[str, Any] | None
    max_tokens: int | None


@dataclass
class FakeLLM:
    """Stands in for Ollama. `handler(call)` returns the model's reply."""

    handler: Callable[[Call], str] | None = None
    calls: list[Call] = field(default_factory=list)
    delay: float = 0.0

    async def chat(self, system, user, *, schema=None, temperature=0.0, max_tokens=None) -> str:
        call = Call(system, user, schema, max_tokens)
        self.calls.append(call)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.handler:
            return self.handler(call)
        return default_reply(call)

    def final_calls(self) -> list[Call]:
        return [c for c in self.calls if c.system not in (SYSTEM_NOTES, REF_SYSTEM)]


def default_reply(call: Call) -> str:
    if call.system == SYSTEM_NOTES:
        return "- relevant note"
    if call.system == REF_SYSTEM:
        return json.dumps({"references": []})
    props = (call.schema or {}).get("properties", {})
    if "answer" in props:
        return json.dumps({"answer": "yes", "confidence": "high"})
    if "label" in props:
        return json.dumps({"label": props["label"]["enum"][0]})
    if "score" in props:
        return json.dumps({"justification": "Solid evidence.", "score": props["score"]["minimum"]})
    return "Model answer."


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        doc_root=tmp_path / "docs",
        output_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        audit_log=tmp_path / "audit.jsonl",
        ollama_url="http://127.0.0.1:9",
        num_ctx=2048,
    )


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def docs(settings: Settings) -> Path:
    settings.doc_root.mkdir(parents=True, exist_ok=True)
    return settings.doc_root


@pytest.fixture
def server(settings: Settings, llm: FakeLLM, docs: Path):
    return create_server(settings, llm, DocumentStore(settings))


@pytest.fixture
async def client(server):
    async with Client(server) as c:
        yield c


def make_pdf(path: Path, sections: list[tuple[str, str]], bookmarks: bool = True, title: str | None = None) -> None:
    """Write a simple PDF: one heading line and body paragraph per section, optional bookmarks."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    if title:
        c.setTitle(title)
    for i, (heading, body) in enumerate(sections):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 780, heading)
        if bookmarks:
            c.bookmarkPage(f"s{i}")
            c.addOutlineEntry(heading, f"s{i}", level=0)
        c.setFont("Helvetica", 10)
        y = 760
        words = body.split()
        line: list[str] = []
        for w in words:
            line.append(w)
            if len(" ".join(line)) > 90:
                c.drawString(72, y, " ".join(line))
                y -= 14
                line = []
        if line:
            c.drawString(72, y, " ".join(line))
        c.showPage()
    c.save()
