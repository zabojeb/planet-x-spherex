# ADR-0012: Caching layers and cache keys

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

A first build costs 30–40 s and hundreds of megabytes of reads (ADR-0002). Archive queries take 3–15 s. Users change display settings, epoch settings and fields repeatedly, demos must be instant, and the same exposures are reused across settings. Level 2 files are immutable: a reprocessing produces a new path with a new pipeline version in it.

## Decision

All caches live under `SPHEREX_CACHE_DIR` (on the dev server `/workspace/cache`):

| Layer | Path | Key | Lifetime |
|---|---|---|---|
| Archive query results | `tap/<key>.json` | RA and Dec to 5 decimals + collection list | 12 h (`SPHEREX_TAP_CACHE_HOURS`) |
| Native cutouts | `cutouts/<aa>/<sha1>.npz` | SHA-1 of file URL, RA, Dec, half-size; also stores "off the frame" results | permanent |
| Wavelength maps | `wave/D<n>.npz` | detector | permanent |
| Catalogue cones | `catalogs/<catalog>_<key>.json` | catalogue, RA, Dec, radius | 7 days |
| Sequence results | `sequences/<id>/` | first 20 hex digits of the SHA-1 of the normalised request (RA/Dec to 6 decimals, proper motion to 0.1 mas/yr, reference epoch, field, epoch parameters, collections, `ALGO_VERSION`) | permanent |

Sequence results are written to `<id>.tmp/` and renamed when complete, so readers never see half-written results. The target's display name is not part of the key.

## Consequences

- Rebuilding a sequence with different epoch settings reuses every cutout: Barnard's Star rebuilt from cached cutouts in 5.2 s instead of 37.7 s.
- Bumping `ALGO_VERSION` invalidates all sequence results after a processing change.
- New weekly data reach users after at most 12 hours (the query cache lifetime).
- There is no eviction yet. The dev server has 150 GB of disk; by array sizes, a cached cutout at a 6′ field (a 91 × 91 window of image, variance and flags) is about 0.1 MB, so ~36 MB for Barnard's Star's 358 exposures, plus the sequence result.
- Requests that differ only by rounding share results; requests with different fields do not share cutouts.

## Alternatives considered

- **Redis or a database for metadata:** another service to run for no measured gain at our scale (ADR-0004).
- **No cutout cache:** every change of epoch settings would re-read hundreds of megabytes.
