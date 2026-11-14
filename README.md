# Planet X × SPHEREx

**NASA Space Apps Challenge 2026 — [Planet X and SPHEREx](https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/)**

A public web tool for watching the infrared sky change. Enter a position (RA/Dec) and get a short "movie" of that patch of sky built from NASA SPHEREx images, so anyone can spot things that move (nearby stars, brown dwarfs, asteroids, comets) or change brightness (variable stars).

> Status: kickoff. This README holds the challenge brief and what we learned from a first look at the data.

## The challenge

Since May 2025 SPHEREx has mapped the whole sky every ~6 months in 102 near-infrared bands (0.75–5.0 µm), returning images of more than a billion objects. Objects that move between visits show up when you compare images taken at different times. That is how Pluto was found, and how volunteers on [Backyard Worlds](http://backyardworlds.org/) find brown dwarfs with [WiseView](http://byw.tools/wiseview) on WISE data.

**Required:** a public web tool where users enter RA/Dec and see a time series of SPHEREx images of that patch, played like a short video.

**Suggested extras (from the brief):** easy updates as new data releases arrive · creative use of the 102 bands (e.g. co-add each of the 6 detector arrays for signal-to-noise; spectra on the fly) · adding or subtracting epochs · user-selectable field of view · overlays from Gaia and WISE · coordinates in the URL so the tool can be embedded · metadata such as contamination flags · reuse of existing tools (Astropy, Aladin from CDS).

## Test targets

Coverage is from IRSA TAP on 2026-11-14 (DR1 + QR3, all six detectors).

| Target | What it is | RA, Dec (J2000) | Proper motion | SPHEREx coverage | What we should see |
|---|---|---|---|---|---|
| Barnard's Star | M4 dwarf, the largest proper motion of any star | 17:57:48.50 +04:41:36.2 | 10.4″/yr | 363 images, 3 main visits, Aug 2025 – Sep 2026 | ~2 px shift; it is ~4.5′ away from its J2000 position |
| ε Indi B | T1 + T6 brown dwarf binary, 6.7′ from ε Indi A | 22:04:10.52 −56:46:57.7 | 4.7″/yr | 465 images, 4 visits, Apr 2025 – Oct 2026 | ~1 px shift shared with ε Indi A; methane absorption in its spectrum |
| WISE 0855−0714 | Coldest known brown dwarf (~285 K) | 08:55:10.831 −07:14:42.53 | 8.1″/yr | 258 images, 4 visits, May 2025 – May 2026 | Visible only at the long-wavelength end (D5–D6, ~3.8–5 µm); ~1.4 px shift |
| RW Aurigae | Young T Tauri binary with deep dimming episodes | 05:07:49.54 +30:24:05.1 | small | 335 images, 3 visits, Sep 2025 – Sep 2026 | Brightness and color changes between visits |
| R Draconis | Mira variable, period ≈ 245 days | 16:32:40.19 +66:45:18.0 | small | 2,493 images (Deep Survey area near the north ecliptic pole), nearly continuous Apr 2025 – Sep 2026 | A light curve covering ~2 pulsation cycles |

## Objectives (team draft)

1. An intuitive interface.
2. A robust microservice architecture for the web service.
3. Many analysis tools, both classical and ML.
4. An MCP server so AI agents can use the tool.
5. Automatic discovery of new data releases if possible; otherwise, at least a convenient way to load new data.

## SPHEREx data cheat-sheet

- **Product:** Level 2 Spectral Images, one multi-extension FITS (MEF) file per exposure per detector (D1–D6). 2040 × 2040 px at ~6.2″/px, ~64 MB, uncompressed.
  Extensions: `IMAGE` (MJy/sr; zodiacal light **not** subtracted) · `FLAGS` (bitmask; bit names and descriptions are in header keywords `MSKNnnnn` / `MSKDnnnn`) · `VARIANCE` · `ZODI` (background model) · `EPSF` (441 effective PSFs across the detector) · `WCS-WAVE` (lookup table from pixel to central wavelength and bandwidth).
- **Wavelength varies across each detector** (linear variable filters), so a small cutout is effectively a single narrow band. Each detector covers 17 of the 102 channels:

  | Detector | D1 | D2 | D3 | D4 | D5 | D6 |
  |---|---|---|---|---|---|---|
  | λ, µm | 0.75–1.1 | 1.1–1.6 | 1.6–2.4 | 2.4–3.8 | 3.8–4.4 | 4.4–5.0 |
  | R | 39 | 41 | 41 | 35 | 112 | 128 |

- **Releases:** QR (Jul 2025) → QR2 (Oct 2025) → QR3 (Sep 2026) → **DR1 (Nov 2026)**. QR3 and DR1 supersede QR2. SIA collections: `spherex_dr1`, `spherex_dr1_deep`, `spherex_qr3`, `spherex_qr3_deep`. New data are ingested weekly.
- **Search:** SIA2 (`astroquery.ipac.irsa.Irsa.query_sia(pos=..., collection=[...])`, ~1 day lag), or TAP on `spherex.plane` ⨝ `spherex.artifact` ⨝ `spherex.observation` (no lag). Footprint test: `CONTAINS(POINT('ICRS', ra, dec), p.poly)`; detector: `p.energy_bandpassname`; time: `p.time_bounds_lower` (MJD).
- **Cutouts:** `https://irsa.ipac.caltech.edu/<artifact.uri>?center=<ra>,<dec>d&size=<deg>` returns a MEF cutout with all extensions (~2 MB at 0.1°, almost all of it the EPSF table).
- **Cloud:** the same files are in the public S3 bucket `s3://nasa-irsa-spherex/` (us-east-1, anonymous access; over HTTPS at `https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/<path>`, where `<path>` is the IRSA URI without the `ibe/data/spherex/` prefix). HTTP range requests work, so a cutout can be read without downloading the whole file.
- **All-sky HiPS cube (DR1, 102 layers):** `https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube`. Good for a context view; not time-resolved.
- **IRSA tools worth knowing:** Spectrophotometry Tool (forced-photometry spectra), Moving Object Search Tool (MOST), Cutout and Mosaic tools, Source Discovery Tool.

## Findings from the first data check (2026-11-14)

- **The IRSA cutout service was overloaded.** Most requests returned `503 Server busy`, and one successful 0.1° cutout took ~95 s. With 250–2,500 images per target, a viewer cannot fetch cutouts from it on demand. Range reads from S3 worked (HTTP 206, 1.5–10 s per request from our location; should be far faster from inside AWS us-east-1).
- **Fast movers are arcminutes away from their J2000 positions** (Barnard's Star: ~4.5′). Propagate proper motion to the observation epoch (Gaia DR3, SIMBAD, CatWISE) before cutting a small field.
- **Motions are small in pixels.** With ~6.2″ pixels and a 1–1.5 year baseline, even the test targets move only 1–2 px. Epoch co-adds, difference images, sub-pixel centroids, predicted-position overlays and blinking against older surveys (WISE, 2MASS) are what make the motion visible.
- **Coverage is very uneven.** Most of the sky gets ~2–3 visits a year, but areas near the ecliptic poles get thousands of images (R Dra). Time binning has to adapt to the target.

## Resources

- IRSA SPHEREx: https://irsa.ipac.caltech.edu/Missions/spherex.html · [Explanatory Supplement](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf) · [Archive documentation](https://caltech-ipac.github.io/spherex-archive-documentation/)
- Tutorial notebooks: https://caltech-ipac.github.io/irsa-tutorials/spherex/ (intro, cutouts, PSF, mosaics, HiPS cubes, Source Discovery Tool)
- WiseView, the reference tool for WISE: http://byw.tools/wiseview
- Gaia DR3: https://archive.stsci.edu/missions-and-data/gaia
- Aladin Lite (CDS): https://aladin.cds.unistra.fr/AladinLite/
- Astropy: https://www.astropy.org/ · astroquery: https://astroquery.readthedocs.io/

**Acknowledgement required when using SPHEREx data:** "This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration."
