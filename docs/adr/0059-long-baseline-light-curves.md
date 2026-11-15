# ADR-0059: Measure 2MASS, AllWISE and NEOWISE frames like the target and tie SPHEREx to them with field stars

- **Status:** Accepted (extends 0020, 0023)
- **Date:** 2026-11-15

## Context

SPHEREx covers 2025–2026, which is too short for long-period variables (Miras: 150–600 d) and for dimming events
that last years. 2MASS (1997–2001), AllWISE (2010) and NEOWISE (2014–2024) cover the same position from 1997 on.
ADR-0023 resamples those frames onto the sequence grid for display. For photometry that is not enough:

- The catalogues (AllWISE / NEOWISE single-exposure tables, CatWISE2020) have no measurement for an arbitrary
  clicked position, and their pipelines do not use the saturated star's PSF wings.
- Resampled images lose the frame's own pixel mask and noise.
- The bright stars this matters most for (R Dra: W1 ≈ 2, Ks 2.4) saturate every WISE and 2MASS frame.
- SPHEREx spectra and WISE broad bands are on different scales: AB per-channel flux densities against Vega W1/W2
  magnitudes, with each band's own response. SPHEREx's absolute calibration has not been checked against WISE.

## Decision

1. **Frames at native resolution.** For one position, `spherex_core.longbaseline` reads a window around the target
   from every frame that covers it: 2MASS Atlas images, AllWISE Atlas tiles, and the NEOWISE L1b frames (thinned to
   12 per visit), each with the frame's MAGZP and, for NEOWISE, its mask. The window has a 150″ radius around the
   target's 2018 position (`field_radius`, up to 360″), widened for fast movers. Windows are cached as npz
   files.
