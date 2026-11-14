"""Job record, queue settings and the errors the HTTP layer maps to status codes."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from functools import lru_cache

from ..pipeline.params import SequenceParams

PRIORITY_INTERACTIVE = 0
PRIORITY_PREWARM = 10
ACTIVE = ("queued", "running")


def _env(name: str, default: str) -> str:
    return os.environ.get(f"SPHEREX_{name}", default)


@dataclass(frozen=True)
class QueueSettings:
    backend: str  # "inprocess" | "redis"
    redis_url: str
    interest_timeout: float  # s without a heartbeat before an unpinned job is cancelled
    max_active_per_ip: int  # queued + running builds one client IP may hold
    max_queue: int  # queued builds before new ones get 429
    max_runtime: float  # s per build
    jobs_per_worker: int  # concurrent builds per worker process (each uses the shared process pool)
    max_frames: int  # refuse sequences with more exposures than this (0 = no cap)
    post_rate_per_min: int  # sequence submissions per IP per minute


@lru_cache(maxsize=1)
def queue_settings() -> QueueSettings:
    redis_url = os.environ.get("REDIS_URL") or _env("REDIS_URL", "")
    backend = _env("QUEUE", "redis" if redis_url else "inprocess")
    return QueueSettings(
        backend=backend, redis_url=redis_url or "redis://localhost:6379/0",
        interest_timeout=float(_env("INTEREST_TIMEOUT", "30")),
        max_active_per_ip=int(_env("MAX_ACTIVE_PER_IP", "3")),
        max_queue=int(_env("MAX_QUEUE", "40")),
        max_runtime=float(_env("MAX_RUNTIME", "900")),
        jobs_per_worker=int(_env("JOBS_PER_WORKER", "2")),
        max_frames=int(_env("MAX_FRAMES", "4000")),
        post_rate_per_min=int(_env("POST_RATE_PER_MIN", "30")),
    )


class QueueFull(Exception):
    """Too many builds are waiting; retry later."""

    def __init__(self, message: str = "The build queue is full, try again shortly.", retry_after: int = 30):
        super().__init__(message)
        self.retry_after = retry_after


class TooManyJobs(QueueFull):
    """This client already has its share of active builds (or submits too fast)."""

    def __init__(self, message: str = "You already have several builds running; wait for one to finish.",
                 retry_after: int = 15):
        super().__init__(message, retry_after)


class JobCancelled(Exception):
    """Raised inside a build when nobody needs its result any more."""


@dataclass
class Job:
    """In-process job record; its to_dict() is the job JSON of the HTTP API (202/500 bodies)."""

    id: str
    params: SequenceParams
    status: str = "queued"  # queued | running | done | error | cancelled
    stage: str = "queued"
    done: int = 0
    total: int = 0
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    priority: int = PRIORITY_INTERACTIVE
    pinned: bool = False
    ip: str | None = None
    clients: set[str] = field(default_factory=set)
    last_seen: float = field(default_factory=time.monotonic)
    cancel_reason: str | None = None

    def update(self, stage: str, done: int, total: int) -> None:
        self.stage, self.done, self.total = stage, done, total

    def touch(self, client: str | None) -> None:
        self.last_seen = time.monotonic()
        if client:
            self.clients.add(client)

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "stage": self.stage, "done": self.done, "total": self.total,
                "error": self.error, "elapsed": (self.finished or time.time()) - self.started}
