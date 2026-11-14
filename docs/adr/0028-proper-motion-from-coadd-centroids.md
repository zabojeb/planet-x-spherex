# ADR-0028: Fit proper motion to per-epoch co-add centroids with per-detector zero points

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Users need a number, not only a blink: how fast does this source move? WiseView and the IRSA tools do not measure motion. Our co-adds are aligned on one grid per epoch and detector.

## Decision

`GET /api/sequences/{id}/motion` centroids the source near a grid pixel (or sky position) in every epoch and detector: flux-weighted within 2.5 px above a median background in a 4–7 px annulus, iterated, SNR ≥ 5, following the source with the running fit (or a supplied prior motion). It fits a straight line in tangent-plane offsets with one shared motion and one zero point per detector, by weighted least squares, with per-centroid errors FWHM/(2.355·SNR) floored at 0.02 px and the covariance scaled by sqrt(reduced χ²) when the scatter is larger. It returns the combined fit, per-detector fits, every centroid with residuals, and the fitted track.

## Consequences

Validation (3–4 epochs, 1.1–1.4 yr): ε Indi B 3628 ± 305 / −2526 ± 260 mas/yr vs Gaia 3982 / −2467; Barnard's Star −1316 ± 325 / 9557 ± 385 vs −802 / 10362 (within 1.6σ / 2.1σ; its saturated, filled core scatters by ~0.5″ per detector); three static Gaia stars give −98…+57 mas/yr, all within 1.4σ of zero. Precision is ~300 mas/yr with today's baseline and will improve with every new visit. Reduced χ² of 20–30 says the per-centroid errors are underestimated for bright stars; the scaling keeps the quoted errors honest.

## Alternatives considered

- PSF fitting on the co-adds: the co-add PSF is a mix of wavelengths and resampling, not a known model.
- One common zero point for all detectors: blends with sources of other colours (WISE 0855) shift centroids per detector.
- Fitting single exposures: many more points but each with a different PSF and wavelength; a later option.
