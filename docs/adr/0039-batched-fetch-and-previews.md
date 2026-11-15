# ADR-0039: Read exposures in batches on the process pool, opening epoch first, and show previews while the rest loads

- **Status:** Accepted
- **Date:** 2026-11-14
- **Extends:** [ADR-0013](0013-process-pool-and-slim-headers.md), [ADR-0037](0037-per-file-chunk-cache.md), [ADR-0038](0038-irsa-cutouts.md)

## Context

- **One core did the reading.** After ADR-0037 the dev server was limited by the API's event loop, not by S3. A cold 6′ build of Barnard's Star used 22.7 s of main-process CPU in 23.9 s of wall time. That CPU went to building a WCS from each header, slimming and parsing headers, decoding FLAGS tiles, and TLS. Only `process_frame` ran on the pool (ADR-0013).
- **A fresh pool costs seconds.** A build whose rows were all cached still took ~4.4 s, mostly the start-up of 24 pool workers importing astropy. The pool was only started by the first build.
- **The viewer waited for everything.** Nothing was shown until all 358 exposures were read and assembled, although the viewer opens on one epoch and that epoch is about a third of the exposures.

## Decision

### Batches on the pool (`pipeline.fetch`)

- **The exposures of a build are cut into batches** of 2–16 (`batches`), one wave over the workers. Each batch goes to a pool worker, which does all of the work for its exposures: IRSA cutouts or S3 range reads, header parsing, FLAGS decoding and `process_frame`. The main process only collects outcomes and assembles.
- **Each worker keeps its own event loop** in a thread (`_WorkerLoop`), with an S3 client and an IRSA client that stay open from batch to batch, so TLS connections are reused. Wavelength maps and ePSF tables are kept per worker too.
- **`SPHEREX_MAX_FRAMES_IN_FLIGHT` is for the whole build.** Each batch reads `ceil(max_frames_in_flight / cpu_workers)` exposures at once, and at least 2. Starting a few at a time lets later exposures find an IRSA slot that an earlier one freed. IRSA slots are a semaphore shared by all workers (ADR-0038).
- **`SPHEREX_FETCH_WORKERS=0`** keeps all reading in the calling process, one core, as before. The same `read_batch` coroutine runs there, and the offline tests drive it that way.
- **The pool is started with the process** (`warm_pool`, called by the API without a Redis queue and by the queue worker). Every worker is spawned and has imported its modules before the first build.

### Opening epoch first

- **`_Plan` orders the exposures.** The opening epoch (the first full epoch, where the viewer opens) comes first, then every other epoch in time order.
- **The opening epoch runs alone.** Its exposures go in small batches, `ceil(n_opening / workers)` each, so every worker reads a part of it, and each batch reads all of its exposures at once.
- **The rest is submitted** when no more than `HEAD_RELEASE` (1/8) of the opening batches are still running. Batches are handed on as they finish; batches that finish together are handed on in priority order.

### Previews

- **When the opening epoch is complete,** the finished epochs are assembled into a result of their own, `<key>p<n>`. Further previews follow once 1/3 and then 2/3 of the exposures are in finished epochs (`PREVIEW_STEPS`). There are no previews for builds of fewer than 40 exposures or with a single epoch.
- **A preview is written in a background task,** one at a time, while reading goes on. It is dropped if the full result is already being assembled when it is ready.
- **The job announces each preview** with a `preview:<id>` stage. `GET /api/jobs/{id}` shows the latest one as `preview`, and its `meta.json` carries `preview: {of, n, frames, total}`. With a shared bucket, each preview is published there, so that every API replica can serve it.
- **Previews are removed** from the cache and from the bucket once the full result is written.

### Web viewer

- **`loadSequence(..., onPreview)`** loads each new preview's metadata and shows it. Previews are not cached in the browser.
- **The view carries over.** When a preview is replaced by the next or by the full result, epochs are matched by time, so the view stays on the same epoch. A view the user has not touched is fitted again to the full result.
- **Planes load in three tiers:** first the image on screen, then its flags, then the neighbouring frames. Catalogue overlays wait until the first image is drawn.
- **A small load chip** at the top right shows the stage, the percentage and "Preview: X of Y exposures". It replaces the centred loader, and the sky is not dimmed while a preview is on screen.

## Consequences

- **Builds are faster.** Barnard's Star 6′ on the dev server (358 exposures, 583 MB, 29 workers, 128 in flight, nine cold runs):

  | Build | ADR-0037 (one event loop) | This ADR |
  |---|---|---|
  | Cold, first picture (the opening epoch, 118 exposures) | 19.8–20.4 s (only the full result) | 3.0–3.5 s in 4 runs, 4.5–6.9 s in 4, none in 1 |
  | Cold, full result | 19.8–20.4 s | 6.6–10.4 s, median 7.9 s |
  | Every row already cached | 4.4 s | ~1.0 s |

  The output files are the same as those of the ADR-0037 reader. The only change in `meta.json` is that `flag_bits` now lists bit 29 `SATELLITE_HALO`, which only files of newer pipeline versions define, whichever file is read first.
- **When to release the rest.** Six cold builds each, half with IRSA first and half from S3 only:

  | Rest of the build submitted | First picture | Full result |
  |---|---|---|
  | when 1/8 of the opening batches are left (`HEAD_RELEASE = 0.125`) | 3.0–3.5 s in 4 runs; 6.1 s in one; none in one | 6.6–8.0 s |
  | when every opening batch is done (`HEAD_RELEASE = 0`) | 3.3–6.0 s | 7.3–13.0 s |

  Waiting for every opening batch leaves the other workers idle behind the slowest read, so the full result comes later and the first picture is not earlier. With release at 1/8, a straggling opening read still delays the first picture: in one run the opening epoch finished only when the build was nearly done, and no preview was written. Hedging slow reads would address that and is not done yet.
- **Frames in flight.** `SPHEREX_MAX_FRAMES_IN_FLIGHT` stays at 64. Three cold builds each, IRSA first:

  | In flight | First picture | Full result |
  |---|---|---|
  | 64 (default; 3 per batch) | 3.1–5.9 s | 7.5–12.6 s |
  | 128 (5 per batch) | 4.5–6.9 s | 7.9–10.4 s |

  The difference is within the spread between runs. The opening batches read all their exposures at once whatever the setting, so it only changes the rest of the build, and 64 asks less of S3 when two builds run at once.
- **The pool is held from start-up:** one process per core (`SPHEREX_CPU_WORKERS`) with numpy, astropy and open connections, whether or not a build runs.
- **More results per build.** A preview is a full result written to disk, published to the bucket and removed again: two or three extra writes per large build. A preview that fails is logged and skipped; the build goes on.
- **A broken pool** (a worker killed, e.g. out of memory) makes the build resubmit its unfinished batches to a fresh pool once.

## Alternatives considered

- **Only `process_frame` on the pool, reading in the event loop** (ADR-0013): the event loop was the limit, at one core.
- **Threads for parsing:** WCS building and header parsing hold the GIL for most of their time.
- **One exposure per pool task:** pickling the spec and per-task overhead for every exposure, and no reuse of connections within a batch.
- **Show exposures one by one as they arrive:** an epoch is a co-add over its exposures. A partial epoch looks different from the finished one and would change under the user's eyes. A finished epoch does not change when the rest arrives.
- **Stream the opening epoch from the client side** (the browser asks for the opening epoch as a build of its own): two builds read the same files, and the second cannot reuse the first's work in flight.
