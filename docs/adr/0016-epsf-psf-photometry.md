# ADR-0016: Fit the detector's effective PSF for photometry, skipping saturated pixels

- **Status:** Accepted
- **Date:** 2026-11-14
- **Supersedes:** [ADR-0009](0009-forced-aperture-photometry.md)

## Context

The validation of ALGO_VERSION 1 ([analysis/validation/REPORT.md](../../analysis/validation/REPORT.md)) found three problems with the aperture photometry of ADR-0009:

- **Bright stars were unusable.** Every measurement of Barnard's Star, RW Aur and R Dra was flagged, because the aperture contains saturated (`OVERFLOW`, `NONLINEAR`) pixels.
- **Fluxes ran about 5 % low.** The Gaussian aperture correction from the median `PSF_FWHM` does not describe the undersampled real PSF. A field star compared against catalogues gave J 0.94, H 0.97, Ks 0.94, W1 0.91 and W2 0.99.
- **Error bars were too small.** A constant star had reduced χ² ≈ 3.8.

Every Level 2 file carries an `EPSF` extension: a 21 × 21 grid of detector zones (~97 px each, 441 rows), each with a 33 × 33 effective PSF oversampled 5× (photutils ePSF convention, each array sums to 1). The table is identical in every DR1 and QR3 file of a detector; we checked row 220 of three D1 files (two DR1 weeks and one QR3 week) byte for byte. The ePSF is compact. At D1, a centred star puts 67 % of its flux in the central pixel and 97 % within 3 × 3 pixels; at D6 the figures are 49 % and 94 %.

## Decision

- **Primary measurement: an ePSF fit** (`photometry.psf_photometry`):
  - The ePSF comes from the zone nearest the cutout centre. The table is read once per detector and cached in `epsf/D<n>.npz`.
  - The ePSF is evaluated with a cubic spline at the target's sub-pixel position and normalised to unit sum at that phase.
  - Flux and a constant background are fitted by weighted linear least squares (weights 1/`VARIANCE`) on pixels within 5 native pixels. Saturated (`OVERFLOW`, `NONLINEAR`) and masked pixels are left out. Pixels outside the ePSF support constrain only the background.
  - A fit is made when the usable pixels hold at least 2 % of the PSF. It is `flagged` when they hold less than 20 %, when more than 20 % of the PSF weight sits on masked pixels, or when a bright-source artifact bit (`GHOST`, `BLOOM`, `CROSSTALK`) is on a fitted pixel.
  - `saturated` is set when a saturated pixel lies within 2 px of the target.
- **Secondary measurement: the aperture sum** of ADR-0009, kept as `flux_ap_mjy` / `err_ap_mjy`. It becomes the primary value (`method: "aperture"`) when the fit is impossible. Saturation always flags an aperture value.
- **Errors.** A 3 % systematic floor is added in quadrature to every error, PSF and aperture alike.
- **meta.json.** Each photometry record gains these fields: `method` (`"psf"` | `"aperture"`), `saturated`, `npix`, `chi2`, `psf_weight`, `flux_ap_mjy` and `err_ap_mjy`. `flux_mjy` and `err_mjy` hold the primary measurement.

## Consequences

- **No aperture correction.** The fitted flux is the total flux under the ePSF normalisation used by the SPHEREx pipeline, so no aperture correction is needed.
- **Mildly saturated stars become measurable** (a few saturated core pixels).
- **Heavily saturated stars are only partly rescued.** With the 3 × 3 core saturated, a few per cent of the PSF is left in usable pixels. Those fits follow brightness changes but are flagged, and their absolute level depends on how good the ePSF is at 1.5–3.3 px. When nothing usable is left inside the ePSF support (±3.3 px), the measurement falls back to the flagged aperture value.
- **Blends.** A fit with a fixed position and a single source still absorbs light from neighbours within ~10″ (for example WISE 0855 next to a G = 16.3 star); `chi2` exposes this but nothing deblends yet.
- **Cost.** One 3.9 MB read per detector, ever, and a 2-parameter least-squares fit per exposure.

## Alternatives considered

- **photutils `PSFPhotometry` with `ImagePSF`:** more general (free positions, grouping), but much slower per exposure and harder to restrict to a pixel mask with a fixed position.
- **Reading the ePSF row per file:** unnecessary, since the table is identical across files of a detector. If a future calibration release changes it, the cache key must include that version.
- **An extended PSF wing model for saturated stars:** the ePSF stops at ±3.3 px and SPHEREx publishes no calibrated wing profile, so ring photometry beyond the core would give relative brightness only.
