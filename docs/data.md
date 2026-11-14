# SPHEREx data for developers

What you need to know about SPHEREx data to work on this project, with the numbers we measured on 2026-11-14. Authoritative references: the [IRSA SPHEREx page](https://irsa.ipac.caltech.edu/Missions/spherex.html), the [Explanatory Supplement](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf) and the [IRSA tutorial notebooks](https://caltech-ipac.github.io/irsa-tutorials/spherex/).

## The instrument in one paragraph

SPHEREx has been mapping the whole sky since May 2025, covering each region about every six months. It has six detector arrays (D1–D6) behind linear variable filters: the transmitted wavelength changes across each detector, so one exposure is a set of narrow-band strips, and a small cutout is effectively a single narrow band. Stepping the field across the filters over a visit gives 17 channels per detector, 102 in total, from 0.75 to 5.0 µm. Pixels are about 6.15″ (pixel solid angle `OMEGA_MEDIAN` ≈ 37.86 arcsec²). The point-spread function is 1.2–1.5 pixels wide (`PSF_FWHM` 7.5″ in a D1 exposure, 9.0″ in a D6 exposure we inspected).

| Detector | D1 | D2 | D3 | D4 | D5 | D6 |
|---|---|---|---|---|---|---|
| Wavelength (µm) | 0.75–1.11 | 1.11–1.64 | 1.64–2.42 | 2.42–3.82 | 3.82–4.42 | 4.42–5.00 |
| Resolving power R | 39 | 41 | 41 | 35 | 112 | 128 |

The detectors work in pairs behind a dichroic: D1 and D4, D2 and D5, D3 and D6 look at the same field. In our coverage counts the images per pair match exactly (for example 584 D1 and 584 D4 images of R Draconis).

## Releases and collections

| Release | Date | Notes |
|---|---|---|
| Quick Release (QR) | July 2025 | first weekly releases |
| QR2 | October 2025 | reprocessed; superseded |
| QR3 | September 2026 | weekly, current processing |
| DR1 | November 2026 | first data release |

Collections searched by default (order = preference when one exposure appears twice): `spherex_dr1`, `spherex_dr1_deep`, `spherex_qr3`, `spherex_qr3_deep`. The `_deep` collections hold Deep Survey images near the ecliptic poles. New data are ingested weekly. Override with `SPHEREX_COLLECTIONS`.

## Finding images

IRSA TAP (`https://irsa.ipac.caltech.edu/TAP/sync`) with the CAOM tables, no ingestion lag ([ADR-0003](adr/0003-tap-image-discovery.md)):

```sql
SELECT a.uri, p.energy_bandpassname AS band, p.time_bounds_lower AS t0, p.time_bounds_upper AS t1,
       p.energy_bounds_lower AS wl0, p.energy_bounds_upper AS wl1, o.collection, o.observationid AS obs
FROM spherex.artifact a
JOIN spherex.plane p ON a.planeid = p.planeid
JOIN spherex.observation o ON p.obsid = o.obsid
WHERE 1 = CONTAINS(POINT('ICRS', 269.4463, 4.7682), p.poly)
  AND o.collection IN ('spherex_dr1','spherex_dr1_deep','spherex_qr3','spherex_qr3_deep')
  AND a.producttype = 'science'
```

- Times are MJD; wavelengths are in metres; `band` is `SPHEREx-D1` … `SPHEREx-D6`.
- SIA2 also works (`Irsa.query_sia(pos=..., collection=[...])`) but lags by about a day.

## Getting pixels

- **Public S3 bucket:** `s3://nasa-irsa-spherex/` in us-east-1, anonymous. Over HTTPS: `https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/<path>`, where `<path>` is the TAP `uri` without the `ibe/data/spherex/` prefix. HTTP range requests return `206`.
- **Latency:** 67 ms median per request from a server in Pennsylvania; 1.5–10 s from the team's own location.
- **IRSA cutout service:** `https://irsa.ipac.caltech.edu/<uri>?center=<ra>,<dec>d&size=<deg>` returns a multi-extension cutout. During the hackathon it answered `503 Server busy` to 3 of 4 requests, and the one success took ~95 s for 0.1° (2.06 MB). We do not use it ([ADR-0002](adr/0002-s3-range-reads.md)).

## Level 2 file layout

One multi-extension FITS file per exposure and detector, 64.2–64.5 MB. Example: `level2_2025W35_1A_0432_4D1_spx_l2b-v27-2026-230.fits` (DR1, detector D1). Byte offsets as read by astropy:

| HDU | Header at | Header length | Data at | Data size | Content |
|---|---|---|---|---|---|
| PRIMARY | 0 | 2,880 | — | 0 | `VERSION = '7.0.5'` |
| IMAGE | 2,880 | 25,920 | 28,800 | 16,646,400 | float32 2040 × 2040, MJy/sr, zodiacal light **not** subtracted |
| FLAGS | 16,675,200 | 17,280 | 16,692,480 | ~10.1 MB | int32 bitmask, RICE_1 tile-compressed (binary table) |
| VARIANCE | 26,789,760 | 11,520 | 26,801,280 | 16,646,400 | float32, (MJy/sr)² |
| ZODI | 43,447,680 | 11,520 | 43,459,200 | 16,646,400 | float32 zodiacal-light model |
| EPSF | 60,105,600 | 5,760 | 60,111,360 | 3,862,080 | table: 441 effective PSFs (21 × 21 zones of ~97 px, rows x-fast then y; `EPSF` 33 × 33 float64 oversampled 5×, sum 1; `XCENTER`/`YCENTER`, `CWAVE`, `NEFF_MEAN`). Identical across DR1 and QR3 files of a detector |
| WCS-WAVE | 63,973,440 | 2,880 | 63,976,320 | 2,880 | pixel → wavelength lookup table |

Things that vary between files:

- The `IMAGE` header is 25,920 bytes for D1 files and 23,040 bytes for the D6 file we checked.
- The compressed `FLAGS` size varied from 9.4 to 11.1 MB in four files, so every extension after `FLAGS` moves.
- 2040 × 2040 × 4 bytes is exactly 5,780 FITS blocks, so image data units carry no padding.

### FLAGS compression

`FLAGS` is stored as a tile-compressed binary table: `ZCMPTYPE = 'RICE_1'`, `ZBITPIX = 32`, `ZTILE1 = 2040`, `ZTILE2 = 1` (one tile per row), `BLOCKSIZE = 32`, `BYTEPIX = 4`, `TFORM1 = '1PB(…)'`. The table has 2,040 rows of 8 bytes: (byte count, heap offset) per tile, with the heap right after the table. To read rows y0…y1, read the table, then the contiguous heap span of those tiles, and decode each tile. astropy's `Rice1` codec expects a NumPy `uint8` array, not `bytes`.

### Useful IMAGE header keywords

| Keyword | Example | Meaning |
|---|---|---|
| `MJD-AVG` | 60914.2132 | mid-exposure time |
| `PSF_FWHM` | 7.53 | median PSF FWHM, arcsec |
| `OMEGA_MEDIAN` | 37.86 | median pixel solid angle, arcsec² (std 0.20) |
| `XPOSURE` | 113.58 | exposure time, s |
| `DETECTOR` | 1 | 1–3 short-wave, 4–6 mid-wave |
| `OBSID` | `2025W35_1A_0432_4` | exposure id (without detector) |
| `CTYPE1/2` | `RA---TAN-SIP` | celestial WCS with SIP distortion |
| `BUNIT` | `MJy / sr` | |

The `IMAGE` header has about 300 cards. Building an astropy `WCS` from all of them costs ~35 ms; we keep only the WCS and the keys above ([ADR-0013](adr/0013-process-pool-and-slim-headers.md)).

## Flags

Bits defined in the `FLAGS` header (`MSKNnnnn` = name, `MSKDnnnn` = description). The last two columns are our policy ([ADR-0018](adr/0018-photometry-flag-policy.md), which replaced ADR-0008). "Bad for photometry" flags an aperture measurement; an ePSF fit instead leaves saturated pixels out and is flagged by how much of the PSF remains ([ADR-0016](adr/0016-epsf-psf-photometry.md)).

| Bit | Name | Description | Masked in images | Bad for photometry |
|---|---|---|---|---|
| 0 | TRANSIENT | Transient detected during SUR | yes | yes |
| 1 | OVERFLOW | Overflow detected during SUR | | yes |
| 2 | SUR_ERROR | Error in onboard processing | yes | yes |
| 6 | NONFUNC | Permanently unusable | yes | yes |
| 7 | DICHROIC | Low efficiency due to dichroic | | |
| 9 | MISSING_DATA | Onboard data lost | yes | yes |
| 10 | HOT | Hot pixel | yes | yes |
| 11 | COLD | Anomalously low signal | yes | yes |
| 12 | FULLSAMPLE | Pixel full sample history is available | | |
| 14 | PHANMISS | Phantom correction was not applied | | |
| 15 | NONLINEAR | Nonlinearity correction cannot be applied reliably | | yes |
| 17 | PERSIST | Persistent charge above threshold | yes | yes |
| 19 | OUTLIER | Pixel flagged by Detect Outliers | yes | yes |
| 20 | CROSSTALK | Pixel affected by crosstalk | | yes |
| 21 | SOURCE | Pixel mapped to a known source | | |
| 22 | GHOST | Affected by a bright source inside the exposure | | yes |
| 23 | GHOST_FPA | Affected by a bright source on another detector | | |
| 24 | GHOST_EXT | Pixel affected by a bright source outside FOV | | |
| 25 | STREAK | Pixel affected by a streak | yes | yes |
| 26 | BLOOM | Pixel part of a bloom artifact | | yes |
| 27 | SNOWBALL | Pixel part of a snowball artifact | yes | yes |
| 28 | HALO | Pixel part of a transient halo | | (informational) |
| 31 | FILLED | *Pipeline bit in `flags.bin`, not in the files:* co-add pixel masked in every exposure, filled from the masked values | | |

The `IMAGE` header also counts flagged pixels per exposure (`L2 N_OVERFLOW`, `L2 N_SOURCE`, …).

A measurement is also flagged when more than 20 % of its aperture or PSF weight sits on masked pixels (it was 5 % in ALGO_VERSION 1; the validation found points with 5–20 % masked no worse than clean ones, and points above 20 % deviating by 12 %). Saturated star cores carry masked bits (`SUR_ERROR`, `TRANSIENT`, `NONFUNC`, `PERSIST`) together with `OVERFLOW`, so they are masked in images; the co-adds fill them back and mark them `FILLED` ([ADR-0017](adr/0017-nan-aware-resampling-and-filled-coadds.md)).

## WCS-WAVE: wavelength per pixel

The `IMAGE` header defines an alternate WCS `W` of type `WAVE-TAB` (`CTYPE1W = CTYPE2W = 'WAVE-TAB'`, `PS1_0W = PS2_0W = 'WCS-WAVE'`). The `WCS-WAVE` table has one row with columns `X` (9 values), `Y` (9 values) and `VALUES` (`TDIM = (2,9,9)`, NumPy shape `[9 y, 9 x, 2]`): central wavelength and bandwidth in µm at a 9 × 9 grid of control points. `CRPIX = CDELT = CRVAL = 1`: the table is indexed by **raw** 1-based FITS pixel coordinates, with bilinear interpolation.

- The table is identical across the D1 files we compared (two DR1 weeks and one QR3 file), so we read it once per detector and cache it.
- D1 covers 0.735–1.126 µm across the detector; D6 covers 4.402–5.025 µm.
- **Gotcha:** `astropy.wcs.WCS(header, fobj=hdul, key='W')` applies the celestial SIP coefficients to these axes too (astropy warns about SIP keywords) and gets wavelengths wrong by up to 0.006 µm at the corners. Remove the `A_*`, `B_*`, `AP_*`, `BP_*` keywords first, or interpolate the table yourself.

## Coverage of the test targets

IRSA TAP on 2026-11-14, DR1 and QR3, all detectors. "Visits" are groups separated by gaps longer than 20 days.

| Target | Images | By collection | Span | Visits |
|---|---|---|---|---|
| Barnard's Star | 363 | DR1 240, QR3 123 | 2025-08-15 – 2026-09-22 | 3 main (110–123 images) + 2 small |
| ε Indi B | 465 | DR1 345, QR3 120 | 2025-04-24 – 2026-10-02 | 4 |
| WISE 0855−0714 | 258 | DR1 258 | 2025-05-05 – 2026-05-14 | 4 (one with 18 images) |
| RW Aurigae | 335 | DR1 226, QR3 109 | 2025-09-01 – 2026-09-16 | 3 |
| R Draconis | 2,493 | DR1 deep 1,225, QR3 deep 729, DR1 413, QR3 126 | 2025-04-24 – 2026-09-27 | nearly continuous |

R Draconis has far fewer D2/D5 images (89 each) than D1/D3/D4/D6 (~580 each): the Deep Survey pointings there favour the outer detector pairs.

## Gotchas we hit

- **Coordinates go stale.** Barnard's Star is ~4.5′ from its J2000 position; propagate proper motion before cutting a small field ([ADR-0010](adr/0010-proper-motion-aware-targets.md)).
- **Bright stars saturate.** All 358 photometric measurements of Barnard's Star are flagged by our policy, and its fluxes (1.3–2.6 Jy median per detector) are far below what its K ≈ 4.5 magnitude implies.
- **Zodiacal light is not subtracted** in `IMAGE`; subtract a model or a local background.
- **Wavelength differs across a cutout's parent detector,** so co-adding a detector mixes 17 wavelengths.
- **CDS was partly down** during the hackathon (SIMBAD TAP, VizieR TAP); Sesame worked through a mirror ([ADR-0011](adr/0011-name-resolution-and-catalogs.md)).
- **Motion is small in pixels.** With 6.15″ pixels and ~1.5 years of data, even the fastest test targets move only 1–2 pixels, so the viewer needs difference images, predicted-position overlays and comparisons with older surveys to make motion obvious.

## Other useful products

- **All-sky HiPS cube (DR1, 102 layers):** `https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube` — context, not time-resolved.
- **IRSA tools:** Spectrophotometry Tool (forced-photometry spectra), Moving Object Search Tool (MOST), Cutout and Mosaic tools, Source Discovery Tool.

**Acknowledgement required when using SPHEREx data:** "This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration."
