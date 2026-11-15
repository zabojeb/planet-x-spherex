"""Queue backends with one async interface: submit / status / withdraw / stats / allow_submit.

Clients are identified twice: `ip` (from the proxy headers) for limits, `client` (X-Client-Id, one per
browser tab; falls back to the IP) for interest, so closing one tab never cancels a build another tab
of the same household is waiting for.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import logging
import os
import socket
import time
from dataclasses import asdict
from typing import Protocol

from .. import logs
from ..pipeline.params import SequenceParams
from ..store import store
from .model import (ACTIVE, PRIORITY_INTERACTIVE, Job, JobCancelled, QueueFull, TooManyJobs, queue_settings)
from .runner import BuildFn, run_build

log = logging.getLogger(__name__)


class JobQueue(Protocol):
    async def submit(self, params: SequenceParams, *, force: bool = False, ip: str | None = None,
                     client: str | None = None, priority: int = PRIORITY_INTERACTIVE,
                     pinned: bool = False) -> dict: ...

    async def status(self, job_id: str, *, client: str | None = None, heartbeat: bool = True) -> dict | None: ...

    async def withdraw(self, job_id: str, *, client: str | None = None) -> dict | None: ...

    async def stats(self) -> dict: ...

    async def allow_submit(self, ip: str | None) -> bool: ...

    async def ping(self) -> bool: ...


def done_dict(job_id: str) -> dict:
    return {"id": job_id, "status": "done", "stage": "done", "done": 0, "total": 0, "error": None, "elapsed": 0.0}


def _result_ready(job_id: str) -> bool:
    return store().ensure_local(job_id)


# ====================================================================== in-process
class InProcessQueue:
    """Jobs as asyncio tasks of the API process. Single process only (uvicorn --workers 1)."""

    def __init__(self, max_concurrent: int | None = None, build: BuildFn | None = None):
        cfg = queue_settings()
        self.cfg = cfg
        self.jobs: dict[str, Job] = {}
        self.max_concurrent = max_concurrent or cfg.jobs_per_worker
        self._build = build
        self._heap: list[tuple[int, int, str]] = []
        self._seq = itertools.count()
        self._running = 0
        self._tasks: set[asyncio.Task] = set()
        self._posts: dict[str, list[float]] = {}

    # -- interface
    async def submit(self, params, *, force=False, ip=None, client=None, priority=PRIORITY_INTERACTIVE,
                     pinned=False) -> dict:
        key = params.key()
        if not force and await asyncio.to_thread(_result_ready, key):
            job = self.jobs.get(key)
            if job is None or job.status != "done":
                job = Job(id=key, params=params, status="done", stage="done", finished=time.time())
                self.jobs[key] = job
            return job.to_dict()
        job = self.jobs.get(key)
        if job and job.status in ACTIVE:
            job.touch(client or ip)
            job.pinned = job.pinned or pinned
            job.priority = min(job.priority, priority)
            return job.to_dict()
        if ip and not pinned:
            active = sum(1 for j in self.jobs.values() if j.status in ACTIVE and j.ip == ip)
            if active >= self.cfg.max_active_per_ip:
                raise TooManyJobs()
        if not pinned and sum(1 for j in self.jobs.values() if j.status == "queued") >= self.cfg.max_queue:
            raise QueueFull()
        job = Job(id=key, params=params, priority=priority, pinned=pinned, ip=ip)
        job.touch(client or ip)
        self.jobs[key] = job
        _log_submitted(key, params, priority, pinned)
        heapq.heappush(self._heap, (priority, next(self._seq), key))
        self._kick()
        return job.to_dict()

    async def status(self, job_id, *, client=None, heartbeat=True) -> dict | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if heartbeat and job.status in ACTIVE:
            job.touch(client)
        d = job.to_dict()
        if job.status == "queued":  # builds that start before this one (wording from Max0072's queue)
            mine = next((e for e in self._heap if e[2] == job_id), None)
            d["position"] = sum(1 for e in self._heap if mine is not None and e < mine)
        return d

    async def withdraw(self, job_id, *, client=None) -> dict | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if client:
            job.clients.discard(client)
        if job.status in ACTIVE and not job.clients and not job.pinned:
            job.cancel_reason = "every client left"
            if job.status == "queued":
                self._finish(job, "cancelled", "every client left")
        return job.to_dict()

    async def stats(self) -> dict:
        counts: dict[str, int] = {}
        for j in self.jobs.values():
            counts[j.status] = counts.get(j.status, 0) + 1
        return {"backend": "inprocess", "running": self._running, "max_concurrent": self.max_concurrent,
                "jobs": counts}

    async def allow_submit(self, ip) -> bool:
        if not ip:
            return True
        now = time.monotonic()
        hits = [t for t in self._posts.get(ip, []) if now - t < 60]
        hits.append(now)
        self._posts[ip] = hits
        return len(hits) <= self.cfg.post_rate_per_min

    async def ping(self) -> bool:
        return True

    # -- internals
    def _stale(self, job: Job) -> str | None:
        if job.cancel_reason:
            return job.cancel_reason
        if not job.pinned and time.monotonic() - job.last_seen > self.cfg.interest_timeout:
            return f"no client polled for {self.cfg.interest_timeout:.0f} s"
        return None

    def _kick(self) -> None:
        while self._running < self.max_concurrent and self._heap:
            _, _, key = heapq.heappop(self._heap)
            job = self.jobs.get(key)
            if job is None or job.status != "queued":
                continue
            reason = self._stale(job)
            if reason:
                self._finish(job, "cancelled", reason)
                continue
            self._running += 1
            task = asyncio.get_running_loop().create_task(self._run(job))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, job: Job) -> None:
        logs.seq_id.set(job.id)  # this task's context: every line of the build carries the id
        job.status, job.stage = "running", "search"
        t_start = time.time()
        logs.event(log, logging.INFO, "job started", queue_wait=t_start - job.started, running=self._running,
                   queued=len(self._heap))

        async def should_cancel() -> str | None:
            return self._stale(job)

        try:
            await run_build(job.id, job.params, job.update, should_cancel, build=self._build)
            self._finish(job, "done")
            logs.event(log, logging.INFO, "job done", s=time.time() - t_start)
        except JobCancelled as e:
            self._finish(job, "cancelled", str(e))
        except Exception as e:  # noqa: BLE001 - report every failure to the client
            log.exception("job failed s=%.2f", time.time() - t_start)
            self._finish(job, "error", str(e) or e.__class__.__name__)
        finally:
            self._running -= 1
            self._kick()

    def _finish(self, job: Job, status: str, error: str | None = None) -> None:
        job.status, job.finished = status, time.time()
        job.stage = status if status in ("done", "cancelled") else job.stage
        job.error = error if status != "done" else None
        if status == "cancelled":
            token = logs.seq_id.set(job.id)
            logs.event(log, logging.INFO, "job cancelled", reason=error, s=job.finished - job.started)
            logs.seq_id.reset(token)


# ====================================================================== Redis
K_QUEUE = "sx:queue"  # zset job id -> priority * 1e10 + enqueue time
K_JOB = "sx:job:{}"  # hash: status stage done total error started finished priority pinned ip seen cancel params worker wseen
K_CLIENTS = "sx:job:{}:clients"  # set of client ids still interested
K_IP = "sx:ip:{}"  # set of active job ids of one IP
K_RATE = "sx:rate:{}:{}"  # per-IP submissions in one minute
K_WORKERS = "sx:workers"  # hash worker id -> last heartbeat
JOB_TTL = 24 * 3600
WORKER_LOST_AFTER = 90.0


def _now() -> float:
    return time.time()


def job_from_hash(job_id: str, h: dict) -> dict:
    started = float(h.get("started") or _now())
    finished = float(h["finished"]) if h.get("finished") else None
    return {"id": job_id, "status": h.get("status", "queued"), "stage": h.get("stage", "queued"),
            "done": int(h.get("done") or 0), "total": int(h.get("total") or 0), "error": h.get("error") or None,
            "position": None, "preview": h.get("preview") or None, "elapsed": (finished or _now()) - started}


class RedisQueue:
    """API side of the Redis backend. Builds run in `spherex_core.jobs.worker` processes."""

    def __init__(self, url: str | None = None, redis=None):
        import redis.asyncio as aioredis

        self.cfg = queue_settings()
        self.r = redis or aioredis.from_url(url or self.cfg.redis_url, decode_responses=True)

    async def submit(self, params, *, force=False, ip=None, client=None, priority=PRIORITY_INTERACTIVE,
                     pinned=False) -> dict:
        key = params.key()
        if not force and await asyncio.to_thread(_result_ready, key):
            return done_dict(key)
        jk = K_JOB.format(key)
        h = await self.r.hgetall(jk)
        if h.get("status") in ACTIVE and not self._worker_lost(h):
            await self._touch(key, client or ip)
            if pinned:
                await self.r.hset(jk, "pinned", "1")
            return job_from_hash(key, h)
        if ip and not pinned:
            active = [j for j in await self.r.smembers(K_IP.format(ip))
                      if (await self.r.hget(K_JOB.format(j), "status")) in ACTIVE]
            if len(active) >= self.cfg.max_active_per_ip:
                raise TooManyJobs()
        if not pinned and await self.r.zcard(K_QUEUE) >= self.cfg.max_queue:
            raise QueueFull()
        now = _now()
        mapping = {"status": "queued", "stage": "queued", "done": 0, "total": 0, "error": "", "started": now,
                   "finished": "", "priority": priority, "pinned": int(pinned), "ip": ip or "", "seen": now,
                   "cancel": "", "params": json.dumps(asdict(params)), "worker": "", "wseen": ""}
        async with self.r.pipeline(transaction=True) as p:
            p.delete(jk, K_CLIENTS.format(key))
            p.hset(jk, mapping=mapping)
            p.expire(jk, JOB_TTL)
            if client or ip:
                p.sadd(K_CLIENTS.format(key), client or ip)
                p.expire(K_CLIENTS.format(key), JOB_TTL)
            if ip:
                p.sadd(K_IP.format(ip), key)
                p.expire(K_IP.format(ip), JOB_TTL)
            p.zadd(K_QUEUE, {key: priority * 1e10 + now})
            await p.execute()
        _log_submitted(key, params, priority, pinned)
        return job_from_hash(key, {k: str(v) for k, v in mapping.items()})

    async def status(self, job_id, *, client=None, heartbeat=True) -> dict | None:
        h = await self.r.hgetall(K_JOB.format(job_id))
        if not h:
            return None
        if h.get("status") in ACTIVE and self._worker_lost(h):
            await self.r.hset(K_JOB.format(job_id), mapping={"status": "error", "finished": _now(),
                                                             "error": "The worker running this build stopped; "
                                                                      "request it again."})
            h = await self.r.hgetall(K_JOB.format(job_id))
        elif heartbeat and h.get("status") in ACTIVE:
            await self._touch(job_id, client)
        d = job_from_hash(job_id, h)
        if d["status"] == "queued":
            rank = await self.r.zrank(K_QUEUE, job_id)
            d["position"] = int(rank) if rank is not None else None
        return d

    async def withdraw(self, job_id, *, client=None) -> dict | None:
        jk = K_JOB.format(job_id)
        h = await self.r.hgetall(jk)
        if not h:
            return None
        if client:
            await self.r.srem(K_CLIENTS.format(job_id), client)
        if h.get("status") in ACTIVE and h.get("pinned") != "1" and not await self.r.scard(K_CLIENTS.format(job_id)):
            await self.r.hset(jk, "cancel", "every client left")
            if h.get("status") == "queued" and await self.r.zrem(K_QUEUE, job_id):
                await self.r.hset(jk, mapping={"status": "cancelled", "stage": "cancelled", "finished": _now(),
                                               "error": "every client left"})
        return job_from_hash(job_id, await self.r.hgetall(jk))

    async def stats(self) -> dict:
        workers = await self.r.hgetall(K_WORKERS)
        alive = {w: float(t) for w, t in workers.items() if _now() - float(t) < WORKER_LOST_AFTER}
        return {"backend": "redis", "queued": await self.r.zcard(K_QUEUE), "workers": len(alive)}

    async def allow_submit(self, ip) -> bool:
        if not ip:
            return True
        k = K_RATE.format(ip, int(_now() // 60))
        n = await self.r.incr(k)
        if n == 1:
            await self.r.expire(k, 70)
        return n <= self.cfg.post_rate_per_min

    async def ping(self) -> bool:
        return bool(await self.r.ping())

    async def _touch(self, job_id: str, client: str | None) -> None:
        async with self.r.pipeline(transaction=False) as p:
            p.hset(K_JOB.format(job_id), "seen", _now())
            if client:
                p.sadd(K_CLIENTS.format(job_id), client)
            await p.execute()

    @staticmethod
    def _worker_lost(h: dict) -> bool:
        return h.get("status") == "running" and bool(h.get("wseen")) and _now() - float(h["wseen"]) > WORKER_LOST_AFTER


def _log_submitted(key: str, params: SequenceParams, priority: int, pinned: bool) -> None:
    token = logs.seq_id.set(key)
    logs.event(log, logging.INFO, "job submitted", target=params.name or params.object_name,
               fov_arcmin=params.fov_arcmin, priority=priority, pinned=pinned or None)
    logs.seq_id.reset(token)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def make_queue() -> JobQueue:
    cfg = queue_settings()
    if cfg.backend == "redis":
        return RedisQueue(cfg.redis_url)
    return InProcessQueue()


__all__ = ["InProcessQueue", "JobQueue", "RedisQueue", "done_dict", "job_from_hash", "make_queue", "worker_id"]
