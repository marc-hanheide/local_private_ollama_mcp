"""Minimal async client for the Ollama chat API, with clear, typed errors."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import httpx

from .config import Settings

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """A user-facing failure talking to the model backend."""


class LLM(Protocol):
    """Anything that can answer a single chat turn. Tests provide a fake."""

    async def chat(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str: ...


class OllamaLLM:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 retries: int = 2, backoff: float = 1.0):
        self.settings = settings
        self.retries = retries
        self.backoff = backoff
        headers = {"Authorization": f"Bearer {settings.ollama_api_key}"} if settings.ollama_api_key else {}
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url,
            headers=headers,
            timeout=httpx.Timeout(settings.request_timeout, connect=10.0),
            transport=transport,
        )

    # -- errors ------------------------------------------------------------------
    def _status_error(self, r: httpx.Response) -> LLMError:
        try:
            detail = str(r.json().get("error", "")).strip()
        except ValueError:
            detail = r.text.strip()[:200]
        url, model = self.settings.ollama_url, self.settings.model
        if r.status_code in (401, 403):
            hint = ("check OLLAMA_API_KEY" if self.settings.ollama_api_key
                    else "the server requires a bearer token; set OLLAMA_API_KEY")
            return LLMError(f"Ollama at {url} rejected the request ({r.status_code}): {hint}.")
        if r.status_code == 404:
            return LLMError(f"Model '{model}' not found on Ollama at {url}. Run 'ollama pull {model}' "
                            f"or set OLLAMA_MODEL. ({detail or 'not found'})")
        return LLMError(f"Ollama at {url} returned HTTP {r.status_code}: {detail or 'no details'}.")

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        url = self.settings.ollama_url
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            try:
                r = await self._client.request(method, path, **kw)
            except httpx.ConnectError as e:
                if last:
                    raise LLMError(f"Cannot connect to Ollama at {url}. Is it running ('ollama serve') "
                                   "and is OLLAMA_URL correct?") from e
            except httpx.TimeoutException as e:
                # A timeout on a long generation is unlikely to succeed on retry.
                raise LLMError(f"Ollama at {url} timed out after {self.settings.request_timeout:.0f}s. "
                               "Narrow the task with char_range, use a smaller model, or raise OLLAMA_TIMEOUT.") from e
            except httpx.HTTPError as e:
                if last:
                    raise LLMError(f"Network error talking to Ollama at {url}: {type(e).__name__}.") from e
            else:
                if r.status_code < 400:
                    return r
                if r.status_code not in RETRY_STATUS or last:
                    raise self._status_error(r)
            delay = self.backoff * (2 ** attempt)
            log.warning("Ollama request failed (attempt %d/%d), retrying in %.1fs", attempt + 1, self.retries + 1, delay)
            await asyncio.sleep(delay)
        raise LLMError("Ollama request failed.")  # pragma: no cover

    # -- API -----------------------------------------------------------------------
    async def chat(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        options: dict[str, Any] = {"temperature": temperature, "num_ctx": self.settings.num_ctx}
        if max_tokens:
            options["num_predict"] = max_tokens
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "think": False,  # Qwen3 and similar: skip the reasoning trace
            "options": options,
        }
        if schema is not None:
            payload["format"] = schema
        r = await self._request("POST", "/api/chat", json=payload)
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError("Ollama returned a response that is not JSON. Is OLLAMA_URL pointing at Ollama?") from e
        if data.get("error"):
            raise LLMError(f"Ollama reported an error: {data['error']}")
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise LLMError("Ollama returned no message content.")
        return content

    async def list_models(self) -> list[str]:
        r = await self._request("GET", "/api/tags")
        try:
            return [m.get("name", "") for m in r.json().get("models", [])]
        except (ValueError, AttributeError) as e:
            raise LLMError("Unexpected response from Ollama's /api/tags.") from e

    async def version(self) -> str | None:
        try:
            r = await self._request("GET", "/api/version")
            return r.json().get("version")
        except (LLMError, ValueError):
            return None

    async def aclose(self) -> None:
        await self._client.aclose()
