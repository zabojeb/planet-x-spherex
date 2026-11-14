# ADR-0024: Regrid archival images on threads, not on the shared process pool

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The first archival builds used the process pool that sequence builds share (`pipeline.sequence.cpu_pool`).
A 20′ ε Indi B build on the dev server returned zero frames: every task failed with `BrokenProcessPool`, and
the worker log showed `OpenBLAS blas_thread_init: pthread_create failed ... Resource temporarily
unavailable`. Each spawned worker starts one OpenBLAS thread per host core (the container sees 128), and with
the API's own pool and other processes running, the container ran out of threads (~5,400 in use). A broken
pool stays broken for every later build in that process.

## Decision

- `archival/build.py` runs `to_grid` (background subtraction, NaN-aware Gaussian smoothing, bilinear
  resampling) with `asyncio.to_thread`. The work is ndimage filtering of windows of a few hundred pixels, so
  threads are fast enough (the 20′ build took 95 s, dominated by 1.75 GB of reads).

## Consequences

- An archival build can no longer break the pool that sequence builds depend on.
- The underlying problem remains for sequence builds: pool workers should cap BLAS threads
  (`OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1` set before the pool spawns), and the
  pool could be recreated after a `BrokenProcessPool`. Recorded here for the owners of `pipeline.sequence`
  and `runtime`.

## Alternatives considered

- **Keep the shared pool and cap BLAS threads:** needed anyway, but it touches modules outside this change.
- **A separate small pool for archival:** more processes competing for the same thread budget.
