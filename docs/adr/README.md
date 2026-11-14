# Architecture Decision Records

An ADR is a short note that records one significant decision: the situation, what we chose, what it costs us, and what we rejected. We write them so that teammates joining mid-hackathon, reviewers and judges can see *why* the code looks the way it does.

## How to add one

1. Copy [`template.md`](template.md) to `NNNN-short-title.md` with the next free number.
2. Fill in every section. Keep it to one page; link to code, docs or measurements instead of repeating them.
3. Add a row to the index below.
4. Accepted ADRs are not edited in substance. To change a decision, write a new ADR and set the old one's status to `Superseded by ADR-NNNN`.

Statuses: `Proposed` · `Accepted` · `Superseded by ADR-NNNN` · `Deprecated`.

## Index

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted | 2026-11-14 |
| [0002](0002-s3-range-reads.md) | Read Level 2 files straight from S3 with HTTP range requests | Accepted | 2026-11-14 |
| [0003](0003-tap-image-discovery.md) | Discover images with IRSA TAP, not SIA | Accepted | 2026-11-14 |
| [0004](0004-modular-monolith.md) | Modular monolith first, microservice seams prepared | Accepted | 2026-11-14 |
| [0005](0005-server-side-alignment-client-side-display.md) | Align and co-add on the server, display on the client | Accepted | 2026-11-14 |
| [0006](0006-common-output-grid.md) | Common output grid for all exposures | Accepted | 2026-11-14 |
| [0007](0007-epochs-and-coadds.md) | Epoch grouping and per-detector co-adds | Accepted, extended by ADR-0017 and ADR-0019 | 2026-11-14 |
| [0008](0008-flag-policy.md) | Contamination-flag policy | Superseded by ADR-0018 | 2026-11-14 |
| [0009](0009-forced-aperture-photometry.md) | Forced aperture photometry for spectra and light curves | Superseded by ADR-0016 | 2026-11-14 |
| [0010](0010-proper-motion-aware-targets.md) | Proper-motion-aware targets | Accepted | 2026-11-14 |
| [0011](0011-name-resolution-and-catalogs.md) | Name resolution and reference catalogs | Accepted, extended by ADR-0021 | 2026-11-14 |
| [0012](0012-caching.md) | Caching layers and cache keys | Accepted | 2026-11-14 |
| [0013](0013-process-pool-and-slim-headers.md) | Process pool and slim FITS headers for per-frame work | Accepted | 2026-11-14 |
| [0014](0014-dev-compute-on-vast-ai.md) | Development compute on a vast.ai instance next to S3 | Accepted | 2026-11-14 |
| [0015](0015-frontend-stack.md) | Frontend stack and URL-addressable views | Accepted | 2026-11-14 |
| [0016](0016-epsf-psf-photometry.md) | Fit the detector's effective PSF for photometry, skipping saturated pixels | Accepted, extended by ADR-0020 | 2026-11-14 |
| [0017](0017-nan-aware-resampling-and-filled-coadds.md) | NaN-aware resampling and filled co-add holes | Accepted | 2026-11-14 |
| [0018](0018-photometry-flag-policy.md) | Flag policy v2: HALO informational, 20 % masked threshold, saturation excluded rather than bad | Accepted | 2026-11-14 |
| [0019](0019-merge-small-epochs.md) | Merge small epochs into close neighbours, otherwise mark them partial | Accepted | 2026-11-14 |
| [0020](0020-ring-photometry-for-saturated-stars.md) | Measure saturated stars in a fixed ring outside the core, as relative fluxes | Accepted | 2026-11-14 |
| [0021](0021-local-object-index-for-search.md) | Suggest objects from a local index while typing; ask Sesame only for names it does not know | Accepted | 2026-11-14 |
| [0022](0022-3d-views.md) | Offer three 3D views (relief, time cube, λ cube), drawn with three.js loaded on demand | Accepted | 2026-11-14 |
| [0023](0023-archival-epochs.md) | Add 2MASS, AllWISE and NEOWISE epochs on the sequence's own grid | Accepted | 2026-11-14 |
| [0024](0024-moving-objects-via-most.md) | Follow solar-system objects with IRSA MOST and a co-moving sequence mode | Accepted | 2026-11-14 |
| [0025](0025-background-tasks-for-archival-and-most.md) | Run archival builds and MOST searches as polled background tasks in the API process | Accepted | 2026-11-14 |
| [0026](0026-archival-regridding-on-threads.md) | Regrid archival images on threads, not on the shared process pool | Accepted | 2026-11-14 |
| [0027](0027-photometry-of-any-position.md) | Measure any position on the sequence's cached cutouts, synchronously | Accepted | 2026-11-14 |
| [0028](0028-proper-motion-from-coadd-centroids.md) | Fit proper motion to per-epoch co-add centroids with per-detector zero points | Accepted | 2026-11-14 |
| [0029](0029-periodograms.md) | Lomb–Scargle periodograms on the client's relative light curve | Accepted | 2026-11-14 |
| [0030](0030-export-formats.md) | Exports as FITS with WCS, CSV/VOTable photometry and a download script | Accepted | 2026-11-14 |
| [0031](0031-releases-freshness-and-filters.md) | Release summary, freshness checks and optional build filters | Accepted | 2026-11-14 |
| [0032](0032-api-worker-split-on-railway.md) | Stateless API replicas, queue workers, Redis and a bucket on Railway | Accepted | 2026-11-14 |
| [0033](0033-job-queue-heartbeats-and-cancellation.md) | Job queue with dedupe, heartbeats, cancellation, priorities and limits | Accepted | 2026-11-14 |
| [0034](0034-immutable-http-caching-and-light-endpoints.md) | Immutable HTTP caching, precompressed metadata and lightweight result endpoints | Accepted | 2026-11-14 |
| [0035](0035-packaging-docker-and-railway-config.md) | One multi-stage image, docker compose for local runs, Railway config as code | Accepted | 2026-11-14 |
| [0036](0036-cpu-quota-sizing.md) | Size pools and server workers from the cgroup CPU quota | Accepted | 2026-11-14 |
