# ADR-0033: Job queue with dedupe, heartbeats, cancellation, priorities and limits

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Users switch targets quickly and close tabs; a build nobody waits for still costs minutes of CPU and gigabytes of S3 reads. Many users may also ask for the same target at once, and a single address could flood the queue.

## Decision

`spherex_core.jobs` exposes one async interface (`submit`, `status`, `withdraw`, `stats`, `allow_submit`) with two backends, `InProcessQueue` and `RedisQueue` + `worker.py`, sharing these rules:

- **Dedupe:** one job per `SequenceParams.key()`; a second request joins the running job. Finished results are reused (local disk, then bucket).
- **Interest:** every POST and every status poll is a heartbeat from a client (`X-Client-Id` header per browser tab, falling back to the IP). `DELETE /api/sequences/{id}` withdraws that client. A job is cancelled when every client withdrew, or when nobody polled for `SPHEREX_INTEREST_TIMEOUT` (30 s). Prewarm jobs are `pinned` and never cancelled for lack of interest.
- **Cancelling** a running build (`jobs.runner.run_build`, checked every second): the build task is cancelled, which aborts in-flight aiohttp range requests and cancels process-pool futures that have not started; the temporary result directory is removed; the job becomes `cancelled` (HTTP 410). A later POST restarts it, reusing the cutout cache.
- **Priorities:** interactive builds (0) run before prewarm builds (10); the Redis queue is a sorted set.
- **Limits** (HTTP 429 with `Retry-After`): `SPHEREX_MAX_ACTIVE_PER_IP` active builds per address (3), `SPHEREX_MAX_QUEUE` queued builds in total (40), `SPHEREX_POST_RATE_PER_MIN` submissions per address and minute (30); `SPHEREX_MAX_RUNTIME` per build (900 s); `SPHEREX_MAX_FRAMES` exposures per position (4000). Pinned jobs bypass the per-address and queue limits.
- **Worker failure:** workers heartbeat every 10 s; a running job whose worker has been silent for 90 s is reported as failed so the client can resubmit. On SIGTERM a worker requeues its running jobs at the head of the queue.

## Consequences

- Abandoned builds stop within about `interest_timeout + 1` s (measured: 30 s timeout → cancelled; DELETE → cancelled in ~1 s).
- Clients must keep polling while they wait (the web client does) and should send `DELETE` when they leave a sequence.
- Long synchronous endpoints (catalog cones) use `run_until_disconnect`: the response slot is released when the client goes away, though the remote call ends on its own timeout.

## Alternatives considered

- **Cancel on WebSocket close:** cleaner signal, but polling already exists and works through every proxy.
- **Never cancel:** simplest, but a burst of quick target switches would queue dozens of useless builds.
