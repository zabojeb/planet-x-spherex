"""Background sequence builds inside a running asyncio loop, with progress and a result cache on disk.

Interface-agnostic: the HTTP API and the MCP server share one JobManager through services.py.
submit() must be called from the event loop thread (it schedules a task).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import results
from .params import SequenceParams
from .sequence import build_sequence

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    params: SequenceParams
    status: str = "queued"  # queued | running | done | error
    stage: str = "queued"
    done: int = 0
    total: int = 0
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def update(self, stage: str, done: int, total: int) -> None:
        self.stage, self.done, self.total = stage, done, total

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "stage": self.stage, "done": self.done, "total": self.total,
                "error": self.error, "elapsed": (self.finished or time.time()) - self.started}


class JobManager:
    def __init__(self, root: Path, max_concurrent: int = 3):
        self.root = root
        self.jobs: dict[str, Job] = {}
        self._sem = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task] = set()

    def result_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def is_done(self, job_id: str) -> bool:
        return results.is_complete(self.result_dir(job_id))

    def submit(self, params: SequenceParams, force: bool = False) -> Job:
        """Start a build unless the result is cached or the same build is already running."""
        key = params.key()
        if not force and self.is_done(key):
            job = self.jobs.get(key) or Job(id=key, params=params, status="done", stage="done")
            job.status = "done"
            self.jobs[key] = job
            return job
        job = self.jobs.get(key)
        if job and job.status in ("queued", "running"):
            return job
        job = Job(id=key, params=params)
        self.jobs[key] = job
        task = asyncio.get_running_loop().create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def _run(self, job: Job) -> None:
        async with self._sem:
            job.status = "running"
            try:
                await build_sequence(job.params, self.result_dir(job.id), progress=job.update)
                job.status, job.stage = "done", "done"
            except Exception as e:  # noqa: BLE001 - report every failure to the client
                log.exception("sequence %s failed", job.id)
                job.status, job.error = "error", str(e) or e.__class__.__name__
            finally:
                job.finished = time.time()
