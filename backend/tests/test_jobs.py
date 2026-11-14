"""JobManager slots, queue positions, timeouts and cancellation, with a fake build (offline, fast)."""

import asyncio
import time

import pytest

from spherex_core.pipeline.jobs import JobManager
from spherex_core.pipeline.params import SequenceParams


def params(i: int) -> SequenceParams:
    return SequenceParams(ra=10.0 + i, dec=2.0)


class FakeBuild:
    """A build that runs until released (or forever), reporting one stage."""

    def __init__(self):
        self.release = asyncio.Event()
        self.started: list[SequenceParams] = []
        self.cancelled = 0

    async def __call__(self, p, out_dir, progress):
        self.started.append(p)
        progress("fetch", 0, 10)
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return {}


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def manager(tmp_path, build, **kw):
    kw = {"max_concurrent": 2, "build_timeout": 0, "idle_timeout": 0, "detach_grace": 0, "reap_interval": 0.01} | kw
    return JobManager(tmp_path, build=build, **kw)


def test_slots_queue_positions_and_dedup(tmp_path):
    async def run():
        b = FakeBuild()
        m = manager(tmp_path, b)
        jobs = [m.submit(params(i)) for i in range(4)]
        await settle()
        assert [j.status for j in jobs] == ["running", "running", "queued", "queued"]
        assert [m.describe(j)["position"] for j in jobs] == [None, None, 0, 1]
        assert m.submit(params(3)) is jobs[3] and jobs[3].watchers == 2  # same build, one more client
        b.release.set()
        await settle()
        await asyncio.gather(*m._tasks)
        assert [j.status for j in jobs] == ["done"] * 4 and len(b.started) == 4
    asyncio.run(run())


def test_detach_frees_the_slot_for_the_next_build(tmp_path):
    async def run():
        b = FakeBuild()
        m = manager(tmp_path, b, max_concurrent=1)
        first, second = m.submit(params(0)), m.submit(params(1))
        await settle()
        assert (first.status, second.status) == ("running", "queued")
        m.detach(first.id)
        await asyncio.sleep(0.05)  # the reaper cancels it after the (zero) grace period
        assert first.status == "cancelled" and "stopped waiting" in first.error and b.cancelled == 1
        assert second.status == "running"
        # submitting again after a cancel starts a new build
        again = m.submit(params(0))
        assert again is not first and again.status == "queued"
        b.release.set()
        await asyncio.gather(*m._tasks)
    asyncio.run(run())


def test_detach_keeps_a_build_another_client_waits_for(tmp_path):
    async def run():
        b = FakeBuild()
        m = manager(tmp_path, b)
        job = m.submit(params(0))
        m.submit(params(0))
        m.detach(job.id)
        await asyncio.sleep(0.05)
        assert job.status == "running" and job.watchers == 1
        b.release.set()
        await asyncio.gather(*m._tasks)
        assert job.status == "done"
    asyncio.run(run())


def test_idle_builds_are_cancelled_and_polling_keeps_them(tmp_path):
    async def run():
        m = manager(tmp_path, FakeBuild(), idle_timeout=60)
        polled, idle = m.submit(params(0)), m.submit(params(1))
        await settle()
        later = time.monotonic() + 61
        polled.last_seen = later  # as if a client polled just now
        assert m.reap(now=later) == [idle.id]
        await settle()
        assert idle.status == "cancelled" and "60 s" in idle.error and polled.status == "running"
        m.cancel(polled.id, "test over")
        await asyncio.gather(*m._tasks)
    asyncio.run(run())


def test_build_timeout_reports_an_error_and_frees_the_slot(tmp_path):
    async def run():
        b = FakeBuild()
        m = manager(tmp_path, b, max_concurrent=1, build_timeout=0.05)
        slow, nxt = m.submit(params(0)), m.submit(params(1))
        await asyncio.sleep(0.1)
        assert slow.status == "error" and "took longer than" in slow.error
        assert nxt.status in ("running", "error")
        b.release.set()
        await asyncio.gather(*m._tasks)
    asyncio.run(run())


def test_a_network_timeout_inside_the_build_is_an_ordinary_error(tmp_path):
    async def build(p, out_dir, progress):
        raise TimeoutError("read timed out")

    async def run():
        m = manager(tmp_path, build, build_timeout=60)
        job = m.submit(params(0))
        await asyncio.gather(*m._tasks)
        assert job.status == "error" and job.error == "read timed out"
    asyncio.run(run())


@pytest.mark.parametrize("status,code", [("queued", 202), ("cancelled", 410)])
def test_api_status_codes_and_detach(tmp_path, status, code):
    from fastapi.testclient import TestClient

    from spherex_api.app import create_app
    from spherex_core import services
    from spherex_core.pipeline.jobs import Job

    services.configure(cache_root=tmp_path)
    try:
        c = TestClient(create_app(web_dist=None))
        services.jobs().jobs["q1"] = Job(id="q1", params=params(0), status=status, watchers=1)
        r = c.get("/api/sequences/q1")
        assert r.status_code == code and "position" in r.json()
        assert c.delete("/api/sequences/q1").status_code == 204
        assert services.jobs().jobs["q1"].watchers == (0 if status == "queued" else 1)
        assert c.delete("/api/sequences/nope").status_code == 404
    finally:
        services.configure(None)
