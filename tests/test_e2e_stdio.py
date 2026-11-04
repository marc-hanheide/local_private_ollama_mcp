"""End to end: the real server over stdio, talking to a fake Ollama over HTTP."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError


class FakeOllama(BaseHTTPRequestHandler):
    token = "e2e-token"
    seen_auth: list[str | None] = []

    def log_message(self, *a):  # keep test output quiet
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(200, {"models": [{"name": "fake:1b"}]})
        elif self.path == "/api/version":
            self._send(200, {"version": "0.0-test"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        FakeOllama.seen_auth.append(self.headers.get("Authorization"))
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            return self._send(401, {"error": "unauthorized"})
        if body["model"] != "fake:1b":
            return self._send(404, {"error": f"model '{body['model']}' not found"})
        self._send(200, {"message": {"role": "assistant",
                                     "content": json.dumps({"answer": "no", "confidence": "medium"})}})


@pytest.fixture
def fake_ollama():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def stdio_client(tmp_path, url: str, **env) -> Client:
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "p.md").write_text("# Plan\nWe will not use GPS.\n")
    base = {
        "DOC_ROOT": str(docs), "OUTPUT_DIR": str(tmp_path / "out"), "CACHE_DIR": str(tmp_path / "cache"),
        "AUDIT_LOG": str(tmp_path / "audit.jsonl"), "OLLAMA_URL": url, "OLLAMA_MODEL": "fake:1b",
        "OLLAMA_API_KEY": FakeOllama.token,
    }
    base.update(env)
    return Client(StdioTransport(command=sys.executable, args=["-m", "local_private_ollama_mcp"], env=base))


async def test_stdio_server_against_fake_ollama(tmp_path, fake_ollama):
    async with stdio_client(tmp_path, fake_ollama) as c:
        status = (await c.call_tool("server_status", {})).structured_content
        assert status["ok"], status["problems"]
        assert status["ollama_version"] == "0.0-test" and status["auth_configured"]
        r = (await c.call_tool("yes_no", {"name": "p.md", "question": "Is GPS used?"})).structured_content
        assert r["answer"] == "no"
    assert f"Bearer {FakeOllama.token}" in FakeOllama.seen_auth
    audit = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [a["tool"] for a in audit] == ["server_status", "yes_no"]


async def test_stdio_server_reports_wrong_token(tmp_path, fake_ollama):
    async with stdio_client(tmp_path, fake_ollama, OLLAMA_API_KEY="wrong") as c:
        with pytest.raises(ToolError, match="OLLAMA_API_KEY"):
            await c.call_tool("yes_no", {"name": "p.md", "question": "Is GPS used?"})


async def test_stdio_server_survives_ollama_down(tmp_path):
    async with stdio_client(tmp_path, "http://127.0.0.1:9") as c:
        status = (await c.call_tool("server_status", {})).structured_content
        assert not status["ok"] and "Cannot connect" in status["problems"][0]
        # Other tools keep working.
        assert (await c.call_tool("document_overview", {"name": "p.md"})).structured_content["sections"]
