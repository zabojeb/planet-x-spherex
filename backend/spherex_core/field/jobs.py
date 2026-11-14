"""Field-analysis jobs: deduplicated, cancellable, results kept next to the sequence.

The sequence queue (spherex_core.jobs) is typed to SequenceParams and runs builds on workers; a field analysis
reads one finished sequence and runs in the API process, so it uses this small runner with the same rules:
one job per (sequence, options) key, every status poll is a heartbeat, DELETE withdraws a client, and a job
nobody has polled for INTEREST_TIMEOUT seconds (or that every client withdrew from) is cancelled and answers
'cancelled' (HTTP 410). The analysis runs in a thread; cancellation is checked between sources and steps.
Results are written atomically to <sequence>/field/<key>.json, so every uvicorn worker on the same disk serves
them once written.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import cache
from ..analysis.common import load_context
from ..pipeline.results import clean_json
from .analyze import FIELD_VERSION, FieldOptions, analyze_field
from .common import FieldCancelled

log = logging.getLogger(__name__)
INTEREST_TIMEOUT = 60.0
MAX_CONCURRENT = 2


def options_key(opts: FieldOptions) -> str:
    raw = json.dumps({"o": opts.to_dict(), "v": FIELD_VERSION}, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def job_id(seq_id: str, opts: FieldOptions) -> str:
    return f"{seq_id}-{options_key(opts)}"


def split_id(jid: str) -> tuple[str, str]:
    seq, _, key = jid.partition("-")
    if not (seq.isalnum() and key.isalnum()):
        raise ValueError("bad field job id")
    return seq, key


def result_path(jid: str) -> Path:
    seq, key = split_id(jid)
    return cache.layout().sequence(seq) / "field" / f"{key}.json"


@dataclass
class FieldJob:
    id: str
    status: str = "queued"  # queued | running | done | error | cancelled
    stage: str = "queued"
    done: int = 0
    total: int = 0
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    clients: set[str] = field(default_factory=set)
    last_seen: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "stage": self.stage, "done": self.done, "total": self.total,
                "error": self.error or self.reason, "elapsed": (self.finished or time.time()) - self.started}


class FieldJobs:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT, interest_timeout: float = INTEREST_TIMEOUT):
        self.jobs: dict[str, FieldJob] = {}
        self.interest_timeout = interest_timeout
        self._sem: asyncio.Semaphore | None = None
        self._max = max_concurrent
        self._tasks: set[asyncio.Task] = set()

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max)
        return self._sem

    async def submit(self, seq_id: str, opts: FieldOptions, client: str | None = None, force: bool = False) -> dict:
        load_context(seq_id)  # NotFound for unknown or unfinished sequences
        jid = job_id(seq_id, opts)
        if not force and result_path(jid).exists():
            return {"id": jid, "status": "done", "stage": "done", "done": 0, "total": 0, "error": None,
                    "elapsed": 0.0}
        job = self.jobs.get(jid)
        if job and job.status in ("queued", "running"):
            self._touch(job, client)
            return job.to_dict()
        job = FieldJob(id=jid)
        self._touch(job, client)
        self.jobs[jid] = job
        task = asyncio.get_running_loop().create_task(self._run(job, seq_id, opts))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job.to_dict()

    def _touch(self, job: FieldJob, client: str | None) -> None:
        job.last_seen = time.monotonic()
        if client:
            job.clients.add(client)

    async def status(self, jid: str, client: str | None = None) -> tuple[str, dict | None]:
        """('done', None) when the result file exists, else (status, job dict) or ('unknown', None)."""
        if result_path(jid).exists():
            return "done", None
        job = self.jobs.get(jid)
        if job is None:
            return "unknown", None
        if job.status in ("queued", "running"):
            self._touch(job, client)
        return job.status, job.to_dict()

    async def withdraw(self, jid: str, client: str | None = None) -> dict | None:
        job = self.jobs.get(jid)
        if job is None:
            return None
        if client:
            job.clients.discard(client)
        if not job.clients and job.status in ("queued", "running"):
            self._cancel(job, "withdrawn by every client")
        return job.to_dict()

    def _cancel(self, job: FieldJob, reason: str) -> None:
        job.reason = reason
        job.cancel.set()

    async def _watch(self, job: FieldJob) -> None:
        while job.status in ("queued", "running"):
            await asyncio.sleep(2.0)
            if time.monotonic() - job.last_seen > self.interest_timeout:
                self._cancel(job, f"nobody polled for {self.interest_timeout:.0f} s")
                return

    async def _run(self, job: FieldJob, seq_id: str, opts: FieldOptions) -> None:
        watcher = asyncio.get_running_loop().create_task(self._watch(job))
        try:
            async with self._semaphore():
                if job.cancel.is_set():
                    raise FieldCancelled(job.reason)
                job.status = "running"

                def progress(stage: str, done: int, total: int) -> None:
                    job.stage, job.done, job.total = stage, done, total

                def check() -> None:
                    if job.cancel.is_set():
                        raise FieldCancelled(job.reason or "cancelled")

                ctx = await asyncio.to_thread(load_context, seq_id)
                result = await asyncio.to_thread(analyze_field, ctx, opts, progress, check)
                result["id"] = job.id
                path = result_path(job.id)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".json.part")
                tmp.write_text(json.dumps(clean_json(result)))
                tmp.replace(path)
                job.status, job.stage = "done", "done"
        except FieldCancelled as e:
            job.status, job.reason = "cancelled", str(e) or job.reason
            log.info("field %s cancelled: %s", job.id, job.reason)
        except Exception as e:  # noqa: BLE001 - reported to the client
            log.exception("field %s failed", job.id)
            job.status, job.error = "error", str(e) or e.__class__.__name__
        finally:
            job.finished = time.time()
            watcher.cancel()


_jobs: FieldJobs | None = None


def field_jobs() -> FieldJobs:
    global _jobs
    if _jobs is None:
        _jobs = FieldJobs()
    return _jobs


def reset() -> None:
    global _jobs
    _jobs = None


def read_result(jid: str) -> bytes:
    return result_path(jid).read_bytes()
