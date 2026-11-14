"""Operator logs (spherex_core.logs, by Max0072): the sequence id on every line of a build, timings, quiet polls."""

import asyncio
import logging

import pytest

from spherex_core import cache, logs
from spherex_core.jobs import model
from spherex_core.jobs.queue import InProcessQueue
from spherex_core.pipeline import results
from spherex_core.pipeline.params import SequenceParams
from test_results import make_result


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.addFilter(logs._SeqFilter())
        self.lines: list[tuple[str, str, str]] = []

    def emit(self, record):
        self.lines.append((record.levelname, record.seq, record.getMessage()))


@pytest.fixture
def h():
    cap = Capture()
    root = logging.getLogger()
    old = root.level
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    yield cap
    root.removeHandler(cap)
    root.setLevel(old)


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path):
    cache.set_root(tmp_path)
    model.queue_settings.cache_clear()
    yield
    model.queue_settings.cache_clear()
    cache.set_root(None)


def test_timed_and_summary(h):
    log = logging.getLogger("spherex_core.test")
    with logs.timed(log, "step", slow=1e9, frames=3) as info:
        info["rows"] = 7
    with pytest.raises(ValueError):
        with logs.timed(log, "broken"):
            raise ValueError("boom")
    msgs = [m for _, _, m in h.lines]
    assert any(m.startswith("step done frames=3 rows=7 s=") for m in msgs)
    assert any(m.startswith("broken failed s=") and "ValueError: boom" in m for m in msgs)
    assert logs.summary([3.0, 1.0, 2.0, None]) == {"n": 3, "p50": 2.0, "p90": 3.0, "max": 3.0, "sum": 6.0}


def test_build_lines_carry_the_sequence_id(h):
    log = logging.getLogger("spherex_core.pipeline.sequence")

    async def build(p, out_dir, progress):
        log.info("inside the build")
        await asyncio.to_thread(log.info, "in a worker thread")
        meta, arrays = make_result()
        results.write_result(out_dir, meta, arrays)

    async def run():
        q = InProcessQueue(build=build)
        job = await q.submit(SequenceParams(ra=1.0, dec=2.0), client="c1")
        await asyncio.gather(*q._tasks)
        return job["id"]

    seq = asyncio.run(run())
    tagged = {m for _, s, m in h.lines if s == seq}
    assert {"inside the build", "in a worker thread"} <= tagged
    assert any(m.startswith("job submitted") for m in tagged)
    assert any(m.startswith("job started queue_wait=") for m in tagged)
    assert any(m.startswith("job done s=") for m in tagged)


def test_http_requests_logged_and_polls_quiet(h, tmp_path):
    from fastapi.testclient import TestClient

    from spherex_api.app import create_app
    from spherex_core import services
    from spherex_core.jobs.model import Job

    services.configure(cache_root=tmp_path)
    try:
        c = TestClient(create_app(web_dist=None))
        logs.configure("DEBUG")
        services.jobs().jobs["q1"] = Job(id="q1", params=SequenceParams(ra=1.0, dec=2.0))
        c.get("/api/health")
        c.get("/api/sequences/q1")
        lines = [(lvl, s, m) for lvl, s, m in h.lines if m.startswith("GET ")]
        assert lines[0][0] == "INFO" and lines[0][2].startswith("GET /api/health status=200 s=")
        assert lines[1][:2] == ("DEBUG", "q1") and "status=202" in lines[1][2]
    finally:
        logs.configure("INFO")
        services.configure(None)


def test_broken_pool_is_replaced():
    from concurrent.futures.process import BrokenProcessPool

    from spherex_core.pipeline import sequence

    class Broken:
        _broken = "a worker died"

        def shutdown(self, wait=True, cancel_futures=False):
            pass

    sequence._POOL = Broken()
    try:
        pool = sequence.cpu_pool()
        assert not isinstance(pool, Broken)
        assert asyncio.run(sequence.run_in_pool(pow, 2, 10)) == 1024
        import os
        assert os.environ.get("OPENBLAS_NUM_THREADS") == "1"

        calls = []

        async def flaky():
            loop = asyncio.get_running_loop()
            orig = loop.run_in_executor

            def fake(executor, fn, *args):
                calls.append(1)
                if len(calls) == 1:
                    fut = loop.create_future()
                    fut.set_exception(BrokenProcessPool("died"))
                    return fut
                return orig(executor, fn, *args)

            loop.run_in_executor = fake
            return await sequence.run_in_pool(pow, 3, 2)

        assert asyncio.run(flaky()) == 9 and len(calls) == 2
    finally:
        sequence.reset_pool()
