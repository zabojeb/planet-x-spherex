# ADR-0017: NaN-aware resampling and filled co-add holes

- **Status:** Accepted
- **Date:** 2026-11-14
- **Extends:** [ADR-0006](0006-common-output-grid.md), [ADR-0007](0007-epochs-and-coadds.md)

## Context

Masked pixels are set to NaN before an exposure is aligned to the output grid ([ADR-0018](0018-photometry-flag-policy.md) keeps the image mask). ALGO_VERSION 1 resampled with plain bilinear interpolation, so every NaN wiped out the output pixels that touched it, and each masked area grew by about one pixel.

The cores of saturated stars carry masked bits (`SUR_ERROR`, `TRANSIENT`, `NONFUNC`, `PERSIST`) in every exposure, not only `OVERFLOW`. Together with the growth, this left Barnard's Star with a 6–7 px hole of zero coverage in the D1–D4 co-adds. The hole showed as a dark spot in the viewer and moved its per-epoch centroid by up to 20″ in a partial epoch.

## Decision

- **Resampling** (`processing.grid.resample`) interpolates `data × valid` and `valid` separately and divides. An output pixel is NaN only where less than half of its bilinear weight falls on valid input, so masked areas keep their size.
- **Each exposure is also aligned without masking** (`aligned_raw`, background-subtracted).
- **Holes are filled** (`processing.coadd.fill_holes`). Where a co-add pixel has coverage 0 although unmasked values exist, it gets the median of those values.
- **Filled pixels are marked.** They get pipeline bit 31 `FILLED` in `flags.bin`, which `meta.flag_bits` lists with `pipeline: true`. `coverage` stays 0 there, so the client still knows that no clean exposure covered them. `meta.epochs[i].bands[b].filled_pixels` counts them.

## Consequences

- Saturated stars look like stars in every co-add, and blink and difference images no longer show artificial holes.
- Filled values come from flagged pixels, so they are unreliable for photometry. Photometry is done on native pixels with its own masks ([ADR-0016](0016-epsf-psf-photometry.md)), never on co-adds.
- The per-exposure CPU work resamples one more array (one extra `map_coordinates` call), which is small next to the WCS evaluation.

## Alternatives considered

- **Not masking `SUR_ERROR` / `TRANSIENT` when `OVERFLOW` is set:** this depends on how the onboard processing sets bits around saturation, which we have not seen documented, and it would let real cosmic-ray hits next to bright stars through.
- **Inpainting holes from neighbouring pixels:** it invents structure where the real values exist, flagged but informative.
