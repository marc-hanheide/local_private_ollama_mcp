import json

import httpx
import pytest

from local_private_ollama_mcp.config import Settings
from local_private_ollama_mcp.llm import LLMError, OllamaLLM


def settings(tmp_path, **kw) -> Settings:
    return Settings(doc_root=tmp_path, output_dir=tmp_path, cache_dir=tmp_path, audit_log=tmp_path / "a",
                    **kw)


def client(tmp_path, handler, **kw) -> OllamaLLM:
    return OllamaLLM(settings(tmp_path, **kw), transport=httpx.MockTransport(handler), backoff=0)


async def test_payload_and_bearer_token(tmp_path):
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"message": {"content": "hi"}})

    llm = client(tmp_path, handler, ollama_api_key="tok123", model="qwen3:14b", num_ctx=8192)
    out = await llm.chat("sys", "user", schema={"type": "object"}, max_tokens=50)
    assert out == "hi"
    assert seen["auth"] == "Bearer tok123"
    body = seen["body"]
    assert body["model"] == "qwen3:14b" and body["think"] is False and body["stream"] is False
    assert body["options"] == {"temperature": 0.0, "num_ctx": 8192, "num_predict": 50}
    assert body["format"] == {"type": "object"}


async def test_no_auth_header_without_token(tmp_path):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"message": {"content": "ok"}})

    await client(tmp_path, handler).chat("s", "u")
    assert seen["auth"] is None


@pytest.mark.parametrize("status,match", [
    (401, "OLLAMA_API_KEY"), (403, "OLLAMA_API_KEY"), (404, "ollama pull"), (400, "HTTP 400"),
])
async def test_status_errors_are_explained(tmp_path, status, match):
    llm = client(tmp_path, lambda r: httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(LLMError, match=match):
        await llm.chat("s", "u")


async def test_transient_errors_are_retried(tmp_path):
    attempts = []

    def handler(req):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"message": {"content": "finally"}})

    assert await client(tmp_path, handler).chat("s", "u") == "finally"
    assert len(attempts) == 3


async def test_connection_refused(tmp_path):
    def handler(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError, match="Cannot connect to Ollama"):
        await client(tmp_path, handler).chat("s", "u")


async def test_timeout_is_not_retried(tmp_path):
    attempts = []

    def handler(req):
        attempts.append(1)
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMError, match="timed out"):
        await client(tmp_path, handler).chat("s", "u")
    assert len(attempts) == 1


@pytest.mark.parametrize("resp,match", [
    (httpx.Response(200, text="<html>"), "not JSON"),
    (httpx.Response(200, json={"error": "model crashed"}), "model crashed"),
    (httpx.Response(200, json={"done": True}), "no message content"),
])
async def test_bad_responses(tmp_path, resp, match):
    with pytest.raises(LLMError, match=match):
        await client(tmp_path, lambda r: resp).chat("s", "u")


async def test_list_models(tmp_path):
    llm = client(tmp_path, lambda r: httpx.Response(200, json={"models": [{"name": "qwen3:14b"}]}))
    assert await llm.list_models() == ["qwen3:14b"]
