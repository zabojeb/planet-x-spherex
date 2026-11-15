# Methods

How a sequence is built, with the parameters the code uses today, what we have validated, and what we know is still weak. The reasoning behind each choice is in the [ADRs](adr/README.md); the data themselves are described in [data.md](data.md).

## Inputs

A request gives a target and processing options:

| Parameter | Default | Meaning |
|---|---|---|
| `ra`, `dec` | — | ICRS position in degrees at `ref_epoch` |
| `pmra`, `pmdec` | 0 | proper motion, mas/yr (`pmra` includes cos δ) |
| `ref_epoch` | 2000.0 | Julian year of the position |
| `fov_arcmin` | 6 | field of view, 1–30 arcmin |
| `gap_days` | 20 | a gap longer than this starts a new epoch |
| `max_epoch_days` | 45 | an epoch never spans more than this |

## Pipeline

### 1. Search

Propagate the target to 2026.0 and query IRSA TAP for every `science` image whose footprint contains that position in the configured collections. Keep one image per (observation id, detector), preferring DR1 over QR3 ([ADR-0003](adr/0003-tap-image-discovery.md)). Results are cached for 12 hours.

### 2. Output grid

Centre the grid on the target's position at the median MJD of its images. TAN projection, north up, east left, 6.15″ pixels, n = ceil(FOV / 6.15″) made odd (minimum 9); 6′ gives 59 × 59 pixels ([ADR-0006](adr/0006-common-output-grid.md)).

### 3. Read cutouts

For each image, read a square of 2h + 1 native pixels around the grid centre, with h = ceil(n / 2 × 1.42) + 3 (45 for a 6′ field): `IMAGE`, `FLAGS` and `VARIANCE`.

- **Where from.** First IRSA's cutout service, which cuts the window on its side: one request, ~160 KB at 6′, with at most 8 requests open per build. When no request slot is free, or the service is busy, slow or answers with something unusable, the image is read from S3 with HTTP range requests (`FLAGS` RICE tiles decoded on our side). Both give the same pixels ([ADR-0002](adr/0002-s3-range-reads.md), [ADR-0038](adr/0038-irsa-cutouts.md)).
- **What is kept.** Each file's rows are cached in one chunk, with the columns of a 30′ field, so a wider or narrower field later reads only rows it does not hold yet. `VARIANCE` is read only within 22 px of the target, the farthest any measurement looks; elsewhere it is NaN ([ADR-0037](adr/0037-per-file-chunk-cache.md)).
- **How.** The images are read in batches of 2–16 on the process pool, each worker reading and processing its own (step 4), with 64 images in flight over the whole build. The epoch the viewer opens on is read first, and a preview of the finished epochs is published while the rest is read ([ADR-0039](adr/0039-batched-fetch-and-previews.md)).
- **Tables.** The detector's wavelength table (`WCS-WAVE`) and effective-PSF table (`EPSF`, 441 zones) are read once per detector from S3 and cached ([ADR-0016](adr/0016-epsf-psf-photometry.md)).

### 4. Per exposure (process pool)

