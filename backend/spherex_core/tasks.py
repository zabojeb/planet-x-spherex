"""A small registry of background tasks with progress, for builds that are not sequences.

Archival epochs and MOST searches can take from seconds to minutes; adapters start them here and poll
status(), the same polling contract as sequence jobs (spherex_core.jobs). start() must run on the event loop thread.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger(__name__)
Progress = Callable[[str, int, int], None]


@dataclass
class Task:
    id: str
    status: str = "running"  # running | done | error
    stage: str = "queued"
    done: int = 0
    total: int = 0
    error: str | None = None
    detail: dict | None = None  # extra error payload (e.g. ambiguous-name choices)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def update(self, stage: str, done: int, total: int) -> None:
        self.stage, self.done, self.total = stage, done, total

    def to_dict(self) -> dict:
        d = {"id": self.id, "status": self.status, "stage": self.stage, "done": self.done, "total": self.total,
             "error": self.error, "elapsed": (self.finished or time.time()) - self.started}
        if self.detail:
            d.update(self.detail)
        return d


def claim(lock: Path, max_age_s: float = 900.0) -> bool:
    """Take a cross-process build lock (several uvicorn workers share one disk). False if another process
    holds a fresh one; a lock older than max_age_s is treated as abandoned."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime < max_age_s:
                    return False
                lock.unlink()
            except FileNotFoundError:
                pass
    return False


def release(lock: Path) -> None:
    try:
        lock.unlink()
    except FileNotFoundError:
        pass


class TaskRegistry:
    def __init__(self, max_concurrent: int = 2, keep_failed_s: float = 120.0):
        self.tasks: dict[str, Task] = {}
        self._sem = asyncio.Semaphore(max_concurrent)
        self._handles: set[asyncio.Task] = set()
        self.keep_failed_s = keep_failed_s

    def get(self, key: str) -> Task | None:
        t = self.tasks.get(key)
        if t and t.status == "error" and t.finished and time.time() - t.finished > self.keep_failed_s:
            del self.tasks[key]  # let the client retry after a failure
            return None
        return t

    def start(self, key: str, make: Callable[[Progress], Awaitable[object]]) -> Task:
        """Start make(progress) unless a task with this key is running or recently failed."""
        t = self.get(key)
        if t and t.status in ("running", "error"):
            return t
        t = Task(id=key)
        self.tasks[key] = t
        handle = asyncio.get_running_loop().create_task(self._run(t, make))
        self._handles.add(handle)
        handle.add_done_callback(self._handles.discard)
        return t

    async def _run(self, t: Task, make: Callable[[Progress], Awaitable[object]]) -> None:
        async with self._sem:
            try:
                await make(t.update)
                t.status, t.stage = "done", "done"
            except Exception as e:  # noqa: BLE001 - every failure goes back to the client
                log.warning("task %s failed: %r", t.id, e)
                t.status, t.error = "error", str(e) or e.__class__.__name__
                t.detail = {"choices": e.choices} if hasattr(e, "choices") else None
                t.detail = {**(t.detail or {}), "kind": e.__class__.__name__}
            finally:
                t.finished = time.time()
