# ADR-0020: Measure saturated stars in a fixed ring outside the core, as relative fluxes

- **Status:** Accepted
- **Date:** 2026-11-14
- **Extends:** [ADR-0016](0016-epsf-psf-photometry.md)

## Context

After ALGO_VERSION 2, two of the five challenge targets still had no usable light curve:

- **R Draconis (K ≈ 0)** saturates beyond the ePSF's ±3.3 px support in every detector, so it got 0 ePSF fits out of 2,479 exposures and every point was a flagged aperture value. A Lomb–Scargle periodogram gave noise (254 d, power 0.04).
- **Barnard's Star and RW Aur** got ePSF fits on their few unsaturated pixels, but almost all were flagged because less than 20 % of the PSF was usable.

The validation script `analysis/validation/wing_lightcurve.py` showed that the flux in an annulus outside the saturated core, divided by the median of its wavelength bin, recovers R Dra's period (236–252 d for annuli between 3.5 and 9 px; expected ≈ 245 d). The star's wings scale with its total flux, and the wavelength-bin normalisation cancels both the colour and the ring's share of the PSF.

A diagnostic on Barnard's Star showed what sets the size of the corrupted core. `OVERFLOW`/`NONLINEAR` pixels reach only 1.5–2.2 px, `TRANSIENT`, `SUR_ERROR` and `BLOOM` around the star reach 4–6 px, and `PERSIST` blobs from earlier exposures reach up to 16.5 px in D3, but they are not centred on the star.

## Decision

- **Per exposure** (`photometry.radial_profile`): keep background-subtracted sums, variances and pixel counts in 1-px annuli out to 16 px, plus the saturated-core radius. The core is the blob of `OVERFLOW`, `NONLINEAR`, `TRANSIENT`, `SUR_ERROR` and `BLOOM` pixels touching the source (`flags.RING_CORE`, without `PERSIST`). Pixels with any of `flags.RING_BAD` (image mask, saturation, bloom) are left out of the sums. The local background is the median of usable pixels at 16–21 px, or the frame background when that annulus does not fit.
- **Per detector** (`pipeline.assemble.apply_rings`): switch to rings when at least 50 % of the exposures are saturated (or failed with a bad-pixel blob over the source). Use one ring for every exposure of that detector: inner radius 1 px outside the 95th percentile of the core radii, 3 px wide, capped at 16 px. Fill masked ring pixels with their annulus mean. Drop the photometry of exposures whose ring is cut by the cutout edge or has under 60 % usable pixels, rather than mixing methods within a detector.
- **Records** get `method: "ring"`, `relative: true`, `ring_r_in`, `ring_r_out` and `ab_mag: null`. Every other record gets `relative: false`. The rings chosen are listed in `meta.processing.rings`. The algorithm version becomes 3.

## Consequences

- **The variable targets work in the pipeline itself.**
  - R Dra: rings at 9–15 px in all six detectors, and a Lomb–Scargle period of 236 d (power 0.29).
  - RW Aur: D5 1.22 → 1.00 → 0.65, D6 1.38 → 1.00 → 0.74, D1 0.84 → 1.00 → 1.24.
  - Barnard's Star (constant): flat to within 1–4 % in four detectors.
- **Ring fluxes are relative.** They cannot go on an absolute spectrum, and clients must check `relative`. A saturated star therefore has a light curve but no calibrated spectrum.
- **About 10–15 % systematics per detector.** The ring samples the wing profile at 4–15 px, which changes with the position on the detector, so constant Barnard's Star shows per-epoch offsets of up to 14 % in D2 and D3. Amplitudes below ~15 % are not trustworthy with this method.
- **A fixed ring per detector** keeps fluxes comparable while the core grows and shrinks with the star's brightness. The cost is that exposures with an unusually large core put some masked pixels at the ring's inner edge.
- **Cost:** an extra ~1–2 ms per exposure.

## Alternatives considered

- **A ring chosen per exposure from that exposure's core:** the ring moves outward when the star brightens and inward when it fades, so the measured amplitude shrinks.
- **Including `PERSIST` in the core:** it inflated Barnard's Star's D3 core to 12.5 px (95th percentile) and broke its light curve (0.38 → 1.00 → 1.69).
- **Sigma-clipping field stars inside each annulus:** tried, did not reduce Barnard's D2/D3 offsets, and moved RW Aur's D2 curve (0.94/1.03/1.03 → 0.80/1.17/1.01), so it was dropped.
- **PSF fits to the wings with a wider PSF model:** the ePSF in the files stops at ±3.3 px; a wing model would need its own calibration.
