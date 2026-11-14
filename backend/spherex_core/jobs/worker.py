"""Worker process for the Redis backend: `python -m spherex_core.jobs.worker`.

Each process runs `jobs_per_worker` build loops that pop the highest-priority job from Redis, skip jobs
nobody is waiting for any more, run the build under runner.run_build (cancellation checks every second
against the job's heartbeat and cancel flag), publish the result to the bucket and record the outcome.
Scale out by adding worker replicas; each replica sizes its process pool from its CPU quota.

On SIGTERM (redeploy, scale-in) the worker stops taking jobs, puts running jobs back at the head of the
queue for another replica, and exits; the cutout cache makes the restart cheap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time

from .. import logs
from ..pipeline.params import SequenceParams
from ..runtime import setup_logging
from .model import ACTIVE, JobCancelled, queue_settings
from .queue import K_CLIENTS, K_IP, K_JOB, K_QUEUE, K_WORKERS, worker_id
from .runner import run_build

log = logging.getLogger("spherex.worker")
PROGRESS_EVERY = 0.5  # s between progress writes


class Worker:
    def __init__(self, redis=None, loops: int | None = None, build=None):
        import redis.asyncio as aioredis

        self.cfg = queue_settings()
        self.r = redis or aioredis.from_url(self.cfg.redis_url, decode_responses=True)
        self.loops = loops or self.cfg.jobs_per_worker
        self.id = worker_id()
        self.stopping = asyncio.Event()
        self.running: dict[str, asyncio.Task] = {}
        self._build = build

    async def run(self) -> None:
        log.info("worker %s up: %d build loops, queue %s", self.id, self.loops, self.cfg.redis_url.split("@")[-1])
        beat = asyncio.create_task(self._heartbeat())
        await asyncio.gather(*(self._loop(i) for i in range(self.loops)))
        beat.cancel()
        await self.r.hdel(K_WORKERS, self.id)

    async def _heartbeat(self) -> None:
        while True:
            now = time.time()
            await self.r.hset(K_WORKERS, self.id, now)
            for job_id in list(self.running):
                await self.r.hset(K_JOB.format(job_id), "wseen", now)
            await asyncio.sleep(10)

    async def _loop(self, n: int) -> None:
        while not self.stopping.is_set():
            item = await self.r.bzpopmin(K_QUEUE, timeout=2)
            if not item:
                continue
            _, job_id, _score = item
            task = asyncio.create_task(self._handle(job_id))
            self.running[job_id] = task
            try:
                await task
            except asyncio.CancelledError:
                if not self.stopping.is_set():
                    raise
            finally:
                self.running.pop(job_id, None)

    async def _reason(self, job_id: str) -> str | None:
        h = await self.r.hmget(K_JOB.format(job_id), "cancel", "pinned", "seen")
        cancel, pinned, seen = h
        if cancel:
            return cancel
        if pinned != "1" and seen and time.time() - float(seen) > self.cfg.interest_timeout:
            return f"no client polled for {self.cfg.interest_timeout:.0f} s"
        return None

    async def _handle(self, job_id: str) -> None:
        logs.seq_id.set(job_id)  # this task's context: every line of the build carries the id
        jk = K_JOB.format(job_id)
        h = await self.r.hgetall(jk)
        if h.get("status") != "queued":
            return
        reason = await self._reason(job_id)
        if reason:
            await self._finish(job_id, h, "cancelled", reason)
            return
        params = SequenceParams(**{k: (tuple(v) if k == "collections" and v else v)
                                   for k, v in json.loads(h["params"]).items()})
        t_start = time.time()
        await self.r.hset(jk, mapping={"status": "running", "stage": "search", "worker": self.id,
                                       "wseen": t_start})
        logs.event(log, logging.INFO, "job started", worker=self.id,
                   queue_wait=t_start - float(h.get("started") or t_start), running=len(self.running))
        last = [0.0]
        pending: set[asyncio.Task] = set()

        def progress(stage: str, done: int, total: int) -> None:
            now = time.monotonic()
            if now - last[0] >= PROGRESS_EVERY or done == total:
                last[0] = now
                t = asyncio.get_running_loop().create_task(
                    self.r.hset(jk, mapping={"stage": stage, "done": done, "total": total}))
                pending.add(t)
                t.add_done_callback(pending.discard)

        try:
            await run_build(job_id, params, progress, lambda: self._reason(job_id), build=self._build)
            await asyncio.gather(*pending, return_exceptions=True)
            await self._finish(job_id, h, "done")
            logs.event(log, logging.INFO, "job done", s=time.time() - t_start, worker=self.id)
        except JobCancelled as e:
            await self._finish(job_id, h, "cancelled", str(e))
            logs.event(log, logging.INFO, "job cancelled", reason=str(e), s=time.time() - t_start)
        except asyncio.CancelledError:
            # shutting down: hand the job to another replica
            await self.r.hset(jk, mapping={"status": "queued", "stage": "queued", "worker": "", "wseen": ""})
            await self.r.zadd(K_QUEUE, {job_id: -1})
            logs.event(log, logging.INFO, "job requeued on shutdown", s=time.time() - t_start)
            raise
        except Exception as e:  # noqa: BLE001 - every failure is reported to the client
            log.exception("job failed s=%.2f", time.time() - t_start)
            await self._finish(job_id, h, "error", str(e) or e.__class__.__name__)

    async def _finish(self, job_id: str, h: dict, status: str, error: str | None = None) -> None:
        mapping = {"status": status, "finished": time.time(), "error": error or ""}
        if status in ("done", "cancelled"):
            mapping["stage"] = status
        async with self.r.pipeline(transaction=True) as p:
            p.hset(K_JOB.format(job_id), mapping=mapping)
            if h.get("ip"):
                p.srem(K_IP.format(h["ip"]), job_id)
            p.delete(K_CLIENTS.format(job_id))
            await p.execute()

    def stop(self) -> None:
        log.info("worker %s stopping: requeueing %d running builds", self.id, len(self.running))
        self.stopping.set()
        for t in self.running.values():
            t.cancel()


async def amain() -> None:
    setup_logging()
    w = Worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, w.stop)
    await w.run()


if __name__ == "__main__":
    asyncio.run(amain())

__all__ = ["ACTIVE", "Worker", "amain"]
