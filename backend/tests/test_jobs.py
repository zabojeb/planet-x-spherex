"""Queue semantics for both backends with a fake build: dedupe, heartbeats, cancellation, limits, priority."""

import asyncio
import time

import fakeredis
import pytest

from spherex_core import cache
from spherex_core.jobs import PRIORITY_PREWARM, JobCancelled, QueueFull, TooManyJobs, model
from spherex_core.jobs.queue import InProcessQueue, RedisQueue
from spherex_core.jobs.runner import run_build
from spherex_core.jobs.worker import Worker
from spherex_core.pipeline import results
from spherex_core.pipeline.params import SequenceParams
from test_results import make_result


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    cache.set_root(tmp_path)
    monkeypatch.setenv("SPHEREX_INTEREST_TIMEOUT", "1.5")
    monkeypatch.setenv("SPHEREX_MAX_ACTIVE_PER_IP", "2")
    monkeypatch.setenv("SPHEREX_MAX_QUEUE", "3")
    monkeypatch.setenv("SPHEREX_POST_RATE_PER_MIN", "5")
    model.queue_settings.cache_clear()
    yield
    model.queue_settings.cache_clear()
    cache.set_root(None)


class FakeBuild:
    """Writes a synthetic result after `seconds`, reporting progress; records starts and cancellations."""

    def __init__(self, seconds=0.3):
        self.seconds, self.started, self.cancelled = seconds, [], []

    async def __call__(self, params, out_dir, progress):
        self.started.append(out_dir.name)
        try:
            for i in range(10):
                progress("fetch", i + 1, 10)
                await asyncio.sleep(self.seconds / 10)
        except asyncio.CancelledError:
            self.cancelled.append(out_dir.name)
            raise
        meta, arrays = make_result()
        results.write_result(out_dir, meta, arrays)


def P(i=0):
    return SequenceParams(ra=10.0 + i, dec=5.0)


async def wait_status(q, job_id, want, timeout=6.0, client="c1"):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        st = await q.status(job_id, client=client)
        if st and st["status"] in want:
            return st
        await asyncio.sleep(0.1)
    raise AssertionError(f"{job_id} never reached {want}: {st}")


# ------------------------------------------------------------------ in-process
def test_inprocess_dedupe_and_done():
    async def go():
        b = FakeBuild()
        q = InProcessQueue(build=b)
        a = await q.submit(P(), ip="1.1.1.1", client="tab1")
        c = await q.submit(P(), ip="2.2.2.2", client="tab2")
        assert a["id"] == c["id"]
        await wait_status(q, a["id"], {"done"})
        assert b.started == [a["id"]]  # one build for two clients
        assert (await q.submit(P(), ip="1.1.1.1"))["status"] == "done"  # result reused
    asyncio.run(go())


def test_inprocess_cancels_abandoned_job():
    async def go():
        b = FakeBuild(seconds=10)
        q = InProcessQueue(build=b)
        j = await q.submit(P(), ip="1.1.1.1", client="tab1")
        await asyncio.sleep(3.5)  # nobody polls -> cancelled after ~1.5 s
        st = await q.status(j["id"], heartbeat=False)
        assert st["status"] == "cancelled" and "no client polled" in st["error"]
        assert b.cancelled == [j["id"]]
        assert not (cache.layout().sequence(j["id"]).with_name(j["id"] + ".tmp")).exists()
    asyncio.run(go())


def test_inprocess_heartbeat_keeps_job_and_withdraw_cancels():
    async def go():
        b = FakeBuild(seconds=10)
        q = InProcessQueue(build=b)
        j = await q.submit(P(), ip="1.1.1.1", client="tab1")
        await q.submit(P(), ip="1.1.1.1", client="tab2")
        for _ in range(25):  # 2.5 s of polling > interest timeout
            await q.status(j["id"], client="tab1")
            await asyncio.sleep(0.1)
        assert (await q.status(j["id"], client="tab1"))["status"] == "running"
        await q.withdraw(j["id"], client="tab1")
        assert (await q.status(j["id"], heartbeat=False))["status"] == "running"  # tab2 still interested
        await q.withdraw(j["id"], client="tab2")
        await wait_status(q, j["id"], {"cancelled"})
    asyncio.run(go())


def test_inprocess_limits_and_priority():
    async def go():
        b = FakeBuild(seconds=2)
        q = InProcessQueue(max_concurrent=1, build=b)
        await q.submit(P(0), ip="9.9.9.9", client="x")
        await q.submit(P(1), ip="9.9.9.9", client="x")
        with pytest.raises(TooManyJobs):
            await q.submit(P(2), ip="9.9.9.9", client="x")
        await q.submit(P(3), ip="8.8.8.8", client="y")
        await q.submit(P(6), ip="6.6.6.6", client="w")  # 1 running + 3 queued
        with pytest.raises(QueueFull):
            await q.submit(P(4), ip="7.7.7.7", client="z")
        pinned = await q.submit(P(5), ip="9.9.9.9", pinned=True, priority=PRIORITY_PREWARM)
        assert pinned["status"] == "queued"  # prewarm is exempt from the per-IP limit
        assert [await q.allow_submit("5.5.5.5") for _ in range(6)] == [True] * 5 + [False]
    asyncio.run(go())


