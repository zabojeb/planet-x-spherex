# ADR-0025: Run archival builds and MOST searches as polled background tasks in the API process

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Archival builds (ADR-0023) take 1–2 minutes on a cold cache and MOST searches (ADR-0024) from 2 s to ~3
minutes. Neither fits a synchronous HTTP request. Sequence builds already use a job queue with an in-process
and a Redis implementation (ADR-0032, ADR-0033), but that queue is specialised to `SequenceParams` and
`build_sequence`. In local and dev-server mode the API runs as one uvicorn process; on Railway it runs
several uvicorn processes per replica that share a disk.

## Decision

- A small generic registry (`spherex_core/tasks.py`, `TaskRegistry`) runs these builds as asyncio tasks with
  progress; the first GET starts the task and returns 202, later GETs poll the same URL, the finished result
  is served from disk. Failed tasks are kept for two minutes, then a new GET retries.
- A cross-process lock file next to the result (`claim`/`release`, stale after 15 minutes) stops several
  uvicorn workers on one replica from building the same thing twice.
- Archival regridding runs in threads, not in the shared process pool (ADR-0026).

## Consequences

- No new infrastructure; works the same in tests, on the dev server and in one Railway replica.
- On a multi-replica Railway deployment each replica may build its own copy (results live on local disk, not
  in the bucket), and the work runs on API replicas rather than workers. Follow-up: move archival builds and
  MOST searches onto the job queue and publish archival results to the bucket like sequences.

## Alternatives considered

- **Extend the sequence job queue now:** the right end state, but it would have meant reworking a module
  another agent was changing at the same time.
- **Synchronous endpoints:** requests of minutes would hit proxy timeouts and block a worker.
