# Validation on the five challenge targets

Date: 2026-11-14. Data: SPHEREx DR1 + QR3 Level 2 images through the pipeline at `ALGO_VERSION = 1`.
Scripts in this folder ran on the dev server against the cached sequences listed below.

| Target | Sequence id (fov) | Frames | Epochs |
|---|---|---|---|
| Barnard's Star | `48006f61db3e73e6ae51` (6′) | 358 | 6 |
| ε Indi B | `4928360c2e165743cd5f` (16′) | 458 | 5 |
| WISE 0855−0714 | `6147d624737df078b905` (6′) | 258 | 4 |
| RW Aur | `937c62641bab5fb6ba8f` (6′) | 335 | 3 |
| R Dra | `7ec6c7df80b210955dc4` (6′) | 2493 | 10 |
| Comparison field star, G = 13.9 | `b30a41090fe7c208eef5` (4′) | | |

## Summary

- **Motion is measured correctly.** Alignment holds to ≤ 0.27″ between epochs, and predicted positions match the data for Barnard's Star, ε Indi B and WISE 0855.
- **Wavelengths are right.** The T-dwarf absorption bands of ε Indi B land where expected.
- **Unsaturated photometry is stable to about 1 %** between epochs (1.004 / 1.000 / 1.017 / 0.993 for the field star).
- **Bright stars saturate** (about 0.1–0.2 Jy in D1–D3, about 1 Jy in D5–D6). Barnard's Star, RW Aur and R Dra have almost all photometry flagged, so their light curves need PSF fits to the unsaturated wings.

## WISE 0855−0714

- The preset (challenge position at ref epoch 2010.4, μ = −8118 / +677 mas/yr) is correct. It lands 0.2″ from AllWISE J085510.74−071442.5 (133.7947597, −7.2451456, epoch 2010.59, W1 = 16.23, W2 = 13.70).
- It looked missing because Gaia DR3 5756956644387831296 (G = 16.3, BP−RP = 0.72) sits 8″ from the predicted 2025.9 position. That star produces the D1–D3 flux and hides the dwarf from a simple "red-only" peak search.
- In D1–D3 the light-weighted centre sits on the star in all four epochs. In D5 + D6 it is pulled toward the dwarf by 8.0″, 4.9″, 4.2″ and 1.5″: the dwarf is approaching the star.
- A red-excess map (D5 + D6 S/N minus scaled D1–D3 S/N) detects the dwarf at S/N 6.7 / 10.2 / 8.2 / 7.0, within 0.6–1.9″ of the predicted position in each epoch. The fitted motion of −8.5″/yr in RA agrees with −8.1″/yr; the Dec term is within the noise of a blended source.
- After subtracting the star's extrapolated flux, the dwarf has about 0.46 mJy in D6 (AllWISE W2 predicts 0.55 mJy), about 0.29 mJy in D5 and about 0.01 mJy in D4: the 3.3 µm methane trough.
- CatWISE2020 (including the reject table) has no source moving faster than 0.5″/yr within 4′, so it does not carry this object.
- Figure: `wise0855_blend.png`.

## ε Indi B

- At 16′ both stars are in the field: B at the centre, A 403″ away, 13 px from the edge. A is saturated.
- Twelve field stars move ≤ 0.27″ between epochs with ~0.1″ scatter; the 2025-06-10 mini-epoch is worse (0.8″ in RA).
- B moved 5.95″ (+4.99″ E, −3.25″ N) in 1.39 yr. Gaia's motion predicts 6.51″ (+5.53, −3.43); the residual is −0.55″ / +0.18″.
- Methane absorption is obvious. Flux ratios for B, then the comparison field star in brackets: F(3.3 µm)/F(4.1 µm) = 0.18 (1.51); F(2.3 µm)/F(K) = 0.43 (0.82); F(1.7 µm)/F(H) = 0.80 (0.94). The 4 µm peak is brighter than J (54 vs 36 mJy).
- Figure: `eps_indi_spectrum.png`.

## Barnard's Star

