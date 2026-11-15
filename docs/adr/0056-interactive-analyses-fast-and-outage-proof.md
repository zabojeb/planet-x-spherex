# ADR-0056: Interactive analyses are fast and survive remote-service outages

- Status: Accepted (extends 0053)
- Date: 2026-11-15

## Context

"Click a point → Analyze" took minutes on the live server and then failed. The server log showed why:

- The photometry step looked up the sequence's Level 2 files with a fresh IRSA TAP footprint search whenever the
  TAP cache was older than 12 h. With IRSA timing out and returning 502s, that waited 3 × 300 s and failed, taking
  the spectrum and periodogram steps down with it — although the sequence's cutouts were all on disk.
- Remote calls used background-build timeouts: TAP 300 s × 3 attempts, Gaia 120 s × 3, catalogue cones up to
  ~8 minutes, JPL sb_ident 20–37 s per epoch one after another.
- The steps ran one after another, and point analyses shared the two job slots with minutes-long field analyses.
- Re-measuring cached cutouts loaded each npz, parsed its header and built its WCS on the event loop (~25 ms per
  exposure), so 358 exposures took 10 s and R Dra's 2,493 more than a minute.
- `force=true` did not re-run a finished analysis (the status call served the old result file first), and the
  motion step centroided at the request position, which for a catalogue-epoch position of a fast mover (ε Indi B
  at J2000 is 2′ from where SPHEREx sees it) found nothing.

## Decision

1. **An existing sequence never searches IRSA again.** Builds write `frames.json` (every Level 2 file) next to the
   result; `sequence_frames` reads it, else the build's TAP cache of any age, else rebuilds the IRSA path from the
   file name and collection (`ibe/data/spherex/<dr1|qr3>/level2/<week>_<group>/<version>/<detector>/<file>`).
2. **Interactive network mode** (`spherex_core.netpolicy.fast()`): remote calls clamp to 15 s and two attempts with
   short pauses, and a **per-service circuit breaker** skips a service for 5 minutes after it fails
   (`ServiceUnavailable`, immediately). Successes close it. Background builds keep their long timeouts and never
   skip. Catalogue cones serve a stale cached answer when the service is down. Applied to TAP (IRSA, ESA Gaia,
   SIMBAD mirrors), JPL sb_ident and Horizons, SVO filter curves and template downloads, and the catalogue overlay
   endpoint.
3. **Concurrent steps with a deadline.** Identification, known objects, tracklets and motion start at once;
   photometry waits at most 8 s for a catalogue counterpart to follow, then spectrum, periodogram and colours run
   on its records. Whatever is not finished after 60 s becomes `{"error": "TimeoutError: not finished within 60 s
   ..."}` (the thread keeps going and fills the caches for the next try, but from the deadline on it changes
   nothing in the returned result, reports no progress and starts none of the steps that follow it). The job
   status carries `steps` (`pending | running | done | error` per step) and the result a `timings` block
   (per-step seconds, timed-out steps, services whose breaker is open).
4. **JPL in parallel:** spacecraft states for all epochs in one Horizons call, then sb_ident for up to 8 epochs, 4
   at a time, within 40 s; queries are centred on a 0.01° bucket so nearby clicks share the cache. Catalogue cones
   are bucketed the same way.
5. **Pool-side re-measure:** cached exposures are loaded and measured entirely in the process-pool workers (wave
   maps and ePSF tables loaded once per worker), only missing cutouts go through the S3 reader.
6. Point analyses get their own 6 job slots; `force` drops the old result; motion starts at the measured target's
   position at the sequence's mean epoch, with its catalogue motion as the search prior; the verdict no longer
   claims "no Gaia counterpart" (or a new mover) when Gaia could not be reached.

## Consequences

- Warm analyses return in about a second; cold ones in seconds to tens of seconds, bounded by 60 s; with IRSA and
  Gaia unreachable the analysis still returns, with those slots saying "unavailable".
- A 60 s deadline can cut off a slow step on the first try; repeats are faster because every finished download and
  catalogue answer is cached.
- The breaker is per process; separate uvicorn workers learn about an outage independently (each pays one short
  timeout).

## Alternatives considered

- Keep sequential steps and only shorten timeouts: still the sum of all waits, and one stuck call blocks the rest.
- Async rewrite of every remote client: larger change; threads plus a context-propagated policy were enough.
- Global request deadline in the HTTP layer: the job runs outside the request; the analysis needs to know its own
  budget to return partial results.
