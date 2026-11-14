# ADR-0030: Stateless API replicas, queue workers, Redis and a bucket on Railway

- **Status:** Accepted
- **Date:** 2026-11-14
- **Extends:** [ADR-0004](0004-modular-monolith.md)

## Context

The site goes public on Railway and must serve many concurrent visitors. Until now one uvicorn process ran the API, the build jobs (in-process asyncio tasks) and the process pool, with every result on one local disk. That cannot scale horizontally: jobs live in one process's memory, and Railway volumes attach to a single replica, so replicas cannot share a disk.

Most traffic is reading finished, immutable results (the five presets, shared links); builds are rarer but CPU- and network-heavy (5–40 s, up to minutes for deep fields).

## Decision

- **One Docker image, two roles** (`SPHEREX_ROLE=api|worker`, `deploy/start.sh`).
- **`api`**: FastAPI serving the SPA and the API, stateless, N replicas; uvicorn workers per replica = 2 × CPU quota, capped at 8 (`runtime.cpu_quota()` reads the cgroup quota, not the host's core count).
- **`worker`**: M replicas of `python -m spherex_core.jobs.worker`, each running `SPHEREX_JOBS_PER_WORKER` builds at a time on a process pool sized from its own CPU quota. Builds run on workers only, so slow builds never block page loads.
- **Railway Redis**: the job queue, job state and progress, heartbeats, cancellation flags and rate-limit counters (ADR-0031).
- **Railway Bucket** (S3-compatible): the shared copy of every finished result. A worker builds to its local disk, then publishes `sequences/<id>/*` with `meta.json` last; an API replica that lacks a result downloads it once into its own disk cache (`spherex_core.store`), serialized across its uvicorn processes with a file lock.
- **Cutout, wavelength and ePSF caches stay local per worker** (ephemeral disk). A cold worker re-reads them from the public SPHEREx S3 bucket, which is fast next to us-east-1; a bucket tier for cutouts was not worth the extra writes.
- **Region:** everything in US East (Railway `us-east4`, bucket `iad`), next to `nasa-irsa-spherex` in AWS us-east-1.
- **Local and dev-box mode is unchanged:** without `REDIS_URL` the in-process queue and local disk are used, so `uvicorn spherex_api.main:app`, the tests and the vast.ai server keep working (one uvicorn worker in that mode).

## Consequences

- API replicas and workers scale independently in Railway (`replicas`).
- A result is built once and then served from local disk on every replica, with immutable HTTP caching (ADR-0032).
- Redis is a single point of failure for builds (not for already-cached reads, which only need the bucket).
- Workers and API replicas need the bucket credentials as variables.

## Alternatives considered

- **One big container with a volume and more processes:** simplest, but cannot add replicas and loses everything on redeploy unless the volume is kept; no isolation between page loads and builds.
- **Celery or arq:** arq would fit asyncio, but cancellation by heartbeat and per-IP limits needed custom code anyway; a ~250-line Redis queue (ADR-0031) avoids a dependency and keeps both backends identical.
- **Results only in Redis:** cubes of up to tens of MB per sequence would exhaust Redis memory.
