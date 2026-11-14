# ADR-0025: Measure any position on the sequence's cached cutouts, synchronously

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The IRSA Spectrophotometry Tool extracts spectra for up to 20 sources per job, two jobs per user, minutes per job, and does not follow proper motion (docs/research/analogs.md). Our viewer wants a spectrum and light curve for any source a user clicks or picks from a catalogue. The build already cached every native cutout on disk (cutouts/), keyed by file, grid centre and cutout size.

## Decision

`POST /api/sequences/{id}/photometry` runs the pipeline's own measurements (ePSF fit, aperture fallback, ring photometry for detectors in which the source saturates, ADR-0016/0020) at a new position, with optional proper motion, on those cached cutouts in the shared process pool, and returns records shaped exactly like `meta.photometry`. The Level 2 files of a sequence are recovered from its footprint search (meta.json keeps file names only). The PSF zone is the one nearest to the new position. It is synchronous: 11–14 s for 358–458 exposures from a cold script, faster in the API where the pool is warm; no image data is downloaded again.

## Consequences

Any source in the field gets the same quality of photometry as the target, in seconds, with no second job framework (the infra agent's queue can wrap it later). It depends on the cutout cache: if it was purged, the cutouts are re-read from S3 (slower, still correct). The cutout half-size formula is duplicated in `analysis.common.cutout_half` and `pipeline.sequence`; they must stay equal because the size is part of the cache key.

## Alternatives considered

- Call IRSA's Spectrophotometry Tool: no documented API, job limits, no proper motion.
- Re-measure on the aligned co-adds: resampling broadens the PSF and mixes wavelengths; per-exposure photometry is what gives spectra.
- A job queue for this endpoint: unnecessary at these run times and would duplicate the job system being replaced.