1. **Mask.** Set pixels with any image-mask bit to NaN: `TRANSIENT`, `SUR_ERROR`, `NONFUNC`, `MISSING_DATA`, `HOT`, `COLD`, `PERSIST`, `OUTLIER`, `STREAK`, `SNOWBALL` ([ADR-0018](adr/0018-photometry-flag-policy.md)).
2. **Background.** Subtract the sigma-clipped median (3σ, 5 iterations) of the remaining pixels; keep the clipped standard deviation as the exposure's noise `rms`.
3. **Align.** Map grid pixels to cutout pixels through the exposure WCS (SIP included) evaluated on a 9 × 9 control grid and a cubic spline. Resample bilinearly with NaN-aware weights (data × valid and valid resampled separately, output NaN below half valid weight) so masked areas do not grow; resample flags nearest-neighbour. The exposure is also aligned without masking (`aligned_raw`) for filling co-add holes ([ADR-0017](adr/0017-nan-aware-resampling-and-filled-coadds.md)).
4. **Photometry** at the target's position at `MJD-AVG` ([ADR-0010](adr/0010-proper-motion-aware-targets.md), [ADR-0016](adr/0016-epsf-psf-photometry.md)):
   - **ePSF fit (primary, `method: "psf"`).** The ePSF of the detector zone nearest the cutout centre (33 × 33, oversampled 5×) is evaluated with a cubic spline at the target's sub-pixel position and normalised to unit sum at that phase. Flux and a constant background are fitted by weighted linear least squares (weights 1/`VARIANCE`) on pixels within 5 px that are neither saturated (`OVERFLOW`, `NONLINEAR`) nor masked. A fit is made if the usable pixels hold ≥ 2 % of the PSF; `npix`, reduced `chi2` and `psf_weight` (PSF fraction in the fitted pixels) are reported; `saturated` is set when a saturated pixel lies within 2 px.
   - **Aperture (secondary, `flux_ap_mjy`).** r = max(1.5 px, FWHM) with FWHM = `PSF_FWHM` / √`OMEGA_MEDIAN`; background: sigma-clipped (3σ) median in an annulus from max(4 px, 2.5 × FWHM) to 3 px further out; exact-overlap sum with Gaussian aperture correction 1/(1 − exp(−r²/2σ²)). It becomes the primary value (`method: "aperture"`) when no fit is possible.
   - **Radial profile for rings.** Background-subtracted sums, variances and pixel counts in 1-px annuli out to 16 px (local background: median of usable pixels at 16–21 px, else the frame background), and the radius of the saturated core: the blob of `OVERFLOW`/`NONLINEAR`/`TRANSIENT`/`SUR_ERROR`/`BLOOM` pixels touching the source. `PERSIST` is left out of the core because persistence blobs from earlier exposures are not centred on the star (up to ~200 px around Barnard's Star in D3) ([ADR-0020](adr/0020-ring-photometry-for-saturated-stars.md)).
   - **Errors.** From `VARIANCE`, with a 3 % systematic floor added in quadrature.
   - **Units and wavelength.** MJy/sr × `OMEGA_MEDIAN` → mJy, AB magnitude; wavelength and bandwidth from the detector's `WCS-WAVE` table at the target pixel, bilinear in raw pixel coordinates.
   - **`flagged`.** For a fit: usable PSF weight < 20 %, more than 20 % of the PSF weight on masked pixels, or `GHOST`/`BLOOM`/`CROSSTALK` on a fitted pixel. For an aperture value: any photometry-bad bit in the aperture (image mask, `OVERFLOW`, `NONLINEAR`, `GHOST`, `BLOOM`, `CROSSTALK`; `HALO` is informational) or more than 20 % of it masked ([ADR-0018](adr/0018-photometry-flag-policy.md)).

### 5. Ring photometry for saturated detectors

A detector switches to rings when at least half of its exposures are saturated (or failed with a bad-pixel blob over the source). All its exposures then use one ring, from 1 px outside the 95th percentile of the saturated-core radii, 3 px wide (capped at 16 px); masked pixels in the ring are filled with their annulus mean, and exposures whose ring is cut by the cutout edge or has under 60 % usable pixels lose their photometry instead of mixing methods. These records have `method: "ring"`, `relative: true`, `ring_r_in`, `ring_r_out` and no AB magnitude: the flux is the ring's share of the light, comparable only within one detector and wavelength. Divided by the median of their wavelength bin, as the web client does, they give a relative light curve. The chosen rings are in `processing.rings` ([ADR-0020](adr/0020-ring-photometry-for-saturated-stars.md)).

### 6. Epochs and co-adds

Sort exposures by time; start a new epoch after a gap > `gap_days` or when the epoch spans `max_epoch_days`. Then fold every epoch with fewer than 20 exposures into its nearest neighbour if they are less than 60 days apart; epochs still below 20 exposures are kept with `partial: true` ([ADR-0019](adr/0019-merge-small-epochs.md)). For each epoch and detector: with ≥ 4 exposures, reject pixels more than 5 × max(1.4826 × MAD, median `rms`) from the per-pixel median; then take the mean weighted by 1/`rms`² per exposure. Pixels that no clean exposure covered (coverage 0, typically saturated star cores whose pixels also carry masked bits) are filled with the median of the unmasked values and get pipeline flag bit 31 `FILLED`. Also record the number of contributing exposures per pixel and the bitwise OR of flags ([ADR-0007](adr/0007-epochs-and-coadds.md), [ADR-0017](adr/0017-nan-aware-resampling-and-filled-coadds.md)).

### 7. Outputs

`coadds.f32`, `coverage.u16`, `flags.u32` (FLAGS bits plus pipeline bit 31 `FILLED`), `exposures.f16` (if ≤ 80 million voxels) and `meta.json` with the grid, epochs (dates, `partial`, exposures, wavelength range and filled pixels per detector), every exposure (date, detector, wavelength, noise, flag counts, predicted target pixel), photometry (`method`, `flux_mjy`, `err_mjy`, `saturated`, `npix`, `chi2`, `psf_weight`, `flux_ap_mjy`, `err_ap_mjy`, `relative`, `ring_r_in`, `ring_r_out`, `flagged`, …), flag definitions (`pipeline: true` for bit 31), the processing constants (`processing`), the predicted target track per epoch, and timing and transfer statistics ([ADR-0005](adr/0005-server-side-alignment-client-side-display.md)).

## Measured performance

From the dev server in Pennsylvania (32 vCPU), 6′ field:

| Target | Exposures used | Epochs | Requests | Transferred | Build time (TAP query) |
|---|---|---|---|---|---|
| Barnard's Star | 358 / 358 | 6 | 2,190 | 718 MB | 37.7 s (12.3 s) |
| ε Indi B | 458 / 458 | 5 | 2,748 | 996 MB | 39.4 s (2.7 s) |
| WISE 0855−0714 | 258 / 258 | 4 | 1,548 | 555 MB | 29.2 s (8.7 s) |
| RW Aurigae | 335 / 335 | 3 | 2,010 | 692 MB | 41.0 s (14.7 s) |

These first builds ran before the process-pool change ([ADR-0013](adr/0013-process-pool-and-slim-headers.md)). Rebuilding Barnard's Star from cached cutouts took 19.2 s before that change and 5.2 s after it.

ALGO_VERSION 2 rebuilds at each preset's field of view (cutouts cached except WISE 0855, whose preset moved by 0.2″; the first build also reads the six ePSF tables, 3.9 MB each):

| Target (FOV) | Exposures | Epochs | Build time |
|---|---|---|---|
| Barnard's Star (6′) | 358 | 3 | 6.1 s |
| ε Indi B (16′) | 458 | 4 | 4.0 s |
| WISE 0855−0714 (6′) | 258 | 3 | 24.7 s from S3 (555 MB, TAP 13.0 s) |
| RW Aurigae (6′) | 335 | 3 | 2.7 s |
| R Draconis (6′) | 2,493 | 10 | 16.4 s |

With per-file chunks, batched reading on the pool and IRSA cutouts first (ADR-0037 to ADR-0039), Barnard's Star at 6′ (358 exposures, 583 MB, dev server, 29 workers, 128 exposures in flight, TAP query cached; the default of 64 is within the same spread, ADR-0039):

| Build | Before (ADR-0037) | Now |
|---|---|---|
| Cold: first picture (opening epoch, 118 exposures) | only the full result | 3.0–3.5 s in about half the runs, up to 7 s when one of its reads is slow |
| Cold: full result | 19.8–20.4 s | 6.6–10.4 s, median 7.9 s |
| Every row already cached | 4.4 s | ~1.0 s |

## Validation so far

- **Reader correctness.** For a DR1 file, the range reader's `IMAGE`, `VARIANCE` and `FLAGS` cutouts are identical to astropy's `section` reads (`backend/tests/test_reader_network.py`).
- **Wavelengths.** Our `WCS-WAVE` interpolation equals astropy's FITS `-TAB` result to a relative 10⁻⁶ at four test pixels, once the SIP keywords are removed from the header (see the gotcha in [data.md](data.md#wcs-wave-wavelength-per-pixel)).
- **Astrometry and proper motion.** Flux-weighted centroids of Barnard's Star per visit, measured on cutouts from S3, match the propagated positions to better than 1″ (about 0.15 pixel) on the three full visits:

  | Epoch | Exposures | ΔRA·cos δ measured | predicted | ΔDec measured | predicted |
  |---|---|---|---|---|---|
  | 2025.653 | 108 | −19.9″ | −20.6″ | 266.0″ | 265.8″ |
  | 2026.207 | 113 | −20.5″ | −21.0″ | 271.8″ | 271.6″ |
  | 2026.637 | 117 | −21.9″ | −21.4″ | 276.0″ | 276.0″ |

  Offsets are from the J2000 position. The star moved 10″ (1.6 pixels) north between the first and last full visits.
- **Validation of ALGO_VERSION 1 and 2** on all five targets, with the scripts and numbers, is in [analysis/validation/REPORT.md](../analysis/validation/REPORT.md). After the version-2 fixes:
  - a G = 13.9 field star matches 2MASS J/H/Ks at 1.00/1.04/1.02 and WISE W1/W2 at 0.93/1.08 (version 1: 0.94/0.97/0.94 and 0.91/0.99);
  - its normalised light curve has reduced χ² = 1.2 against a constant (version 1: 3.8) and only 3 % of points are flagged (version 1: 24 %);
  - Barnard's Star has 3 epochs whose centroids follow its proper motion to ≤ 0.36″, and its co-add cores are filled (≤ 1 NaN pixel instead of up to 49);
  - RW Aur fades by about 2× at 4–5 µm (D5 1.13 → 1.00 → 0.50, D6 1.47 → 1.00 → 0.63) while brightening at 0.75–1.1 µm, in ePSF fits and independently in PSF-wing photometry.
- **ALGO_VERSION 3 (ring photometry)**, from the pipeline's own photometry normalised per wavelength bin (`analysis/validation/pipeline_lightcurves.py`):
  - R Draconis: every detector uses rings (9–15 px); Lomb–Scargle on 2,452 measurements gives a period of 236 d (power 0.29; expected ≈ 245 d).
  - RW Aur: D5 1.22 → 1.00 → 0.65 and D6 1.38 → 1.00 → 0.74 (fading), D1 0.84 → 1.00 → 1.24 (brightening) over Sep 2025 – Sep 2026.
  - Barnard's Star (constant): per-epoch medians within 1–4 % in D1, D4, D5 and D6 and 1.00 / 1.00 / 1.00 over all detectors, but up to 14 % off in D2 and D3 (see limitations).
  - ε Indi B, WISE 0855 and the comparison star are unsaturated and keep ePSF photometry in every detector.
- **Unit tests** cover coordinate parsing, proper-motion propagation, epoch grouping and merging, outlier rejection and hole filling in co-adds, NaN-aware resampling, grid centring, wavelength interpolation, FITS header parsing, flags, ePSF photometry with saturated cores, ring photometry (flux scaling through a growing saturated core, one ring per detector), catalog failures mapped to HTTP 502, the result format and the HTTP API; network tests compare the reader's cutouts, wavelengths and ePSF table with astropy (`backend/tests/`).

## Known limitations

- **Heavily saturated stars.** The ePSF covers only ±3.3 px and holds ~97 % of the flux in the central 3 × 3 pixels, so stars whose saturated core is larger than that (R Draconis, Barnard's Star, RW Aur) get ring photometry instead: relative fluxes only, no calibrated spectrum. Ring fluxes depend on the wing profile at 4–15 px, which changes with the detector position, so constant stars scatter by ~5 % per exposure and per-epoch medians can be off by up to ~14 % in some detectors (Barnard's Star D2/D3); amplitudes below ~15 % are not reliable with this method.
- **Blends.** With 6.15″ pixels, neighbours within about 10″ contaminate both the ePSF fit (single source, fixed position) and the aperture. WISE 0855 passes 8″ from a G = 16.3 star in 2025–26, which dominates D1–D4 at its position.
- **Absolute flux scale** rests on the ePSF normalisation; one field star agrees with 2MASS and WISE to within 8 %, more stars are needed for a proper calibration check.
- **Mixed wavelengths in co-adds.** A detector co-add averages up to 17 channels; epochs that miss channels can change the apparent brightness of strongly coloured sources.
- **Filled co-add pixels** (`FILLED`) come from flagged pixels: fine for display, not for measurement.
- **Parallax ignored** in position propagation (0.55″ for Barnard's Star).
- **Fixed flag policy;** users can display any flag but cannot yet change which bits are masked.
- **Running jobs are not persistent;** a restart loses in-flight builds (finished results stay on disk).

## Analyses on a built sequence

- **Photometry of any position** (ADR-0027): the pipeline's measurements on the cached native cutouts, at a new position with optional proper motion, PSF zone nearest to that position. Re-measuring the targets reproduces the build's fluxes exactly for Barnard's Star (357 of 358 exposures) and with a median difference of zero for ε Indi B (maximum 6 %, expected where the PSF zone nearest the source differs from the one nearest the cutout centre that the build used). 11–14 s for 358–458 exposures in a cold script, no downloads.
- **Proper motion** (ADR-0028): flux-weighted centroids per epoch and detector, a straight-line fit with per-detector zero points. ε Indi B 3628 ± 305 / −2526 ± 260 mas/yr (Gaia 3982 / −2467); Barnard's Star −1316 ± 325 / 9557 ± 385 (Gaia −802 / 10362); three static Gaia stars −98 … +57 mas/yr. Precision is about 300 mas/yr with 3–4 visits; per-detector centroids of bright, saturated stars scatter by ~0.5″.
- **Periodograms** (ADR-0029): Lomb–Scargle on the client-identical relative light curve. R Dra 235.7 d (standard) and 246.9 d (multiband) against ~245 d; RW Aur's three visits give the visit spacing (131 d), not a period.

### Calibration checks (2026-11-14)

- **Wavelengths.** WCS-WAVE (the 9 × 9 lookup we use) against the EPSF table's CWAVE at all 441 zone centres: median offset +0.02–0.04 % (D1–D4) and +0.14–0.16 % (D5–D6), maximum 0.19 % — a fifth of a resolution element or less. Bandwidths agree in the median (−0.04 %) but differ by up to 4 % (95th percentile) in some zones. No change needed.
- **Pixel solid angle.** The pixel area from the WCS (with SIP) is 0.03 % below OMEGA_MEDIAN at the detector centre and 0.9–1.3 % away from it at the corners and edges, so using OMEGA_MEDIAN for every pixel biases fluxes by up to ~1.3 % near the edges. Proposed (needs an algorithm version bump): compute the solid angle at the target pixel from the WCS Jacobian.

## Archival epochs and solar-system objects

### Archival epochs (ADR-0023)

1. CAOM TAP search around the grid centre: 2MASS and AllWISE images that intersect the field, NEOWISE-R
   frames that contain the centre (`spherex_core/archival/sources.py`).
2. Pixels: AllWISE Atlas tiles and NEOWISE-R frames by range reads from `s3://nasa-irsa-wise` (rows covering
   the field only); 2MASS images downloaded whole (gzip, ~1.5 MB).
3. Calibration to MJy/sr: `DN × F0 × 10^(−0.4 MAGZP) / Ω_pixel`, Vega zero points 1594 / 1024 / 666.7 Jy
   (J/H/Ks) and 309.54 / 171.79 Jy (W1/W2).
4. Background: sigma-clipped median subtracted per image.
5. Smoothing to the SPHEREx scale: NaN-aware Gaussian, σ = 0.35 × (6.15″ / native pixel) native pixels
   (2.2 px for 2MASS, 1.6 for AllWISE, 0.8 for NEOWISE); then bilinear resampling onto the sequence grid.
6. Epochs: 2MASS images within 3 days mosaicked (mean), all AllWISE tiles mosaicked (mean), NEOWISE single
   exposures median-combined per visit (gap > 20 days or span > 30 days starts a new visit; ≥ 3 frames).

Checks (2026-11-14, light-weighted centroid within 4 px of the predicted target position):

| Field | Frames | Baseline | Centroid offset from proper motion | Build |
|---|---|---|---|---|
| Barnard's Star, 12′ | 47 | 2000.4–2024.2 | 0.1–2.5″ (median ~0.8″) | 134 s, 1.1 GB |
| ε Indi B, 16′ | 50 | 1999.9–2024.4 | 0.2–1.4″ | 130 s, 1.5 GB |
| ε Indi B, 20′ | 50 | 1999.9–2024.4 | 0.2–1.3″ | 95 s, 1.75 GB |

Limitations: AllWISE combines 2010 passes ~6 months apart, so fast movers look slightly elongated; 2MASS
coverage of wide fields can be partial (`coverage`); bright stars saturate in 2MASS and WISE.

### Solar-system objects (ADR-0024)

IRSA MOST lists the SPHEREx files that contain the object with its predicted position at each exposure
start. The co-moving build cuts out and resamples every exposure on a grid centred on that position, then
runs the normal epoch grouping, co-adds and photometry, so co-adds stack the object while the stars trail.

| Object | MOST | Files | Epochs | Build | Brightest co-add pixel |
|---|---|---|---|---|---|
| 3I/ATLAS, 2025-08-01..20 | 1.9 s | 160 | 1 | 11 s | central pixel in D1–D6 (D4: 1 px off), S/N 69 (D1) to 3 (D6) |
| 433 Eros, 2025-04..2026-11 | 12–165 s | 954 | 6 | 47 s | central pixel in every epoch and detector |

Command-line tools: `scripts/build_archival.py <sequence id> [--fov 12]` builds (or warms) archival epochs and
prints the target's centroid offset in every frame; `scripts/build_moving.py <name> [--begin --end --type]`
runs the MOST search and builds the co-moving sequence.
