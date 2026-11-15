# ADR-0057: A local SPHEREx file-catalogue mirror, multi-endpoint fallbacks and a persistent popular-field tier

- **Status:** Accepted (builds on ADR-0037–0039 and ADR-0056)
- **Date:** 2026-11-15

## Context

Every new field started with an IRSA TAP footprint search (8–15 s on a good day). On 2026-11-14/15 IRSA TAP and
SIA answered 502 or timed out for hours, ESA's Gaia archive and VizieR timed out, and CDS SIMBAD was down: new
fields could not be built at all, and catalogue overlays waited on dead services. The files themselves stayed
reachable on AWS S3 and on IRSA's ibe file server. ADR-0037–0039 made the pixel reads fast (chunk cache, IRSA
cutouts first, batched reads) but left the search, the catalogues and slow single reads ("one slow read delays
the first picture") untouched.

## Decision

1. **Local mirror of the file catalogue** (`archive/mirror.py`, SQLite + R-tree on the footprint's unit-vector
   bounding box). Filled from the public bucket alone: list the keys (~20 s for 1.63 M files), read the first
   32 KiB of each new file and take the footprint from its own WCS (SIP included). Incremental hourly refresh from
   a background thread (one process per machine, file lock); `sync_tap()` can relabel wide/deep collections when
   TAP is up. `query_frames` answers from the mirror when it is complete and fresh, else asks IRSA TAP / SIA and
   falls back to the mirror when both fail. The release summary and freshness checks use it too.
2. **Endpoints as interchangeable sources** (`upstreams.py`, `endpoints.py`). Each kind of data has several
   sources; every call records latency and outcome; the ordering is p50 latency plus a penalty for recent
   failures and open breakers. `call()`/`call_async()` try sources in order and, when asked, *hedge*: start the
   next source once the current one runs past ~1.5× its p90. Applied to SPHEREx file range reads (AWS S3 ↔ IRSA
   ibe ↔ optional S3-compatible mirror, with per-source concurrency and shared identical in-flight reads),
   image search (mirror ↔ TAP ↔ SIA), Gaia DR3 (ESA ↔ MAST ↔ IRSA ↔ VizieR), CatWISE (IRSA ↔ VizieR ↔ NOIRLab
   Data Lab), 2MASS (IRSA ↔ VizieR TAP ↔ VizieR CfA mirror over ASU ↔ Data Lab) and SIMBAD (CDS ↔ Harvard). Sources
   with an open breaker are dropped before a call; the last one left gets one longer attempt (30 s) in
   interactive mode. Known solar-system objects fall back from JPL `sb_ident` to IMCCE SkyBoT, flagged
   geocentric with widened tolerances. IRSA's cutout service (ADR-0038), MOST and Sesame report into the same
   statistics; the cutout service is skipped while it fails most requests. `GET /api/status/upstreams` shows the
   order, latency, success rate and breakers.
3. **Popular fields** (`popular.py`, `targets/popular.json`, `scripts/prewarm_popular.py`): a curated list plus
   fields requested ≥ 5 times are built (pinned, prewarm priority) with archival epochs and catalogue cones.
4. **Persistent tier in Cloudflare R2** (`scripts/cutout_tier.py`): per popular sequence, a bundle of its result,
   chunk/legacy cutout files and detector tables, read back over the bucket's public URL by a fresh server. Uploads
   run from a machine logged in with `wrangler`, which can also stream a bundle back to a server (`fetch`); the
   server needs no credentials, and the bucket has no public URL unless one is enabled.

## Consequences

- New fields no longer depend on IRSA TAP; a footprint search takes 0.06–0.5 s instead of ~13 s
  (measurements in [data-access-performance.md](../data-access-performance.md)).
- A dead catalogue service costs one failed (or hedged) request, then is skipped; Gaia keeps working via MAST,
  2MASS via the CfA VizieR mirror, CatWISE via Data Lab (~16 s per cone).
- The mirror costs 1.63 M range reads of 32 KiB (~53 GB, 13 min) once, 1.0 GB of disk, and a 13 s listing per
  hour.
- Archival epochs of fields never built still need IRSA's image search (separate work).
- Wide vs deep survey labels are only known after a TAP relabel; until then both count as their release.
- Statistics are per process; with Redis each process publishes a summary every 15 s for a cluster view.
- On Railway (no shared disk between replicas) the mirror file has to be shipped to each worker (e.g. from the
  bucket at start-up); this is not automated yet.

## Alternatives considered

- **Mirror the whole archive (106 TB) in R2:** ~$1,600/month; the open-data bucket already serves the bytes for
  free, so only metadata and popular pixels are worth copying.
- **Query TAP once for the whole plane table:** depends on the service that was failing; the bucket listing plus
  headers needs nothing from IRSA.
- **Always hedge every request:** doubles load; hedging only past ~p90 adds a few percent of extra requests.
