# Data access: speed, fallbacks and what to mirror

How the backend finds SPHEREx images, reads pixels and queries catalogues when the remote services are slow or
down, with the measurements behind [ADR-0057](adr/0057-data-access-mirror-and-fallbacks.md) and a cost
comparison of mirroring options. It builds on the per-file chunk cache, IRSA cutouts first and batched reads of
ADR-0037 to ADR-0039.

All numbers are from the dev server on 2026-11-15, during an outage: IRSA TAP and SIA answered `502 Proxy Error`,
ESA's Gaia archive, CDS (SIMBAD TAP, VizieR TAP and ASU) and JPL's `sb_ident` timed out. IRSA's file server
(ibe), IRSA MOST, AWS S3, MAST, the CfA VizieR mirror, NOIRLab Data Lab and IMCCE SkyBoT kept working.

## Where each kind of data comes from

Each kind of data has several interchangeable sources (`backend/spherex_core/endpoints.py`). Every call records
latency and outcome; the next call asks the source with the lowest median latency plus a penalty for recent
failures and open circuit breakers. Interactive requests *hedge*: when a source runs past about 1.5× its p90, the
next one starts alongside and the first answer wins. A source whose breaker is open is skipped; the last source
left gets one 30 s attempt instead of two 10 s ones. `GET /api/status/upstreams` shows the current order,
p50/p90 latency, success rate, last error and the breakers ([api.md](api.md#data-sources-and-the-file-catalogue-mirror-adr-0057)).

| Kind | Sources (starting order) |
|---|---|
| `spherex-meta` image search | local file-catalogue mirror · IRSA TAP · IRSA SIA2 |
| `spherex-files` byte ranges | optional S3-compatible mirror (`SPHEREX_FILE_MIRROR_BASE`) · AWS S3 open data · IRSA ibe (same bytes) |
| `spherex-cutouts` | IRSA cutout service (ADR-0038; skipped while it fails most requests) · range reads |
| `gaia` | ESA Gaia archive · MAST · IRSA `gaia_dr3_source` · VizieR I/355 |
| `catwise` | IRSA `catwise_2020` · VizieR II/365 · NOIRLab Data Lab `catwise2020.main` |
| `2mass` | IRSA `fp_psc` · VizieR II/246 (TAP) · VizieR CfA mirror (ASU) · NOIRLab Data Lab `twomass.psc` |
| `simbad` | CDS · Harvard mirror |
| `names` | CDS Sesame and the Harvard mirror, raced (unchanged; now reported) |
| `solar-system` | JPL `sb_ident` (SPHEREx-centric) · IMCCE SkyBoT (geocentric, flagged) · IRSA MOST (reported) |

Identical in-flight byte ranges are shared, and with Redis each process publishes a summary every 15 s
(`spherex:upstreams`) for a cluster view.

## The file-catalogue mirror

`archive/mirror.py` keeps a SQLite table of every SPHEREx Level 2 file with an R-tree over its footprint. It is
built from the public bucket alone: list the keys, read the first 32 KiB of each new file and take the footprint
from the file's own WCS (SIP included), so it needs nothing from IRSA.

| | |
|---|---|
| Files | 1,628,230 (DR1 1,179,406; QR3 448,824), 0 failed headers |
| Time span | DR1 2025-04-24 → 2026-05-14, QR3 2026-05-14 → 2026-10-05 |
| Size | 1.01 GB (SQLite, WAL) |
| First build | 769.7 s (12.8 min): S3 listing plus one 32 KiB range read per file (~53 GB read), ~2,100 files/s |
| Hourly refresh | 12.7 s with no new files (list 1.63 M keys, diff) |
| Agreement with TAP | the same file sets as IRSA TAP for all five presets: 358, 458, 258, 335 and 2,493 files, no difference |
| Point query | presets 63–512 ms (R Dra, 2,493 files, is the slowest); 200 random points p50 87 ms, p90 125 ms (the first query after start 4.6 s while the page cache fills) |

The release summary (`/api/releases`) also comes from it: 1.16 s on the first call, 18 ms cached (TAP: a full
plane-table scan, ~14 s, and 502 during the outage). Wide and deep survey files count as their release until
`scripts/sync_mirror.py --tap` relabels them from TAP.

## Before and after

"Before" is the dev API with the code of `main` plus #18 at the same moment; "after" is this branch on a spare port
with the same cache directory (catalogue cones are cached per position, so each row used a field neither had
cached).

| Case | Before | After |
|---|---|---|
| New field (Centaurus A, 4′, 379 exposures, 3 epochs), IRSA TAP down | fails after 8.0 s: `TAP query failed after 3 attempts: HTTP Error 502` | full result in 9.0 s: search 0.11 s from the mirror, reads 8.3 s |
| Footprint search when TAP works | 13.0 s (WISE 0855, [methods.md](methods.md)) | 0.06–0.5 s |
| The same field again | instant (cached) | 4 ms (unchanged) |
| Gaia DR3 cone, ESA down | 502 at once (breaker open) | 0.2–2.9 s via MAST |
| CatWISE2020 cone, IRSA and CDS down | 502 at once | 15.8–18.5 s via NOIRLab Data Lab |
| 2MASS cone, IRSA and CDS down | 502 at once | 0.1–4.2 s via the CfA VizieR mirror |
| SIMBAD cone, CDS down | 2.4 s via Harvard | 0.1–4.2 s via Harvard (now ordered by measured latency) |
| Known asteroids and comets, JPL `sb_ident` down | error | 15.5 s: 10 s JPL timeout, then SkyBoT (4.9 s), geocentric and flagged |
| Popular fields | built on first request | nine fields prewarmed in 5.1 min (pinned, prewarm priority) with catalogue cones; their pixels are kept in R2 |

The known-object fallback is a degraded answer: SkyBoT cannot place the observer on SPHEREx, so its positions are
geocentric (off by up to 9.7″/D for an object D AU away) and its rates miss the spacecraft's orbital motion
(37″/h ÷ D). Such objects carry `geocentric: true`, their position error includes the parallax, the motion
tolerance includes the orbital term, the overlay's `observer` says so, and the answer is cached apart so the next
query asks JPL again.

**Not covered:** archival epochs of fields never built still need IRSA's image search, so they failed during the
outage for the four non-preset prewarm fields (the presets were cached); that fallback is separate work.

## Popular fields and the persistent tier

- `targets/popular.json`: the five presets plus 16 well-known fields (nearby stars and brown dwarfs, variables,
  young stars, nebulae and galaxies, two moving objects). Fields requested ≥ 5 times
  (`SPHEREX_POPULAR_PROMOTE_AFTER`) are promoted automatically; counts live in Redis or `<cache>/popular/`.
- `scripts/prewarm_popular.py` builds them as pinned jobs at prewarm priority, one at a time, with archival
  epochs and catalogue cones.
- `scripts/cutout_tier.py` bundles a sequence's result, its per-file chunks, legacy cutouts and detector tables
  into checksummed tar parts (≤ 240 MB) and pushes them to Cloudflare R2 through a machine logged in with
  `wrangler`; the server holds no credentials. `fetch` + `pull` restore them on a new server (`pull` also reads a
  public bucket URL, if the bucket is given one).

The nine `tier` fields (five presets, Proxima Centauri, Wolf 359, Luhman 16, Mira) make 1.80 GB in 12 parts plus
a manifest each. The five presets (1.10 GB, 8 parts) are in the R2 bucket `planet-x-spherex-tier`; the other four
(0.70 GB) are bundled on the dev server and go up with `cutout_tier.py push <ids>` (the upload streams through the
logged-in machine, ~1 MB/s from a home connection; `push` retries a failed part and `--skip-parts` resumes).

## What to mirror, and what it costs

The archive on 2026-11-15: DR1 76.9 TB, QR3 29.2 TB, 1.63 M files of 65 MB on average (106 TB in the collections
we search), growing ~1.4 TB per week (~6 TB per month). 1 TB holds ~15,300 files, 0.94 % of the archive.

| Option | Size | Monthly cost | Notes |
|---|---|---|---|
| File-catalogue mirror (this PR) | 1.0 GB | ~$0.09 on vast.ai disk, ~$0.15 on a Railway volume | removes the dependency on IRSA TAP; refresh 13 s/hour |
| Popular-field tier in R2 (this PR) | 1.1 GB (1.8 GB with all nine fields) | $0 (R2 free tier: 10 GB-month, 1 M writes, 10 M reads) | $0.015/GB-month beyond 10 GB |
| 1 TB of pixels in R2 | 1 TB | ~$15 | writes $4.50/M (15,300 files: $0.07 once), reads $0.36/M, no egress fees |
| 1 TB in a Railway Bucket | 1 TB | ~$15 | $0.015/GB-month, egress and operations free |
| 1 TB on the vast.ai instance disk | 1 TB | ~$93 | $0.093/GB-month, fixed when the instance is created, lost with it |
| 1 TB on a Railway volume | 1 TB | ~$150 | $0.15/GB-month, plus $0.05/GB service egress |
| The whole archive in R2 | 106 TB, +6 TB/month | ~$1,590, +$90 every month | duplicates a bucket that is already free to read |
| AWS S3 open data (what we read now) | — | $0 | no egress fees on the open-data bucket; 0.13 s per range read from the dev server |

**Recommendation:** keep reading pixels from the AWS open-data bucket (free, fast, and it stayed up while IRSA did
not), with IRSA ibe as its automatic fallback. Keep the 1 GB file-catalogue mirror on every server, and keep only
the popular fields' pixels in R2 (a few GB, inside the free tier). A 1 TB pixel mirror would only help if S3 *and*
IRSA ibe were both unreachable, and the per-file chunk cache of ADR-0037 already keeps what has been read; if it is
ever wanted, R2 or a Railway Bucket (~$15/month) are the right places, never instance disk or volumes.
