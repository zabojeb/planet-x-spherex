"""Background sequence builds inside a running asyncio loop, with progress and a result cache on disk.

Interface-agnostic: the HTTP API and the MCP server share one JobManager through services.py.
submit() and detach() must be called from the event loop thread (they schedule tasks).

Slots. At most `max_concurrent` builds run at once; the rest wait in submission order and report their
place in the queue. A build gives its slot back when it finishes, fails, times out or is cancelled.

Lifetime. Nobody needs a build that nobody is waiting for, so a queued or running build is cancelled
- `detach_grace` seconds after its last client detached (the web client detaches when it moves on), or
- when no client has asked about it for `idle_timeout` seconds (a closed tab, a crashed client);
and every build is stopped after `build_timeout` seconds. 0 disables `idle_timeout` and `build_timeout`.

Cancelling stops the pipeline at its next await. A blocking archive query already running in a thread
finishes in the background, but it no longer holds a slot.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import logs
from ..config import settings
from . import results
from .params import SequenceParams
from .sequence import build_sequence

log = logging.getLogger(__name__)

Build = Callable[..., Awaitable[dict]]
_order = itertools.count()


@dataclass
class Job:
    id: str
    params: SequenceParams
    status: str = "queued"  # queued | running | done | error | cancelled
    stage: str = "queued"
    done: int = 0
    total: int = 0
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    position: int | None = None  # builds ahead of this one while queued, filled in by JobManager.describe
    watchers: int = 0
    last_seen: float = field(default_factory=time.monotonic)
    detached_at: float | None = None
    order: int = field(default_factory=lambda: next(_order))
    task: asyncio.Task | None = field(default=None, repr=False)
    cancel_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")

    def update(self, stage: str, done: int, total: int) -> None:
        self.stage, self.done, self.total = stage, done, total

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "stage": self.stage, "done": self.done, "total": self.total,
                "error": self.error, "position": self.position,
                "elapsed": (self.finished or time.time()) - self.started}


class JobManager:
    def __init__(self, root: Path, max_concurrent: int | None = None, build_timeout: float | None = None,
                 idle_timeout: float | None = None, detach_grace: float | None = None,
                 reap_interval: float = 2.0, build: Build = build_sequence):
        self.root = root
        self.max_concurrent = max_concurrent or settings.max_jobs
        self.build_timeout = settings.job_timeout_s if build_timeout is None else build_timeout
        self.idle_timeout = settings.job_idle_s if idle_timeout is None else idle_timeout
        self.detach_grace = settings.job_detach_grace_s if detach_grace is None else detach_grace
        self.reap_interval = reap_interval
        self.jobs: dict[str, Job] = {}
        self._sem = asyncio.Semaphore(self.max_concurrent)
        self._build = build
        self._tasks: set[asyncio.Task] = set()
        self._reaper: asyncio.Task | None = None

    def result_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def is_done(self, job_id: str) -> bool:
        return results.is_complete(self.result_dir(job_id))

    # ------------------------------------------------------------ clients
    def submit(self, params: SequenceParams, force: bool = False) -> Job:
        """Start a build unless the result is cached or the same build is already queued or running.
        The caller becomes one of the build's watchers until it calls detach()."""
        key = params.key()
        token = logs.seq_id.set(key)
        try:
            return self._submit(key, params, force)
        finally:
            logs.seq_id.reset(token)

    def _submit(self, key: str, params: SequenceParams, force: bool) -> Job:
        if not force and self.is_done(key):
            job = self.jobs.get(key)
            if job is None or job.active:  # a finished result on disk wins over a stale in-memory job
                job = Job(id=key, params=params, status="done", stage="done")
                self.jobs[key] = job
            job.status, job.stage = "done", "done"
            logs.event(log, logging.INFO, "job cached", target=params.target.name)
            return job
        job = self.jobs.get(key)
        if job is not None and job.active:
            logs.event(log, logging.INFO, "job joined", status=job.status, stage=job.stage,
                       watchers=job.watchers + 1)
        else:
            job = Job(id=key, params=params)
            self.jobs[key] = job
            loop = asyncio.get_running_loop()
            job.task = loop.create_task(self._run(job))
            self._tasks.add(job.task)
            job.task.add_done_callback(self._tasks.discard)
            self._ensure_reaper(loop)
            running = sum(1 for j in self.jobs.values() if j.status == "running")
            logs.event(log, logging.INFO, "job submitted", target=params.target.name, fov_arcmin=params.fov_arcmin,
                       force=force or None, running=running, slots=self.max_concurrent,
                       ahead=self.queue_position(job) if running >= self.max_concurrent else None)
        job.watchers += 1
        job.detached_at = None
        job.last_seen = time.monotonic()
        return job

    def get(self, job_id: str) -> Job | None:
        """The job, without counting as a client asking about it."""
        return self.jobs.get(job_id)

    def poll(self, job_id: str) -> Job | None:
        """The job; a client asking keeps a queued or running build alive."""
        job = self.jobs.get(job_id)
        if job is not None:
            job.last_seen = time.monotonic()
        return job

    def detach(self, job_id: str) -> Job | None:
        """A client stopped waiting for this build. The last one to leave cancels it after the grace period
        (so a client that comes straight back, e.g. a page reload, finds it still running)."""
        job = self.jobs.get(job_id)
        if job is None or not job.active:
            return job
        job.watchers = max(0, job.watchers - 1)
        if job.watchers == 0:
            job.detached_at = time.monotonic()
        token = logs.seq_id.set(job_id)
        logs.event(log, logging.INFO, "job detached", status=job.status, stage=job.stage, watchers=job.watchers)
        logs.seq_id.reset(token)
        return job

    def describe(self, job: Job) -> dict:
        job.position = self.queue_position(job) if job.status == "queued" else None
        return job.to_dict()

    def queue_position(self, job: Job) -> int:
        """Queued builds submitted before this one (0: it starts as soon as a slot is free)."""
        return sum(1 for j in self.jobs.values() if j.status == "queued" and j.order < job.order)

    def cancel(self, job_id: str, reason: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or not job.active or job.task is None:
            return False
        job.cancel_reason = reason
        job.task.cancel()
        return True

    # ------------------------------------------------------------ internals
    async def _run(self, job: Job) -> None:
        logs.seq_id.set(job.id)  # this task's own context: every line of the build carries the id
        t_submit = time.perf_counter()
        try:
            async with self._sem:
                job.status = "running"
                job.started = time.time()
                waited = time.perf_counter() - t_submit
                logs.event(log, logging.WARNING if waited > 30 else logging.INFO, "job started", queued_s=waited)
                cm = asyncio.timeout(self.build_timeout or None)
                try:
                    async with cm:
                        await self._build(job.params, self.result_dir(job.id), progress=job.update)
                except TimeoutError:
                    if cm.expired():  # our deadline, not a network timeout inside the build
                        raise _BuildTimeout from None
                    raise
                job.status, job.stage = "done", "done"
                logs.event(log, logging.INFO, "job done", s=time.time() - job.started, queued_s=waited)
        except _BuildTimeout:
            logs.event(log, logging.WARNING, "job timed out", s=self.build_timeout, stage=job.stage,
                       done=job.done, total=job.total)
            job.status = "error"
            job.error = (f"The build took longer than {self.build_timeout / 60:.0f} min and was stopped; "
                         "the archive may be overloaded. Try again later.")
        except asyncio.CancelledError:
            logs.event(log, logging.INFO, "job cancelled", reason=job.cancel_reason, stage=job.stage,
                       done=job.done, total=job.total, s=time.perf_counter() - t_submit)
            job.status, job.error = "cancelled", job.cancel_reason or "cancelled"
            if job.cancel_reason is None:  # not ours (e.g. server shutdown): let it propagate
                raise
        except Exception as e:  # noqa: BLE001 - report every failure to the client
            log.exception("job failed stage=%s: %s", job.stage, e)
            job.status, job.error = "error", str(e) or e.__class__.__name__
        finally:
            job.finished = time.time()
            job.task = None

    def _ensure_reaper(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = loop.create_task(self._reap())

    async def _reap(self) -> None:
        while any(j.active for j in self.jobs.values()):
            await asyncio.sleep(self.reap_interval)
            self.reap()

    def reap(self, now: float | None = None) -> list[str]:
        """Cancel builds nobody is waiting for; returns their ids."""
        now = time.monotonic() if now is None else now
        cancelled = []
        for job in list(self.jobs.values()):
            if not job.active:
                continue
            reason = None
            if job.watchers == 0 and job.detached_at is not None and now - job.detached_at >= self.detach_grace:
                reason = "cancelled: every client stopped waiting for it"
            elif self.idle_timeout and now - job.last_seen >= self.idle_timeout:
                reason = f"cancelled: no client asked about it for {self.idle_timeout:.0f} s"
            if reason and self.cancel(job.id, reason):
                cancelled.append(job.id)
        return cancelled


class _BuildTimeout(Exception):
    pass
