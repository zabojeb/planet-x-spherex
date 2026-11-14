"""Operator logs: the sequence id on every line of a build, timings, and quiet progress polls."""

import asyncio
import logging

from spherex_core import logs
from spherex_core.pipeline.jobs import JobManager
from spherex_core.pipeline.params import SequenceParams


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.addFilter(logs._SeqFilter())
        self.lines: list[tuple[str, str, str]] = []

    def emit(self, record):
        self.lines.append((record.levelname, record.seq, record.getMessage()))


def capture() -> Capture:
    h = Capture()
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    return h


def test_timed_and_summary():
    h = capture()
    log = logging.getLogger("spherex_core.test")
    with logs.timed(log, "step", slow=1e9, frames=3) as info:
        info["rows"] = 7
    try:
        with logs.timed(log, "broken"):
            raise ValueError("boom")
    except ValueError:
        pass
    msgs = [m for _, _, m in h.lines]
    assert any(m.startswith("step done frames=3 rows=7 s=") for m in msgs)
    assert any(m.startswith("broken failed s=") and "ValueError: boom" in m for m in msgs)
    assert logs.summary([3.0, 1.0, 2.0, None]) == {"n": 3, "p50": 2.0, "p90": 3.0, "max": 3.0, "sum": 6.0}
    logging.getLogger().removeHandler(h)


def test_build_lines_carry_the_sequence_id(tmp_path):
    h = capture()
    log = logging.getLogger("spherex_core.pipeline.sequence")

    async def build(p, out_dir, progress):
        log.info("inside the build")
        await asyncio.to_thread(log.info, "in a worker thread")
        return {}

    async def run():
        m = JobManager(tmp_path, max_concurrent=1, build_timeout=0, idle_timeout=0, build=build)
        job = m.submit(SequenceParams(ra=1.0, dec=2.0))
        await asyncio.gather(*m._tasks)
        return job.id

    seq = asyncio.run(run())
    tagged = {m for _, s, m in h.lines if s == seq}
    assert {"inside the build", "in a worker thread"} <= tagged
    assert any(m.startswith("job submitted") for m in tagged) and any(m.startswith("job started") for m in tagged)
    assert any(m.startswith("job done") for m in tagged)
    logging.getLogger().removeHandler(h)


def test_http_requests_logged_and_polls_quiet(tmp_path):
    from fastapi.testclient import TestClient

    from spherex_api.app import create_app
    from spherex_core import services
    from spherex_core.pipeline.jobs import Job

    services.configure(cache_root=tmp_path)
    try:
        c = TestClient(create_app(web_dist=None))
        logs.configure("DEBUG")
        h = capture()
        services.jobs().jobs["q1"] = Job(id="q1", params=SequenceParams(ra=1.0, dec=2.0))
        c.get("/api/health")
        c.get("/api/sequences/q1")
        lines = [(lvl, s, m) for lvl, s, m in h.lines if m.startswith("GET ")]
        assert lines[0][0] == "INFO" and lines[0][2].startswith("GET /api/health status=200 s=")
        assert lines[1][:2] == ("DEBUG", "q1") and "status=202" in lines[1][2]
        logging.getLogger().removeHandler(h)
    finally:
        logs.configure("INFO")
        services.configure(None)
