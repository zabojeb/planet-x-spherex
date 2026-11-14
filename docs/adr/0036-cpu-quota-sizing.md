# ADR-0036: Size pools and server workers from the cgroup CPU quota

- **Status:** Accepted
- **Date:** 2026-11-14
- **Extends:** [ADR-0013](0013-process-pool-and-slim-headers.md)

## Context

Containers report the host's cores: `nproc` showed 128 on the vast.ai box with a 32-CPU quota, and Railway replicas see the host too. A process pool of `os.cpu_count()` workers oversubscribes the quota and thrashes.

## Decision

`spherex_core.runtime.cpu_quota()` reads cgroup v2 `cpu.max`, then cgroup v1 CFS quota, then the affinity mask. The process pool defaults to `cpu_quota() − 1` (override `SPHEREX_CPU_WORKERS`); uvicorn workers to `2 × cpu_quota()` capped at 8 (override `WEB_CONCURRENCY`).

## Consequences

Scaling a Railway service's CPU up automatically widens its pools on the next start.