- The three main epochs (108–117 frames each) match the proper motion to within 0.6″: residuals (−0.29, −0.05), (+0.46, +0.39), (−0.59, +0.17) arcsec.
- The 2025.75 mini-epoch (10 frames, 2 detectors) is off by +15.8″ / −19.9″ because of the empty hole at the saturated centre (issue a).
- All photometry is flagged as saturated. Figure: `barnard_core.png`.

## RW Aur and R Dra

- **RW Aur:** 315 of 333 points are flagged, with OVERFLOW in every detector (0.18–1.7 Jy). Ring photometry around the saturated centre (r = 3–6 px) shows D5 at 1.25 → 1.00 → 0.61 and D6 at 1.46 → 1.00 → 0.71, about a 2× fade at 4–5 µm over the year. The same method varies ≤ 15 % on the constant Barnard's Star, so the fade is likely real but not quotable yet.
- **R Dra:** all 2479 points are flagged. D3 and D4 median flux is 0 because the masked centre empties the aperture. A Lomb–Scargle peak at 254 d has power 0.04 (noise). Ring photometry works only in D1 at 3 epochs (0.88 / 1.00 / 1.48): clearly variable, but the period is not recoverable yet.

## Pipeline issues and fixes

| | Issue | Fix |
|---|---|---|
| a | Saturated centres become empty holes in the co-adds (Barnard's Star: a 6–7 px hole with zero coverage in D1–D4). Saturated pixels also carry SUR_ERROR / TRANSIENT / NONFUNC / PERSIST, which are masked, and bilinear resampling spreads each NaN. | Fill pixels masked in every frame from the masked values and mark them; resample with NaN-aware weights so holes do not grow. |
| b | Photometry of saturated stars is meaningless. | PSF fits to the unsaturated outer pixels using each file's EPSF; label saturated spectra and light curves in the UI until then. |
| c | Fluxes run about 5 % low (field star vs catalogues: J 0.94, H 0.97, Ks 0.94, W1 0.91, W2 0.99). | Aperture correction from the EPSF per detector instead of a Gaussian; photometry on co-adds needs r ≥ 3 px. |
| d | Error bars are about 2× too small (constant star: reduced χ² = 3.8). | Add a ~3 % systematic floor in quadrature. |
| e | Flagging marks 16–24 % of good points. HALO barely affects flux but is set on 18 % of points. | Make HALO informational and flag only when the masked fraction exceeds 0.2 (flag rate 24 → 18 % on the field star). |
| f | Mini-epochs of 2–18 frames on 1–2 detectors give noisy frames and outlying centres. | Merge epochs under ~20 frames into a neighbour less than 60 days away, or mark them partial and skip them in blink. |

## After fixes (ALGO_VERSION 2)

Changes ([ADR-0016](../../docs/adr/0016-epsf-psf-photometry.md) to [ADR-0019](../../docs/adr/0019-merge-small-epochs.md)):

- **ePSF photometry.** Photometry now fits the detector's ePSF and skips saturated and masked pixels, with a 3 % systematic error floor; the aperture value is kept as `flux_ap_mjy`.
- **Co-adds.** Resampling is NaN-aware, and pixels masked in every exposure are filled and flagged `FILLED` (bit 31).
- **Flag policy.** `HALO` is now informational and the masked-fraction threshold is 0.2.
- **Epochs.** Epochs under 20 exposures merge into a neighbour less than 60 days away; any still under 20 are marked `partial`.
- **WISE 0855 preset.** It is now anchored on AllWISE.

All six sequences were rebuilt on 2026-11-14 at each preset's field of view. No epoch ended up partial.

| Target | Sequence id (fov) | Exposures | Epochs (v1 → v2) | ePSF fits | Flagged (v1 → v2) |
|---|---|---|---|---|---|
| Barnard's Star | `de877ff9fee68388864b` (6′) | 358 | 6 → 3 | 90 | 100 % → 99.7 % |
| ε Indi B | `bfc97fb90227896b869f` (16′) | 458 | 5 → 4 | 458 | 23 % → 3.5 % |
| WISE 0855−0714 | `ef8bef4d524a3abc8839` (6′) | 258 | 4 → 3 | 258 | 16 % → 2.3 % |
| RW Aur | `1273eeb4145087270547` (6′) | 335 | 3 → 3 | 309 | 94 % → 55 % |
| R Dra | `158eda49942980c00885` (6′) | 2493 | 10 → 10 | 0 | 100 % → 100 % |
| Comparison field star, G = 13.9 | `be50f65dd2807dc67fb2` (4′) | 464 | → 4 | 459 | 24 % → 3 % |

### Flux scale

The comparison star (Gaia DR3 6412593435166417536), median SPHEREx flux over catalogue flux:

| Band | v2 (ePSF fit) | v1 (aperture) |
|---|---|---|
| 2MASS J | 1.002 | 0.94 |
| 2MASS H | 1.035 | 0.97 |
| 2MASS Ks | 1.020 | 0.94 |
| WISE W1 | 0.925 | 0.91 |
| WISE W2 | 1.076 | 0.99 |

The mean ratio is 1.01, against 0.95 in v1. The W1 and W2 differences are within the bandpass mismatch between broad WISE bands and SPHEREx channels.

### Error bars and flags

- **Error bars.** The comparison star's normalised light curve has reduced χ² = **1.2** against a constant (v1: 3.8). Its robust scatter is 3.6 % against a median quoted error of 3.4 %. Epoch levels are 1.000 / 1.000 / 1.009 / 1.000.
- **Masked fraction.** Points with more than 20 % of their weight masked deviate by 11.8 % (n = 14), while those with 5–20 % masked deviate by 2.1 % (n = 16). This supports the 0.2 threshold.
- **HALO.** Points with `HALO` deviate by 2.9 %, against 2.1 % without it.

### Barnard's Star

- **Centroids.** The three epochs now hold 118, 121 and 119 exposures, because the mini-epochs were merged. Residuals from the proper motion are (+0.36, +0.29), (+0.11, −0.06) and (−0.06, −0.27) arcsec. In v1 the three main epochs were within 0.6″ and the mini-epoch was 25″ off.
- **Co-add core.** Within 4 px of the star:
  - v1 had up to 49 NaN, zero-coverage pixels per epoch and detector, with NaN in 16 of 24 cells.
  - In v2 the zero-coverage pixels (up to 31) are filled and flagged `FILLED`: 209 pixels across the sequence.
  - At most one NaN pixel remains, in 5 of 12 D1–D4 cells. Those are pixels with no finite value in any exposure.
  - Figure: `barnard_core_v2.png`.
- **Photometry.** It stays flagged. Only 90 exposures leave at least 2 % of the ePSF in usable pixels, and none leave 20 %.

### RW Aur

ePSF fits, including flagged ones, give these per-detector medians of flux over the median at the same wavelength:

| Detector | 2025-09-01 | 2026-02-20 | 2026-09-02 |
|---|---|---|---|
| D1 | 0.71 | 1.00 | 1.26 |
| D2 | 0.76 | 1.10 | 1.03 |
| D3 | 0.97 | 1.07 | 0.99 |
| D4 | 0.97 | 1.00 | 1.00 |
| D5 | 1.13 | 1.00 | **0.50** |
| D6 | 1.47 | 1.00 | **0.63** |

- **Wing photometry agrees.** PSF-wing photometry of the aligned exposures (annulus 4–7 px, `wing_lightcurve.py`) gives D5 1.23 → 1.00 → 0.64 and D6 1.41 → 1.00 → 0.73.
- **Control.** On constant, saturated Barnard's Star the same method stays between 0.94 and 1.09, with a robust scatter of 6.9 %.
- **Result.** RW Aur faded by about 2× at 4–5 µm over the year while brightening at 0.75–1.1 µm.

### R Dra

- **ePSF photometry fails.** There are no ePSF fits at all: the saturated core extends beyond the ePSF's ±3.3 px support in every detector. Lomb–Scargle on the pipeline photometry gives 254 d at power 0.04, which is noise.
- **Wing photometry recovers the period** (`wing_lightcurve.py`, aligned exposures, expected ≈ 245 d):

| Annulus (px) | Usable exposures | Best period | Power |
|---|---|---|---|
| 3.5–6.5 | 322 | 252 d | 0.12 |
| 4–7 | 548 | 246 d | 0.16 |
| 5–8 | 1304 | 236 d | 0.25 |
| 6–9 | 1925 | 236 d | 0.34 |

The pipeline does not compute this measurement yet. Adding it as a relative "wing" flux for saturated stars is the next step for R Dra's light curve.

## ALGO_VERSION 3: ring photometry in the pipeline

Detectors in which the target saturates in at least half of the exposures now use one fixed ring per detector, 1 px outside the 95th percentile of the saturated-core radii and 3 px wide ([ADR-0020](../../docs/adr/0020-ring-photometry-for-saturated-stars.md)). The core is the blob of `OVERFLOW`, `NONLINEAR`, `TRANSIENT`, `SUR_ERROR` and `BLOOM` pixels around the source. `PERSIST` is left out: around Barnard's Star in D3 it reaches 16.5 px without being centred on the star. Ring fluxes are relative (`relative: true`).

The numbers below come from the pipeline's own `meta.json` photometry. Each unflagged value is divided by the median of its method and wavelength bin (log step 0.025), as the web client does (`pipeline_lightcurves.py`).

| Target | Sequence id (FOV) | Photometry |
|---|---|---|
| Barnard's Star | `4261e28d6e0c3cb6df94` (6′) | rings in all six detectors (4–10 px) |
| ε Indi B | `d651cb1431ed20027191` (16′) | ePSF in all six detectors |
| WISE 0855−0714 | `e3fc10babf437ede1d93` (6′) | ePSF in all six detectors |
| RW Aur | `cb983e7485da07d622a4` (6′) | rings in all six detectors (3–8 px) |
| R Dra | `c16e0057355ff8f04f32` (6′) | rings in all six detectors (9–15 px) |
| Comparison star, G = 13.9 | `51edd709fdbf3d9eedab` (4′) | ePSF in all six detectors, robust scatter 3.6 % |

### R Dra

Lomb–Scargle on 2,452 ring measurements gives a best period of **236 d** (power 0.29; expected ≈ 245 d, wing script 236–252 d). The D1 medians follow a full pulsation cycle: 1.45 → 1.05 → 0.51 → 0.67 between 2025-04 and 2025-10, then 0.95 → 1.34 in 2026-07/08.

### RW Aur

| Detector | 2025-09-01 | 2026-02-20 | 2026-09-02 |
|---|---|---|---|
| D1 | 0.84 | 1.00 | 1.24 |
| D2 | 0.94 | 1.03 | 1.03 |
| D3 | 0.96 | 1.00 | 1.00 |
| D4 | 1.05 | 1.00 | 0.89 |
| D5 | 1.22 | 1.00 | **0.65** |
| D6 | 1.38 | 1.00 | **0.74** |

The 4–5 µm fade and the 0.75–1.1 µm brightening of version 2 and of the wing script are reproduced.

### Barnard's Star (control, constant)

| Detector | 2025-08-15 | 2026-03-11 | 2026-08-16 |
|---|---|---|---|
| D1 | 0.99 | 1.00 | 1.01 |
| D2 | 0.95 | 0.90 | 1.14 |
| D3 | 0.98 | 0.95 | 1.11 |
| D4 | 0.99 | 1.01 | 1.00 |
| D5 | 1.00 | 1.01 | 0.98 |
| D6 | 1.03 | 0.99 | 1.01 |
| all | 1.00 | 1.00 | 1.00 |

- **Flat** to within 1–4 % in D1, D4, D5 and D6 and over all detectors; robust per-exposure scatter is 6.4 %.
- **D2 and D3 drift by 10–14 %.** Sigma-clipping field stars in each annulus did not change this, so it most likely comes from the wing profile changing with the position on the detector. Ring amplitudes below ~15 % are not trustworthy.
- **What did not work.**
  - Including `PERSIST` in the core inflated the D3 ring to 14–16 px and gave 0.38 → 1.00 → 1.69.
  - Sigma-clipping moved RW Aur's D2 curve to 0.80 / 1.17 / 1.01.
  - Both were dropped.
