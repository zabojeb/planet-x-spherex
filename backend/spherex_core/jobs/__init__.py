"""Sequence build jobs: one queue interface, two backends.

    InProcessQueue   asyncio tasks inside the API process, local disk (laptop, tests, single dev server)
    RedisQueue       jobs in Redis, executed by `python -m spherex_core.jobs.worker` replicas (Railway)

Shared semantics (see ADR-0031):
- dedupe: one job per SequenceParams.key(); every POST and status poll from a client is a heartbeat;
- interest: a job nobody has polled for `interest_timeout` seconds, or that every client withdrew from
  (DELETE), is cancelled — unless pinned (prewarm). Cancelling aborts in-flight S3 range requests,
  drops queued process-pool tasks and removes the temporary result directory; a later request starts
  again and reuses the cutout cache;
- limits: per-IP active builds and a global queue cap raise TooManyJobs / QueueFull (HTTP 429);
- priority: interactive builds run before prewarm builds; every build has a maximum runtime.
"""

from .model import (PRIORITY_INTERACTIVE, PRIORITY_PREWARM, Job, JobCancelled, QueueFull, TooManyJobs,
                    queue_settings)
from .queue import JobQueue, make_queue

__all__ = ["PRIORITY_INTERACTIVE", "PRIORITY_PREWARM", "Job", "JobCancelled", "JobQueue", "QueueFull",
           "TooManyJobs", "make_queue", "queue_settings"]
