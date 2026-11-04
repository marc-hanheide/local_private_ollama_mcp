"""Deduplicated background jobs with a time budget.

MCP clients give up on a tool call after a fixed time (about 60 seconds for Claude), while a
small local model can need longer. Each tool call therefore runs as a background job keyed by
the tool, the document's identity and the arguments:

- the call waits up to the time budget and returns the result if it is ready;
- otherwise it reports that the job is still running, and the job carries on;
- calling again with the same arguments attaches to the same job (no duplicate work queued
  on Ollama) or returns the finished result, which is kept for a while.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from fastmcp.exceptions import ToolError

T = TypeVar("T")


@dataclass
class Job:
    tool: str
    started: float
    task: asyncio.Task[Any] | None = None
    progress: str = ""
    finished: float | None = None


_current: contextvars.ContextVar[Job | None] = contextvars.ContextVar("local_llm_job", default=None)


def report_progress(message: str) -> None:
    """Record what the current job is doing; shown if the caller has to come back later."""
    job = _current.get()
    if job is not None:
        job.progress = message


class JobRunner:
    def __init__(self, budget: float, ttl: float = 3600.0, max_finished: int = 128):
        self.budget = budget  # seconds; 0 or less waits without limit
        self.ttl = ttl
        self.max_finished = max_finished
        self._jobs: dict[str, Job] = {}

    def _sweep(self) -> None:
        now = time.monotonic()
        finished = [(k, j) for k, j in self._jobs.items() if j.finished is not None]
        for k, j in finished:
            if now - (j.finished or now) > self.ttl:
                self._jobs.pop(k, None)
        finished = [(k, j) for k, j in self._jobs.items() if j.finished is not None]
        for k, _ in sorted(finished, key=lambda kj: kj[1].finished or 0)[: max(0, len(finished) - self.max_finished)]:
            self._jobs.pop(k, None)

    def running(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.finished is None]

    async def run(self, tool: str, key: str, work: Callable[[], Awaitable[T]]) -> T:
        self._sweep()
        job = self._jobs.get(key)
        if job is None:
            job = Job(tool=tool, started=time.monotonic())

            async def runner() -> T:
                _current.set(job)
                try:
                    return await work()
                finally:
                    job.finished = time.monotonic()

            job.task = asyncio.create_task(runner(), name=f"local-llm:{tool}")
            self._jobs[key] = job

        assert job.task is not None
        done, _ = await asyncio.wait({job.task}, timeout=self.budget if self.budget > 0 else None)
        if not done:
            elapsed = time.monotonic() - job.started
            others = len(self.running()) - 1
            raise ToolError(
                f"{tool} is still running on the local model ({elapsed:.0f}s so far"
                f"{'; ' + job.progress if job.progress else ''}). It continues in the background. "
                f"Call {tool} again with exactly the same arguments in about 30 seconds to collect the "
                "result; changing the arguments starts a new job."
                + (f" {others} other job(s) are queued on the same model, so expect delays." if others > 0 else "")
            )
        if job.task.cancelled():
            self._jobs.pop(key, None)
            raise ToolError(f"{tool} was cancelled before it finished; call it again to restart it.")
        exc = job.task.exception()
        if exc is not None:
            self._jobs.pop(key, None)  # let the next call retry
            raise exc
        return job.task.result()