2. **Per-frame photometry.** The position is moved to each frame's MJD with its proper motion (from the request or
   a Gaia DR3 counterpart within 3″), then measured in one of two ways:
   - **Aperture:** the surveys' own standard apertures (WISE 8.25″, 2MASS 4″) with a sigma-clipped annulus
     background, corrected to total flux with a measured enclosed-energy curve. Bad pixels inside take the mean
     of their radius.
   - **Ring (saturated cores):** when at least half the frames saturate, one fixed ring per survey and band,
     placed 1 px outside the 95th percentile of the core radii. The core is taken from the NEOWISE saturation mask
     bits 10–18, or NaN pixels that no bad-pixel bit explains (L1b sets dead and noisy pixels to NaN as well;
     treating those as saturation sent half the faint field stars to rings). AllWISE tiles have no mask, so an
     AllWISE ring starts no closer than the NEOWISE ring of the band. The ring flux is scaled by the curve's
     ring fraction (ADR-0020's ring method on the WISE PSF).

   Frames are combined per NEOWISE visit, per 2MASS night and once for AllWISE with a 3σ-clipped weighted mean.
   Each point's error is the larger of the formal and scatter errors, plus a floor of 1.5% (aperture) or 3% (ring).
3. **Enclosed-energy curves** for NEOWISE, AllWISE and 2MASS come from
   `analysis/validation/long_baseline_calibration.py`. Isolated, unsaturated stars on the validation fields give
   their background-subtracted growth curves, divided by each star's catalogue flux in that frame's DN (AllWISE
   `w*mpro`, the 2MASS PSC), minus the expected flux of faint field sources. The curves are frozen in
   `instruments.py`. Aperture corrections: NEOWISE W1/W2 0.167/0.207 mag (star-to-star scatter 0.005/0.008),
   AllWISE 0.270/0.323, 2MASS 0.050.
4. **Zero points from the field's own stars.** Up to 16 calibration stars within `field_radius` − 10″ are used:
   isolated, W1 9–14, without contamination flags (when the reference is AllWISE), and not saturated in
   NEOWISE or SPHEREx. Each is measured exactly like the target on the same frames and in every
   SPHEREx exposure.
   - **Frames → catalogue.** The median of (catalogue − ours) replaces the default correction when at least 4
     stars agree. The catalogue is CatWISE2020 for WISE (AllWISE from a VizieR mirror when IRSA fails) and the
     2MASS PSC for 2MASS.
   - **SPHEREx → archive.** Synthetic W1/W2 (and J/H/Ks) of each star's all-epoch spectrum are compared against
     its calibrated NEOWISE mean (or the PSC), using only stars constant in both (robust scatter < 0.05 mag).
     The median offset is applied to the target's synthetic points when at least 3 stars are left. Otherwise the
     validation-field default is used.

   Both offsets are reported with their robust scatter, the star count and the star list.
5. **Synthetic bands.** Per SPHEREx time bin (epochs split into ≤ 15-day chunks), the binned spectrum is
   integrated over the band:

       ⟨Fν⟩ = ∫ Fν R/λ dλ / ∫ R/λ dλ

   This uses the SVO W1/W2/J/H/Ks responses, log-flux interpolation and power-law extrapolation past 5 µm (as in
   `spectral.synphot`), and needs ≥ 75% of the band's photon weight covered. Errors come from the Jacobian of the
   band flux with respect to every spectral bin, with a 2% floor. Saturated targets (ring photometry in SPHEREx)
   give relative synthetic points, with the zero point fitted. The six SPHEREx detector bands D1–D6 stay as
   SPHEREx-only narrow bands (relative to the all-epoch spectrum).
6. **One light curve per band.** Points carry `group`:
   - the band itself, for points on one calibrated scale;
   - `"<band>:<survey>-ring"` or `"<band>:<survey>-relative"`, for segments whose zero point is fitted.

   `mag_anchored` moves fitted groups onto the band's reference group: the calibrated one, else the largest ring
   group. Points use Vega magnitudes for J/H/Ks/W1/W2 and AB for D1–D6.
7. **Multiband period analysis** (`model.py`). A weighted least-squares Lomb–Scargle over W1, W2 and D1–D6
   (VanderPlas & Ivezić 2015) is run on a grid from 50 d to baseline/1.5 with 10 samples per peak. Each group gets
   a free offset and each band its own sinusoids. The power is the fractional χ² reduction. The FAP is the
   F-test p-value times the number of independent frequencies, and σ_P comes from the χ² curvature after
   rescaling χ²/dof to 1.

   A period is significant when FAP < 10⁻³, the power is ≥ 0.3 and the period is not a NEOWISE cadence alias
   (within half the frequency resolution of 365.25/k d, k = 1–7: the scan direction alternates every visit, so a
   neighbour's spikes, ghosts and latents repeat yearly). The final model uses 2 harmonics; other bands get their
   own sinusoid at the best frequency.

   From the residuals:
   - **Trends:** a slope per band over ≥ 3 yr.
   - **Era means:** one mean per survey.
   - **Events:** two consecutive archival visits, or three SPHEREx bins, beyond max(3σ, 0.1 mag), or one point
     confirmed within 5 d by an independent measurement. SPHEREx bands never confirm each other.

   A rule-based classification follows: constant, variable (aperiodic), periodic or long-period variable.
   Variable means χ²/dof ≥ 5 and a 5–95% range ≥ max(0.1 mag, 5 median errors) in one band of W1/W2, the
   other band not flat, and not the scan pattern (a cadence alias as best period at power ≥ 0.5).
8. **Field-wide search.** Up to 150 CatWISE2020 sources (W1 ≤ 15) within `radius_arcsec` (150–360″) are
   measured on the same frames. NEOWISE points are normalised by each visit's ensemble zero point. Contaminated
   sources are measured but kept out of the summary: AllWISE W1/W2 flags (halo, spike, ghost, latent), entries
   on a moving target's track, and apertures whose median W1 is > 0.5 mag off the catalogue (blends). SPHEREx adds relative
   aperture points on the epoch co-adds. Every source runs through the same model, and the field is ranked by a
   score (log χ²/dof + 2 × range + periodic bonus).
9. **Jobs and interfaces.** Both run as field jobs (`spherex_core.field.jobs`, kinds `l` and `w`, deduplicated
   per request and result version, cancelled 60 s after the last poll). The endpoints are:
   - `POST /api/sequences/{id}/long-baseline`
   - `POST /api/sequences/{id}/long-period-search`
   - `GET|DELETE /api/long-baseline/{job}`
   - `GET /api/long-baseline/{job}/lightcurve.csv`

   The MCP tools are `long_baseline_lightcurve` and `long_period_search`, and `scripts/long_baseline.py` runs it
   from the command line.

## Consequences

- Any position inside a built sequence gets a 1997–2026 light curve in one to three minutes. The first run reads
  0.5–0.8 GB of WISE frames by S3 range reads. Later runs reuse the cached windows and take 10–70 s.
  Validation figures are in docs/methods.md, "Validation".
- Saturated stars get light curves at all: rings in every archival survey and relative SPHEREx points. Their
  absolute scale is only as good as the enclosed-energy curve. Rings holding under 0.5% of the PSF are marked
  `scale_uncertain`; they can be off by a magnitude or more. The analysis fits their zero points, so periods
  and amplitudes do not depend on it.
- How well SPHEREx sits on the WISE scale depends on the field's calibration stars. Fields with fewer than three
  usable constant stars (bright-star haloes, high latitude within 150″) fall back to the default offsets, and the
  result says so. The defaults are the medians over five validation fields, which scatter by 0.01–0.02 mag.
- Each archival survey is in a different era, so a constant offset between surveys and a real change in
  brightness cannot be separated within one band unless the zero points are calibrated. Era means are reported
  with that caveat.
- IRSA outages: the catalogues fall back to VizieR mirrors (AllWISE, 2MASS PSC), then to Gaia DR3 without
  WISE magnitudes (then frame zero points are skipped). Finding new archival frames still needs IRSA's CAOM
  search; image lists are cached per field for a week.

## Alternatives considered

- **Catalogue photometry** (AllWISE / NEOWISE single-exposure source tables): no value at arbitrary positions,
  saturated stars badly biased, and no common treatment with the target's other surveys.
- **The resampled archival epochs of ADR-0023:** the frame mask and noise are lost, and the PSF is broadened by
  the resampling.
- **PSF fitting on the frames:** better for faint blended sources, but needs a per-frame PSF model of each survey,
  and saturated stars would still need rings. The standard apertures match the catalogues' calibration directly.
- **Converting SPHEREx to WISE with the nominal calibrations only:** untested at the few-per-cent level.
  Cross-calibrating on field stars measures the offset and reports its scatter.
- **Separate single-band Lomb–Scargle periodograms:** they waste the shared phase across bands and the fitted
  zero points of ring and relative segments.