def test_run_build_timeout_cleans_up():
    async def go():
        b = FakeBuild(seconds=10)

        async def never():
            return None

        with pytest.raises(TimeoutError):
            await run_build("abc", P(), lambda *a: None, never, build=b, max_runtime=1.2)
        assert b.cancelled == ["abc"]
    asyncio.run(go())


# ------------------------------------------------------------------ Redis
def test_redis_queue_with_worker_dedupe_progress_cancel():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        q = RedisQueue(redis=r)
        b = FakeBuild(seconds=1.0)
        w = Worker(redis=r, loops=2, build=b)
        wt = asyncio.create_task(w.run())

        a = await q.submit(P(0), ip="1.1.1.1", client="tab1")
        same = await q.submit(P(0), ip="2.2.2.2", client="tab2")
        assert a["id"] == same["id"] and a["status"] == "queued"
        st = await wait_status(q, a["id"], {"running"})
        assert st["status"] == "running"
        done = await wait_status(q, a["id"], {"done"})
        assert done["status"] == "done" and b.started.count(a["id"]) == 1
        assert (await q.submit(P(0), ip="1.1.1.1"))["status"] == "done"

        # abandoned: submitted, never polled again -> cancelled by the worker
        b.seconds = 20
        j = await q.submit(P(1), ip="3.3.3.3", client="tab3")
        await asyncio.sleep(4.0)
        st = await q.status(j["id"], heartbeat=False)
        assert st["status"] == "cancelled", st
        assert j["id"] in b.cancelled

        # explicit withdraw of a queued job removes it from the queue
        w.stop()
        await asyncio.wait_for(wt, 10)
        k = await q.submit(P(2), ip="4.4.4.4", client="tab4")
        await q.withdraw(k["id"], client="tab4")
        assert (await q.status(k["id"], heartbeat=False))["status"] == "cancelled"
        assert await r.zcard("sx:queue") == 0
        stats = await q.stats()
        assert stats["backend"] == "redis"
    asyncio.run(go())


def test_redis_limits():
    async def go():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        q = RedisQueue(redis=r)
        await q.submit(P(0), ip="9.9.9.9")
        await q.submit(P(1), ip="9.9.9.9")
        with pytest.raises(TooManyJobs):
            await q.submit(P(2), ip="9.9.9.9")
        await q.submit(P(3), ip="8.8.8.8")
        with pytest.raises(QueueFull):
            await q.submit(P(4), ip="7.7.7.7")
        assert [await q.allow_submit("5.5.5.5") for _ in range(6)] == [True] * 5 + [False]
    asyncio.run(go())


def test_job_cancelled_is_exception():
    assert issubclass(JobCancelled, Exception)


# ------------------------------------------------------------------ previews (ADR-0039)
class PreviewBuild(FakeBuild):
    """Announces a preview half way, the way build_sequence does."""

    async def __call__(self, params, out_dir, progress):
        progress("fetch", 5, 10)
        progress(f"preview:{out_dir.name}p1", 5, 10)
        await asyncio.sleep(self.seconds)
        progress("fetch", 10, 10)
        meta, arrays = make_result()
        results.write_result(out_dir, meta, arrays)


def test_inprocess_status_carries_the_preview():
    async def go():
        q = InProcessQueue(build=PreviewBuild(seconds=0.6))
        job = await q.submit(P(), client="tab1")
        st = await wait_status(q, job["id"], {"running"})
        for _ in range(30):
            st = await q.status(job["id"], client="tab1")
            if st.get("preview"):
                break
            await asyncio.sleep(0.05)
        assert st["preview"] == job["id"] + "p1" and st["stage"] == "fetch"  # the stage is not replaced
        await wait_status(q, job["id"], {"done"})
    asyncio.run(go())


def test_redis_status_carries_the_preview():
    async def go():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        q = RedisQueue(redis=r)
        w = Worker(redis=r, loops=1, build=PreviewBuild(seconds=0.6))
        task = asyncio.create_task(w.run())
        try:
            job = await q.submit(P(), client="tab1")
            st = None
            for _ in range(60):
                st = await q.status(job["id"], client="tab1")
                if st and st.get("preview"):
                    break
                await asyncio.sleep(0.05)
            assert st["preview"] == job["id"] + "p1"
        finally:
            w.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(go())
