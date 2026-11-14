# Planet X × SPHEREx

**NASA Space Apps Challenge 2026 — [Planet X and SPHEREx](https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/)**

A public web tool for watching the infrared sky change. Enter an object name or a position (RA/Dec) and get a short "movie" of that patch of sky built from NASA SPHEREx images, so anyone can spot things that move (nearby stars, brown dwarfs, asteroids, comets) or change brightness (variable stars), and see their infrared spectrum.

> **Status, 2026-11-14 (hackathon day 1).** The data pipeline works on our development server: for a sky position it finds every SPHEREx image, reads cutouts straight from NASA's public S3 bucket, aligns them, groups them into epochs, co-adds each detector and measures the target in every exposure. The web viewer is being built. MCP access for AI agents, ML tools and release monitoring come next.

## What it does

1. **Find** every SPHEREx Level 2 image that covers the position, in the current releases (DR1 and the weekly QR3), including the Deep Survey fields.
2. **Follow the target**: positions are propagated with proper motion, so fast movers like Barnard's Star (4.5′ from its J2000 position) stay in the field.
3. **Read only what is needed** from each 64 MB file with HTTP range requests, about 2 MB per exposure for a 6′ field.
4. **Align and stack**: every exposure is resampled to a common north-up grid, grouped into epochs (visits about six months apart), and co-added per detector to raise the signal-to-noise ratio.
5. **Measure**: forced photometry at the target in every exposure, with the exact wavelength of each pixel, gives a spectrum per visit and a light curve.
6. **Show**: the browser blinks the epochs, subtracts or stacks them, builds false-colour images, overlays Gaia, CatWISE and 2MASS sources at the right date, and plots the spectrum and light curve (in progress).

## Features

| Feature | Status |
|---|---|
| Image discovery for any position (IRSA TAP; DR1 + QR3, wide + deep; releases configurable) | Done |
| Cutouts straight from S3 with range requests, identical to astropy's reads | Done |
| Proper-motion-aware targets; the five challenge targets as presets; coordinate input | Done |
| Name search (CDS Sesame) | Implemented |
| Alignment on a common grid, epoch grouping, per-detector co-adds with outlier rejection | Done |
| Forced photometry with per-pixel wavelengths (data for spectra and light curves) | Done |
| Contamination flags per epoch and detector, definitions read from the files | Done |
| HTTP API with background jobs, progress and binary arrays | Implemented |
| Gaia DR3, CatWISE2020 and 2MASS cone searches for overlays | Implemented |
| Web viewer: blink, difference, stack, false-colour RGB, single exposures, overlays, charts | In progress |
| Field-of-view choice, shareable and embeddable links with the view in the URL | In progress (API supports it) |
| MCP server so AI agents can use the tool | Planned |
| ML tools: moving-source candidates, real/bogus ranking, spectral classification | Planned |
| Release monitor and "what's new" (TAP already sees weekly data) | Planned |
| Split into services: api, worker, mcp, web (seams already in place) | Planned |

"Done" means we ran it on real data and checked the result; "Implemented" means the code exists and runs but has not been fully checked end to end.

## Key findings so far

