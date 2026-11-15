"""Field-analysis jobs (field analysis, known objects, track & stack search): deduplicated, cancellable,
results kept next to the sequence.

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
from ..analysis.common import full_context, load_context
from ..pipeline.results import clean_json
from .analyze import FIELD_VERSION, FieldOptions, analyze_field
from .common import FieldCancelled

log = logging.getLogger(__name__)
INTEREST_TIMEOUT = 60.0
MAX_CONCURRENT = 2
MAX_POINT_CONCURRENT = 6


KINDS = {"f": "field analysis", "k": "known objects", "s": "track & stack search", "p": "point analysis"}


def options_key(opts: FieldOptions) -> str:
    raw = json.dumps({"o": opts.to_dict(), "v": FIELD_VERSION}, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def make_id(seq_id: str, kind: str, params: dict) -> str:
    """Job id '<sequence>-<kind><hash>': kind f (field), k (known objects), s (stack search)."""
    raw = json.dumps({"p": params, "v": FIELD_VERSION}, sort_keys=True)
    return f"{seq_id}-{kind}{hashlib.sha1(raw.encode()).hexdigest()[:10]}"


def job_id(seq_id: str, opts: FieldOptions) -> str:
    return make_id(seq_id, "f", opts.to_dict())


def split_id(jid: str) -> tuple[str, str]:
    seq, _, key = jid.partition("-")
    if not (seq.isalnum() and key.isalnum() and key[:1] in KINDS):
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
    steps: dict[str, str] | None = None  # per-step state for multi-step jobs (point analysis)

    def to_dict(self) -> dict:
        d = {"id": self.id, "status": self.status, "stage": self.stage, "done": self.done, "total": self.total,
             "error": self.error or self.reason, "elapsed": (self.finished or time.time()) - self.started}
        if self.steps is not None:
            d["steps"] = dict(self.steps)
        return d


class FieldJobs:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT, interest_timeout: float = INTEREST_TIMEOUT):
        self.jobs: dict[str, FieldJob] = {}
        self.interest_timeout = interest_timeout
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._max = max_concurrent
        self._tasks: set[asyncio.Task] = set()

    def _semaphore(self, jid: str = "") -> asyncio.Semaphore:
        """Point analyses (light, interactive) get their own slots, so a minutes-long field analysis never
        makes a click wait in the queue."""
        kind = "p" if jid.partition("-")[2].startswith("p") else "heavy"
        if kind not in self._sems:
            self._sems[kind] = asyncio.Semaphore(MAX_POINT_CONCURRENT if kind == "p" else self._max)
        return self._sems[kind]

    async def submit(self, seq_id: str, opts: FieldOptions, client: str | None = None, force: bool = False) -> dict:
        """Start (or join) the full field analysis of a finished sequence."""
        def work(ctx, progress, check):
            return analyze_field(ctx, opts, progress, check)

        return await self.submit_work(seq_id, job_id(seq_id, opts), work, client, force)

    async def submit_work(self, seq_id: str, jid: str, work, client: str | None = None, force: bool = False) -> dict:
        """Start (or join) `work(ctx, progress, check) -> dict` for a finished sequence under job id `jid`."""
        full_context(seq_id)  # NotFound for unknown or unfinished sequences, and for previews
        if not force and result_path(jid).exists():
            return {"id": jid, "status": "done", "stage": "done", "done": 0, "total": 0, "error": None,
                    "elapsed": 0.0}
        job = self.jobs.get(jid)
        if job and job.status in ("queued", "running"):
            self._touch(job, client)
            return job.to_dict()
        if force:  # status() serves the result file first, so a forced re-run must drop the old one
            result_path(jid).unlink(missing_ok=True)
        job = FieldJob(id=jid)
        self._touch(job, client)
        self.jobs[jid] = job
        task = asyncio.get_running_loop().create_task(self._run(job, seq_id, work))
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

    async def _run(self, job: FieldJob, seq_id: str, work) -> None:
        watcher = asyncio.get_running_loop().create_task(self._watch(job))
        try:
            async with self._semaphore(job.id):
                if job.cancel.is_set():
                    raise FieldCancelled(job.reason)
                job.status = "running"

                def progress(stage: str, done: int, total: int, steps: dict | None = None) -> None:
                    job.stage, job.done, job.total = stage, done, total
                    if steps is not None:
                        job.steps = steps

                def check() -> None:
                    if job.cancel.is_set():
                        raise FieldCancelled(job.reason or "cancelled")

                ctx = await asyncio.to_thread(load_context, seq_id)
                result = await asyncio.to_thread(work, ctx, progress, check)
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