- The IRSA cutout service was overloaded during the hackathon (`503`, ~95 s per cutout), so we read the public S3 copies directly: 358 exposures of Barnard's Star in 37.7 s from a server next to the bucket ([ADR-0002](docs/adr/0002-s3-range-reads.md)).
- Barnard's Star's measured position per visit matches its proper motion to better than 1″; it moved 10″ (1.6 SPHEREx pixels) north in a year ([methods](docs/methods.md#validation-so-far)).
- Bright stars saturate SPHEREx: Barnard's Star's photometry is all flagged, though its images still show the motion.
- The wavelength of every pixel comes from a lookup table that astropy misreads when SIP distortion keywords are present; we interpolate it ourselves ([data](docs/data.md#wcs-wave-wavelength-per-pixel)).

## Quick start

Backend (Python 3.11+):

```bash
uv pip install -e "backend[dev]"
```

```bash
SPHEREX_CACHE_DIR=./cache uvicorn spherex_api.main:app --port 8000
```

```bash
python scripts/build_sequence.py barnard
```

Reading from S3 is slow far from us-east-1, so we run the backend on a server in US-East. Setup, tests, deploy and the web dev server are in [docs/development.md](docs/development.md).

## Documentation

- [Architecture](docs/architecture.md): components, request lifecycle, module map
- [SPHEREx data for developers](docs/data.md): detectors, releases, file layout, flags, wavelengths, access
- [Methods](docs/methods.md): pipeline parameters, validation, known limitations
- [HTTP API](docs/api.md)
- [Development](docs/development.md)
- [Architecture Decision Records](docs/adr/README.md)

## Test targets

Coverage from IRSA TAP on 2026-11-14 (DR1 + QR3, all six detectors).

| Target | What it is | RA, Dec (J2000) | Proper motion | SPHEREx coverage | What we should see |
|---|---|---|---|---|---|
| Barnard's Star | M4 dwarf, the largest proper motion of any star | 17:57:48.50 +04:41:36.2 | 10.4″/yr | 363 images, 3 main visits, Aug 2025 – Sep 2026 | ~2 px shift; it is ~4.5′ away from its J2000 position |
| ε Indi B | T1 + T6 brown dwarf binary, 6.7′ from ε Indi A | 22:04:10.52 −56:46:57.7 | 4.7″/yr | 465 images, 4 visits, Apr 2025 – Oct 2026 | ~1 px shift shared with ε Indi A; methane absorption in its spectrum |
| WISE 0855−0714 | Coldest known brown dwarf (~285 K) | 08:55:10.831 −07:14:42.53 | 8.1″/yr | 258 images, 4 visits, May 2025 – May 2026 | Visible only at the long-wavelength end (D5–D6, ~3.8–5 µm); ~1.4 px shift |
| RW Aurigae | Young T Tauri binary with deep dimming episodes | 05:07:49.54 +30:24:05.1 | small | 335 images, 3 visits, Sep 2025 – Sep 2026 | Brightness and colour changes between visits |
| R Draconis | Mira variable, period ≈ 245 days | 16:32:40.19 +66:45:18.0 | small | 2,493 images (Deep Survey area near the north ecliptic pole), nearly continuous Apr 2025 – Sep 2026 | A light curve covering ~2 pulsation cycles |

## Team objectives

| Objective | Where it stands |
|---|---|
| An intuitive interface | web viewer in progress ([ADR-0015](docs/adr/0015-frontend-stack.md)) |
| A robust service architecture | modular monolith with seams for api / worker / mcp / web ([ADR-0004](docs/adr/0004-modular-monolith.md)) |
| Classical and ML analysis tools | co-adds, difference imaging and forced photometry done; ML planned |
| An MCP server for AI agents | planned, as an adapter over the same services as the HTTP API |
| Automatic discovery of new data | TAP search sees weekly data with no lag; collections configurable ([ADR-0003](docs/adr/0003-tap-image-discovery.md)); release monitor planned |

## The challenge

Since May 2025 SPHEREx has mapped the whole sky every ~6 months in 102 near-infrared bands (0.75–5.0 µm), returning images of more than a billion objects. Objects that move between visits show up when you compare images taken at different times. That is how Pluto was found, and how volunteers on [Backyard Worlds](http://backyardworlds.org/) find brown dwarfs with [WiseView](http://byw.tools/wiseview) on WISE data.

**Required:** a public web tool where users enter RA/Dec and see a time series of SPHEREx images of that patch, played like a short video.

**Suggested extras (from the brief):** easy updates as new data releases arrive · creative use of the 102 bands (e.g. co-add each of the 6 detector arrays for signal-to-noise; spectra on the fly) · adding or subtracting epochs · user-selectable field of view · overlays from Gaia and WISE · coordinates in the URL so the tool can be embedded · metadata such as contamination flags · reuse of existing tools (Astropy, Aladin from CDS).

## Resources

- IRSA SPHEREx: https://irsa.ipac.caltech.edu/Missions/spherex.html · [Explanatory Supplement](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf) · [Archive documentation](https://caltech-ipac.github.io/spherex-archive-documentation/)
- Tutorial notebooks: https://caltech-ipac.github.io/irsa-tutorials/spherex/ (intro, cutouts, PSF, mosaics, HiPS cubes, Source Discovery Tool)
- WiseView, the reference tool for WISE: http://byw.tools/wiseview
- Gaia DR3: https://archive.stsci.edu/missions-and-data/gaia
- Aladin Lite (CDS): https://aladin.cds.unistra.fr/AladinLite/
- Astropy: https://www.astropy.org/ · astroquery: https://astroquery.readthedocs.io/

**Acknowledgement required when using SPHEREx data:** "This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration."
