# Analog tools and gap analysis

What the NASA/IPAC SPHEREx tools, IRSA Viewer, WiseView and the main sky viewers can do, how that compares with our tool, and which gaps are worth closing during the hackathon. Researched on 2026-11-14.

**Method.** We read the online help, user guides, tutorial notebooks, release notes and, where the help was thin, the page source (HTML/JavaScript) of each tool. A few read-only API calls confirmed behaviour; they are marked "tested" and listed in [Checks we ran](#checks-we-ran). Every claim has a source tag in square brackets, resolved in [Sources](#sources). **(inf.)** marks something we inferred rather than read, usually that a feature is absent because no page mentions it.

**Status of "ours"** comes from the [README](../../README.md), [docs/api.md](../api.md), [docs/methods.md](../methods.md), [web/README.md](../../web/README.md) and the brief for this research [OURS]. Done = ran on real data; Done\* = code exists and runs but is not checked end to end (the README's "Implemented"); In progress = web viewer being built; Missing (Planned) = on our roadmap.

**Effort.** S = up to half a day, M = one to two days, L = more than two days, for one developer.

## Summary

Top 10 parity gaps, ranked by value for this challenge (details in [section 3](#3-parity-gaps-ranked)):

| # | Gap | Who has it | Effort |
|---|---|---|---|
| 1 | Long-baseline comparison: 2MASS, AllWISE and NEOWISE epochs in the same movie | WiseView, Legacy Viewer, IRSA Viewer, ESASky | M (single epochs), L (NEOWISE visits) |
| 2 | Sky context map: HiPS (SPHEREx all-sky cube, 2MASS, WISE) with coverage, exposure footprints and click-to-target, built on Aladin Lite | SPHEREx Data Explorer, IRSA Viewer, Aladin, ESASky, Legacy | M |
| 3 | Solar-system objects: ephemeris-driven search and object-centred stacks via MOST | MOST, ESASky | M |
| 4 | Shift-and-add at a trial motion, difference against all other epochs, sliding windows | WiseView | S |
| 5 | Spectrum and light curve for any clicked or catalogued source, on the fly | Spectrophotometry Tool, Firefly extraction, Source Discovery Tool | M |
| 6 | Measurement tools: wavelength readout, click-lock and copy, distance and offset, centroid and proper-motion fit | SPHEREx Data Explorer and IRSA Viewer (Firefly) | S–M |
| 7 | Periodogram and phase folding of light curves | IRSA Viewer Time Series Tool, ESASky | S |
| 8 | Exports: FITS with WCS, CSV and VOTable photometry, download script, animated GIF or MP4 | Data Explorer, IRSA Viewer, Legacy, Aladin | S–M |
| 9 | ePSF photometry with simultaneous neighbours, solid-angle map and CWAVE/CBAND calibration | Spectrophotometry Tool | M–L (calibration S) |
| 10 | Release monitor and data freshness, plus date and detector filters | SPHEREx Data Explorer | S |

Main findings:

- **No IRSA tool shows SPHEREx as a time sequence.** The Data Explorer lists single exposures and can auto-play through loaded images, but has no epochs, co-adds or difference images and no proper-motion handling [DE-vis] [DE-results]. The Mosaic Tool and the all-sky HiPS cube co-add all epochs [MOSAIC] [AUG-products]. The Spectrophotometry Tool does not recentre or propagate positions; its help tells users to check high-proper-motion sources themselves [SP].
- **WiseView is the closest UX analog**, and the challenge names it as the model [CHALLENGE]: blink interval, sliding-window co-adds, difference imaging, shift-and-add, a Gaia DR3 overlay moved to each frame's date, synthetic sources, and every setting in the URL [WV] [WV-code].
- **Firefly (Data Explorer, IRSA Viewer) sets the bar for interaction:** readout including SPHEREx wavelength and bandwidth, distance and region statistics, extraction, mask layers per flag bit, WCS lock, downloads and a job monitor [DE-vis]. Only IRSA Viewer's Time Series Tool and ESASky offer periodograms and phase folding [IV-TS] [ESA-int].
- **The biggest wins for the challenge are a longer baseline (WISE, 2MASS), a HiPS context map (the challenge names Aladin [CHALLENGE]) and solar-system objects through MOST**, whose SPHEREx search we tested: 160 DR1 exposures of 3I/ATLAS in about 3 s [MOST-test].
- **Our unique ground:** proper-motion-aware epochs and co-adds, per-epoch spectra and light curves from one request, catalogues moved to each epoch's date, S3 range reads that avoid IRSA's overloaded cutout service, and full view state in the URL for SPHEREx. None of the analogs does these for SPHEREx ([section 4](#4-what-none-of-them-has)).

## 1. The tools

### 1.1 SPHEREx Data Explorer (IRSA)

A Firefly-based web application for searching, visualising and downloading SPHEREx Level 2 spectral images. It also hosts the Spectrophotometry Tool and the Mosaic Tool as tabs; by default the tabs are Results, Job Monitor, Spectral Image Search, Spectrophotometry Tool and Mosaic Tool, and the side drawer adds VO TAP, IRSA Catalogs, VO cone search, NED and upload [DE-overview]. It works on the on-premises copy of the data [AUG-access] [DE-faq].

Links: [application](https://irsa.ipac.caltech.edu/applications/spherex/) · [online help](https://irsa.ipac.caltech.edu/onlinehelp/spherex/) · [SPHEREx Archive User Guide](https://caltech-ipac.github.io/spherex-archive-documentation/) · [IRSA mission page](https://irsa.ipac.caltech.edu/Missions/spherex.html).

**Searching** [DE-search]

- The search screen is the SPHEREx all-sky HiPS (multi-plane) with MOC overlays for released wide coverage (yellow) and Deep Fields (red). The Galactic Center is not released yet and shows as a gap. Users pan, zoom, click or draw a region to choose a position.
- Five query types: observation boundary contains point (default), contains shape, is contained by shape, intersects shape, centre point inside shape. Shapes are cones or polygons of 3–15 vertices.
- Targets by name, resolved NED-then-SIMBAD or SIMBAD-then-NED, with the resolver and object type shown; or coordinates in decimal, sexagesimal or `Jhhmmss+ddmmss` form, with `gal`, `ecl` and `b1950` suffixes and `G…` names parsed as Galactic. The parsed position is echoed while typing.
- Multi-object search from an uploaded or loaded table. It returns the union of images rather than a list per target [DE-results].
- Time constraint: overlap with a UTC or MJD range, or "completed in the last N days". Spectral constraint: chosen bands, a contained wavelength, or an overlapping range. With no constraints it returns everything, calibration files included.
- Data can be up to 60 days old and arrive weekly. The Data Explorer and TAP see new data before SIA, which lags by about a day [DE-search] [AUG-access].
- Only fixed (inertial) targets; moving targets are not supported in this tool [DE-results].

**Results** [DE-results]

- The left pane has one table tab per search, and searches accumulate. The right pane has four tabs: Data (the FITS of the selected row), Coverage (a HiPS with the footprints of all results, the selected one highlighted), Details (metadata) and Active Chart (`s_ra`/`s_dec` by default).
- "Image sets" (D1+D4, D2+D5, D3+D6, the same field at the same time) are highlighted together. `obs_collection` tells DR1 from QR3.

**Image display** [DE-vis] [DE-images] [DE-cutouts]: Firefly's viewer plus SPHEREx specifics.

- Readout of coordinates in eight systems (equatorial, galactic, ecliptic, B1950, FITS and zero-based pixels), pixel value in decimal or hexadecimal, and **the wavelength and bandwidth under the cursor**. Click-lock with copy to clipboard, including a Python-ready format. Magnifier with a compass rose.
- HDU and plane navigation through IMAGE, FLAGS, VARIANCE, ZODI, EPSF and WCS-WAVE. Planes of a cube are stepped by hand; the help states there is no automatic playback through planes.
- **Mask layer for SPHEREx flags**, with a toggle and colour per bit.
- "More" menu: calibration files related to the image, found through DataLink.
- Layouts: one image at a time; tiles, up to 8 per page; image set only; scrolling tiles; an expanded view with arrows and an **"Auto play" checkbox that cycles through the loaded images**.
- Cutouts (scissors icon) of a chosen size, centred on the search target, the table row or an entered position. This is the only way to see the same patch at the same zoom across exposures; without it, each new image resets the zoom even with WCS lock on.
- Three-colour image from an image set, so effectively two colours.
- Toolbar: save as FITS, PNG or DS9 region (crop first to save a subimage), reset, FITS header, rotation to any angle, north up, flip, compass, coordinate grid, **distance tool with ΔRA/ΔDec offsets**, DS9 region import (text, circle, box, polygon, line, annulus), markers and instrument footprints (Spitzer, SOFIA, HST, JWST, Roman), extraction (drill through planes, line profile, points; 1×1 to 7×7 sum or average; pin to a table and chart), zoom, fit, fill, 1:1, colour tables (invert, NaN colour, bias and contrast, locked across images), stretches (histogram editor, linear, histogram equalisation, Lupton hue-preserving asinh for RGB), recentre, region selection (crop, select or filter catalogue sources, **statistics with flux-weighted centroid**, launch TAP, NED or SIMBAD searches), layers, and lock or align by WCS, target, pixel origin or centre.
- HiPS menus: change HiPS (IRSA featured list), add a MOC layer (including your own MOC file), automatic switch to a 2MASS Ks FITS image when zoomed in, Galactic, equatorial or ecliptic orientation, spherical or Aitoff projection [DE-images].

**Spectra** [DE-spectra] [DE-examples] [DE-faq]: spectrum plots with unit conversion; line lists (SPHEREx, Spitzer, Herschel, or uploaded); redshift (observed or rest frame); table view; and colouring points by MJD to reveal variability between visits. The FAQ explains that alternating high and low points in a spectrum usually mean saturation or different epochs.

**Catalogues, tables, plots and uploads** [DE-catalogs] [DE-tables] [DE-plots] [DE-upload]: IRSA catalogues (cone, ellipse, box, polygon, multi-object, all-sky, with column constraints), VO TAP, cone search and NED. Uploads from disk, a URL or the IRSA Workspace: IPAC table, CSV, TSV, VOTable, Parquet, FITS, ASDF, DS9 regions, MOC, DataLink and UWS job files. Above about 1,000 sources, catalogues are drawn hierarchically on HEALPix cells. Brushing and linking between table, plot and image; filters; derived columns from SQL expressions; scatter, heatmap and histogram plots.

**Downloads** [DE-downloads] [DE-cutouts]: select rows, then "Generate Download Script" with a choice of products, structured or flattened directories, and the local disk or the IRSA Workspace. Cutouts download at the size shown. The script is curl, wget or a list of URLs. A Job Monitor runs background jobs, sends email on request, and keeps jobs for two weeks (TAP jobs 72 hours), tied to the browser's cookies. Users can log in.

**Programmatic access.** A URL API opens the search panel filled in, or runs it [DE-api]:

```
https://irsa.ipac.caltech.edu/applications/spherex/?api=spectral-images&ra=255.65704&dec=-26.26794&sr=30m&spatialRegionOperation=intersects&execute=true
```

Parameters: `worldPt` or `ra`/`dec`, `sr`, `polygon`, `spatialRegionOperation` (`contains_point`, `contains_shape`, `contained_by_shape`, `intersects`, `center_contained`), `exposureMin`/`exposureMax` with `exposureTimeMode=iso|mjd`, `obsCoreWavelengthRangeType`, `obsCoreWavelengthContains`, `obsCoreWavelengthMinRange`/`MaxRange`, and `execute`. Underneath are SIA2 (`https://irsa.ipac.caltech.edu/SIA?COLLECTION=spherex_dr1…`), TAP, IBE cutouts, browsable directories and the `nasa-irsa-spherex` S3 bucket, all usable from astroquery or pyvo [AUG-access].

**Limits that matter to us:** no time-sequence view (no epoch grouping, co-adds or difference images); auto play cycles whole exposures only; no proper-motion handling (inf.); catalogue overlays are not moved to the observation date (inf.); heavy jobs are queued, with two running jobs per user [DE-downloads].

### 1.2 IRSA SPHEREx science tools

Listed on the IRSA mission page together with the SPLICES ice-source catalogue, the 3I/ATLAS page, cloud access and the tutorial notebooks [MISSION].

| Tool | Inputs | Outputs | Options and limits | Programmatic access |
|---|---|---|---|---|
| Spectrophotometry Tool | Position(s) by name or coordinates; up to 20 sources per run | VOTable spectrum per source (µJy) plus linked cutouts | Point source or Sérsic; wavelength ranges; include Deep Field frames; two running jobs per user | None documented; the Job Monitor lists it as a UWS job [DE-downloads] (inf.) |
| Mosaic Tool | Centre, size (default 15′, at most 5°×5°, rectangles allowed), rotation, pixel size, wavelength range | FITS cube with 102 channels: IMAGE, NHITS, FLAGS, SPECTRAL_CHANNELS, WCS-WAVE | Pixel 6.15″–3600″ (default 9″); zodiacal light subtracted; wide survey only; no time parameter (inf.) | GUI only; the tutorial uses a cube made in the GUI [MOSAIC-nb] (inf.) |
| Cutout Tool | On-premises image URL plus `center` and `size` | MEF cutout with all HDUs; WCS-WAVE adjusted to the cutout | Units deg, arcmin, arcsec or pixels; not gzipped for SPHEREx; axes of the parent image, not north-up | Yes: URL query string [IBE] |
| Source Discovery Tool | Position, radius, two detectors | SExtractor catalogue, band-difference image, aperture-photometry spectra | Runs locally or on Fornax | Python package in a notebook [SDT] |
| MOST | Solar-system object by name, number, NAIF ID, MPC one-line or orbital elements; date range; ephemeris step | Images crossed by the object, with predicted RA/Dec, distances, phase and magnitude; orbit plot; tarballs of FITS and DS9 regions | `catalog=spherex` among 2MASS, Spitzer, PTF, SOFIA, WISE/NEOWISE and ZTF | Yes: `nph-most` GET API and astroquery `Most` [MOST-nb] [MOST-test] |
| All-sky HiPS cube | HiPS client or HEALPix order-5 tile index | 102-frame PNG HiPS; 512×512×102 FITS cubes | DR1, all epochs combined; zodiacal light subtracted; PNG stretch fixed at 0–40 MJy/sr | Yes: static URLs and SIA collection `spherex_dr1_cube` [HIPS-nb] [AUG-access] |
| Comet 3I/ATLAS page | none | VOTable of SPHEREx exposures with predicted comet positions; curl and wget scripts | Advises against the Spectrophotometry Tool because the comet is extended | Static files [3I] |

#### Spectrophotometry Tool

[Application](https://irsa.ipac.caltech.edu/applications/spherex/tool-spectrophotometry) · [help](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/sp.html). All facts here are from [SP] unless tagged otherwise.

- **Method.** The Tractor forced photometry with the position-dependent PSF, VARIANCE weights and a local background fitted at the same time; only the flux amplitude is free. Point-source or Sérsic model (index 0.5–6, axis ratio, position angle, effective radius 1–20″). Close companions can be fitted together by listing their positions. Fluxes are converted to µJy with the Pixel Solid Angle Map. Pixels with SUR_ERROR, NONFUNC, MISSING_DATA, HOT, COLD, NONLINEAR, PERSIST, BLOOM, SNOWBALL, HALO or SATELLITE_HALO are ignored.
- **Output columns:** `wavelength`, `flux`, `flux_err`, `lambda_width`, `flags`, `fit_ql` (mean |data − model|/σ within 2.5 pixels; 1 is good), `flux_bkg`, `lvf_id`, `det_id`, `deep_flg`, `mjd`, `data_collection`, `x_image`, `y_image`, `ra`, `dec`. The flags OR the pixel flags and add CONTAINS_BAD_PIXEL (bit 32) and FIT_ERROR (bit 33).
- **Results view:** the spectrum (or its table) on the right, linked to cutouts along the bottom: 1, 3, 5 or 7 shown, 1.2′ by default, WCS-locked; the mask layer can be overlaid. Points can be coloured by `mjd` or `flags`.
- **Limits:** up to 20 sources per job and two running jobs per user. Run time is roughly 1.67 s per image plus 50 s, so minutes to hours: about 11 min for Barnard's Star's 358 images and about 70 min for R Draconis's 2,493 (our arithmetic from IRSA's formula and our image counts [OURS-data]). No recentring and no proper-motion propagation; the help asks users to check whether a high-proper-motion source has moved since its catalogue epoch. The FAQ reports about 10 minutes for about 200 images once a processor is free, and jobs lost to DDoS attacks and maintenance [DE-faq].
- **Access:** GUI and Job Monitor; download as VOTable through "Prepare Download". No public API is documented (inf.). A third-party paper says the tool needs manual coordinate input and does not allow large batch queries (from a search-result summary of arXiv:2604.22012, not read in full).

#### Mosaic Tool

[Application](https://irsa.ipac.caltech.edu/applications/spherex/tool-mosaic) · [help](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/mosaic.html) [MOSAIC] · [notebook](https://caltech-ipac.github.io/irsa-tutorials/spherex-mosaic/) [MOSAIC-nb].

- Rebuilds narrow-wavelength mosaics by pulling apart spectral images (Cukierman et al. 2026). Output: a cube with the 102 nominal channels (or a chosen range), zodiacal light subtracted, so fluxes differ from single exposures. HDUs IMAGE (MJy/sr), NHITS and FLAGS (OVERFLOW, OUTLIER and SOURCE ORed); SPECTRAL_CHANNELS and WCS-WAVE are saved in the file but hidden in the viewer.
- Parameters: centre (or a box drawn on the HiPS), size from one pixel up to 5°×5° (default 15′×15′, rectangles allowed), rotation east of north, pixel size from 6.15″ to 3600″ (default 9″), wavelength range.
- Wide survey only; Deep Field frames are not used yet. Stripe artifacts appear where there are few frames. About 5 minutes for the example. Two running jobs per user. Saved from the image toolbar only.
- The drill tool gives a quick-look spectrum through the cube; the help stresses that it is not equivalent to the Spectrophotometry Tool and not research-ready.
- No time-range parameter is listed, so mosaics mix all epochs (inf.).

#### Cutout Tool / IBE cutouts

[SPHEREx cutout page](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/cutout_tool.html) [CUT] · [IBE cutout reference](https://irsa.ipac.caltech.edu/ibe/cutouts.html) [IBE] · user guide [AUG-access] [AUG-products].

- Three routes: the scissors icon in the Data Explorer, a direct URL, or Python notebooks.
- `<on-prem image URL>?center=x,y[deg|rad|pix]&size=w[,h][deg|arcmin|arcsec|pix][&gzip=…]`. A cutout that overlaps the image edge is clipped; one with no overlap returns an error. SPHEREx cutouts are not gzipped.
- The MEF keeps all HDUs. WCS-WAVE is adjusted to the cutout; the PSF HDU is copied unchanged, so users locate the ePSF zone with `CRPIX1A`/`CRPIX2A`.
- Axes follow the parent image, not north-up, and the distortion terms may not be supported by other FITS viewers [IBE].
- On-premises only. The Data Explorer uses this service for its cutout downloads [AUG-access]. During the hackathon it returned `503` to three of four requests and took about 95 s for one success [OURS-data].

#### Source Discovery Tool

[Notebook](https://caltech-ipac.github.io/irsa-tutorials/spherex-source-discovery-tool-demo/) [SDT]: the Python package `spherex_source_discovery_tool`, run in a notebook on a laptop or on NASA Fornax.

- Steps: SIA search (`spherex_dr1` + `spherex_qr3`); cloud reads; conversion to µJy with the solid-angle pixel map from S3; background removal and masks; reprojection to a common grid; **a D2 − D4 "colour" difference image to find red sources** (an AGN use case); SExtractor on all images; interactive selection with bokeh or Firefly; TAP query plus IRSA cutouts for all exposures of the chosen source; aperture photometry (3–5 pixel radii) with a conservative bad-flag list, then a spectrum.
- No service or API; the notebook is the interface.

#### Moving Object Search Tool (MOST)

[Application](https://irsa.ipac.caltech.edu/applications/MOST/) · [instructions](https://irsa.ipac.caltech.edu/applications/MOST/instructions.html) [MOST] · [notebook](https://caltech-ipac.github.io/irsa-tutorials/most-queries/) [MOST-nb].

- Computes the orbit of a solar-system object and finds images that contain its predicted position at their exposure times. Datasets include `spherex`, 2MASS, Spitzer, PTF, SOFIA, the WISE/NEOWISE phases and ZTF. It can serve as a precovery tool.
- Input by name, number, designation or NAIF ID (resolved through JPL Horizons, with hints such as `:NID`, `:SAT`, `:AST` for ambiguous names), MPC one-line elements, or manual elements. Observation begin and end dates, ephemeris step (default 0.25 d).
- Output modes Regular, Full, Brief, VOTable and Gator: an orbit plot and a table of matched images with predicted `ra_obs`/`dec_obs`, heliocentric and geocentric distances, distance from the image centre, phase angle and V magnitude. Links download images or view them in IRSA Viewer. Optional tarballs of FITS images and DS9 region files.
- **API:** `https://irsa.ipac.caltech.edu/cgi-bin/MOST/nph-most` with `catalog`, `input_type` (`name_input`, `naifid_input`, `mpc_input`, `manual_input`), `obj_name`/`obj_naifid`/`obj_type`, `obs_begin`, `obs_end`, `ephem_step`, `output_mode`, `fits_region_files`; astroquery `Most.query_object()` wraps it [MOST-nb] [ASTROQUERY-MOST].
- **Tested on 2026-11-14** for SPHEREx: 3I/ATLAS from 2025-08-01 to 2025-08-20 returned 160 matched DR1 exposures in about 3 s, each with `ra_obj`, `dec_obj`, `mjd_start` and the DR1 file `uri`. "Ceres" by name was refused as ambiguous; the NAIF ID 20000001 was accepted (no SPHEREx matches in the window we tried) [MOST-test].

#### SPHEREx all-sky HiPS cube (DR1)

[Notebook](https://caltech-ipac.github.io/irsa-tutorials/spherex-hipscube/) [HIPS-nb] · [HiPS properties](https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube/properties) [HIPS-props] · user guide [AUG-products].

- HiPS at `https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube`: 102 frames (`hips_cube_depth=102`, `hips_cube_firstframe=1`), PNG tiles of 512 pixels, `hips_order=5`, 12.9″ pixels, asinh stretch with a fixed 0–40 MJy/sr cut. Frame index = 17 × (band − 1) + (channel − 1); frames 50 and 51 (the dichroic channels) are blank. Served with `access-control-allow-origin: *` (tested).
- FITS cubes, 512×512×102 per HEALPix order-5 tile (12,288 tiles of about 1.8°): `https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/hips5_cube/hips_20261026/Norder5/Dir{(ipix//10000)*10000}/Npix{ipix}_cube.fits` with IMAGE, NHITS, FLAGS, SPECTRAL_CHANNELS and WCS-WAVE. Zodiacal light is subtracted; there are stripes from atmospheric emission lines; the tiles use the HPX projection [AUG-products] [HIPS-nb].
- Intended for visualisation and context. It is time-averaged: built from DR1, MJD 60789–61174 [HIPS-props].

#### Comet 3I/ATLAS resources

[Page](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/index.html) [3I]: SPHEREx observed the comet in August and December 2025. The page offers a VOTable (`obscore_3I_Atlas.vot`) with one row per spectral image, the predicted comet position (`comet_ra`, `comet_dec`), times and access URL, and curl and wget scripts. It recommends IRSA Viewer for display and advises against the Spectrophotometry Tool because the comet is extended. **Checked:** the table has 259 rows, all `spherex_qr2`, MJD 60888–61024 (2025-08-07 to 2025-12-21), spread over D1–D6 [3I-table].

### 1.3 IRSA Viewer (Firefly)

[Application](https://irsa.ipac.caltech.edu/irsaviewer/) · [online help](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/) · [Firefly on GitHub](https://github.com/Caltech-IPAC/firefly).

- **What it is:** IRSA's general viewer for images, catalogues and charts, built on Firefly like the Data Explorer, so the visualisation tools in 1.1 apply [IV-overview] [IV-vis]. Images come from IRSA searches (Spitzer, WISE, Herschel, 2MASS, IRAS, ZTF, PTF, AKARI, DSS, SDSS, MSX and others), a URL, a local file or the Workspace. FITS, HiPS and ASDF formats; three-colour images from any surveys, resampled to the red image's pixels [IV-images]. The "Data Collections" tab draws each collection's MOC on a HiPS [IV-dce]. External searches: NED, multi-archive TAP, cone search, SIAv2 and CADC ObsCore [IV-overview].
- **Time Series Tool** [IV-TS]: upload a light-curve table (IPAC table); mission WISE/NEOWISE, PTF, ZTF or "Other".
  - For WISE/NEOWISE and PTF/ZTF it shows 1, 3, 5 or 7 single-epoch cutouts centred on the target and linked to the table and plot. For "Other" (where a SPHEREx light curve would go, inf.) only plots and period analysis work, without images.
  - Period Finder: manual entry, a slider (0.001–365 d by default), or a Lomb–Scargle periodogram from the NExScI periodogram service, with fixed frequency or period steps, defaults of twice the median time step to half the baseline, and the top 50 peaks.
  - "Accept" produces a phase-folded table and plot, with the images sorted by phase. Results download as tables, PNG plots, or images through "Prepare Download".
- **Firefly 2026 releases** [FF-rel]: HiPS data extraction (drill, line, point) from the underlying FITS (2026.4), direct download of job results, spectral line plotting and colour grouping by column in charts (2026.3), URL API support for SIA and the `hipsPanel` command and MOC 2.0 (2026.2), mask cubes, WebGPU rendering, an ecliptic HiPS frame, better iPad and iPhone support, a Rubin-only alert viewer, and a box mode for the search tool currently used only by SPHEREx (2026.1).
- **Embedding and programmatic access** [FF-api] [FF-readme]:
  - JavaScript: load `firefly_loader.js` from a Firefly server, then call `firefly.showImage`, `showTable`, `showChart`, `showCoverage`, the region-layer API and action listeners; `firefly.getViewer()` drives a viewer in another tab.
  - Python: `firefly_client`, for example `FireflyClient.make_client(url="https://irsa.ipac.caltech.edu/irsaviewer")`, then `show_hips` or a dispatch of `ImagePlotCntlr.ChangeHiPS` with `cubeIdx` [HIPS-nb].
  - Our own server: Docker (`docker run -p 8090:8080 -m 4g --rm ipac/firefly:latest`), Helm, or a standalone desktop install.
  - Embedding needs a Firefly (Java) server; whether IRSA's public server can be embedded from another origin is untested (inf.).

### 1.4 WiseView

[Application](http://byw.tools/wiseview), by Dan Caselden and Paul Westin and operated by the American Museum of Natural History. It shows unWISE time-resolved W1/W2 images with a Pan-STARRS image below [WV]. The challenge suggests it as a model [CHALLENGE].

- **Controls** (labels and tooltips in the page) [WV]:
  - Position: RA/Dec in decimal or sexagesimal only (no name resolver, inf.).
  - Field of view in arcseconds; band W1, W2 or W1+W2 colour (W1 blue, W2 red, green the average).
  - Sliding window (years): each frame co-adds all coadds within the window.
  - Blink interval in ms, zoom, minimum and maximum pixel values, "stretch linearity" (the asinh `a`), invert, maximise dynamic range.
  - Separate scan directions (shows parallax and some artifacts), NEOWISE only, skip middle epochs (first and last only), skip duplicate windows, smooth by scan direction, border on epoch 0 (a dashed border marks the loop restart).
  - Gaia DR3 overlay.
  - Difference imaging: everything inside the window minus everything outside it, with an exclusion window.
  - Shift and add at a given pmra/pmdec.
  - Two synthetic objects: RA/Dec at an MJD, W1/W2 magnitudes, proper motion, add or subtract.
- **Interaction:** an epoch legend (+, −, . for added, subtracted and unused epochs); click to recentre; drag to open a new tab with another field of view. A Links tab points to the Legacy Survey viewer, Pan-STARRS cutouts, SIMBAD, VizieR and the nearest Backyard Worlds (Zooniverse) subjects [WV] [WV-code].
- **From the source code** [WV-code]:
  - Every setting lives in the URL hash: `ra`, `dec`, `size` (at most 2000″), `band`, `speed`, `minbright`, `maxbright`, `window`, `diff_window`, `linear`, `color`, `zoom`, `border`, `gaia`, `invert`, `maxdyr`, `scandir`, `neowise`, `diff`, `outer_epochs`, `unique_window`, `smooth_scan`, `shift`, `pmra`, `pmdec`, `synth_a_*`, `synth_b_*`. Old v1 URLs are converted.
  - The default position is 133.786245 −7.244372, the WISE 0855−0714 field (inf. from the coordinates).
  - The Gaia DR3 overlay **moves each source to each frame's mean epoch**, draws its proper-motion path from the first to the last epoch, and scales the circle by parallax. The code propagates from MJD 57204 (2015.5, the Gaia DR2 epoch) although DR3 positions are at 2016.0, about 5″ of error for Barnard's Star (inf. from the code; we use each catalogue's own epoch [OURS-api]).
  - Frames come from an undocumented JSONP AWS Lambda endpoint (`…/default/png-animation`) as PNGs on S3. There is no public API (inf.).

### 1.5 Aladin Lite v3, ESASky, Legacy Survey Viewer

**Aladin Lite v3** (CDS) [AL] [AL-chlog] [AL-src] [NPM-AL] · [application](https://aladin.cds.unistra.fr/AladinLite/) · [API docs](https://cds-astro.github.io/aladin-lite/) · npm `aladin-lite`:

- An embeddable HiPS viewer with a Rust/WebGL2 core. It renders HiPS, including HiPS cubes (since 3.6.1, `HiPS.setSliceNumber()`; 3D HiPS not yet), FITS images (SIP keywords supported since 3.9.0-beta) and JPEG/PNG images with a WCS.
- Colour maps with linear, asinh, log, sqrt or pow stretches, cuts, opacity and additive blending.
- MOCs (fill or perimeter, save as JSON), catalogues (SIMBAD, NED, VizieR cones, progressive catalogues) and custom shapes, including an example that draws proper-motion vectors.
- Shareable view URL, PNG export of the view (`getViewImageBuffer`), dark and light UI, a HiPS browser.
- npm `aladin-lite`: stable 3.8.2 (2026-03-06), beta 3.9.2-beta (2026-09-15). LGPL-3.0-or-later; the Aladin logo and link must stay visible.
- No built-in blink or time-series features (inf.).

**ESASky** (ESA) [ESA-help] [ESA-int] [ESA-api] · [application](https://sky.esa.int):

- A multi-mission discovery portal: HiPS "skies" with opacity, stretch, cuts and blending, and **video-style buttons and a slider to play through the stack of skies** (a multi-survey blink).
- Treemaps of imaging, catalogue and spectral data; external TAP; target lists; a JWST planning tool; snapshots; sessions; ESA Datalabs.
- Solar-system object search for HST, Herschel and XMM-Newton images, with the orbit drawn.
- A time-series viewer for Gaia epoch photometry, XMM EPIC and OM, and CHEOPS, with time and SED views and a folded view whose period is looked up in the NASA Exoplanet Archive or Gaia DR3 variability.
- Bookmark URLs (`?action=goto&target=…&hips=…&fov=…&cooframe=…`), an iframe JavaScript API over `postMessage` (about 100 commands, including `playerPlay`, `addHips`, `addMOC`, `overlayCatalogue`, `plotSpectra`), an astroquery module and the EDDIE HiPS cutout service.

**Legacy Survey Viewer** [LS-tips] [LS-viewer] [LS-urls] · [application](https://www.legacysurvey.org/viewer):

- A Leaflet map of the Legacy Surveys up to DR11, unWISE W1/W2 layers (NEO2 to NEO11 coadds, cumulative rather than epochs (inf.)), Pan-STARRS, SDSS and others.
- **Blink between two layers with the space bar or `&blink=<layer>`**; marks with `&mark=ra,dec;…` and lines with `&poly=…`.
- Custom catalogue upload (CSV or FITS with RA, DEC, NAME, COLOR, RADIUS); catalogue overlays (Gaia DR2 and EDR3, Pan-STARRS, Tycho-2, SDSS, DESI).
- "Link Here" for sharing and "Discuss This Object" for the forum.
- Cutouts `cutout.jpg` and `cutout.fits` with `layer`, `pixscale`, `size` or `width`/`height`, `bands`, `subimage` and `invvar`.

## 2. Feature matrix

Cells: **Yes** documented; **Part.** partial or limited; **No** documented absent or not possible; **n/d** not documented (probably absent, inferred). "IRSA SPHEREx tools" names which tool. Tags point to [Sources](#sources).

### Search and discovery

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Name resolution | Yes: NED/SIMBAD, shows object type [DE-search] | SP and Mosaic: same target panel [SP] [MOSAIC]; MOST: JPL Horizons [MOST] | Yes [IV-images] | No, coordinates only [WV] | ESASky: SIMBAD [ESA-int]; Legacy: jump to object [LS-tips]; Aladin: n/d | Done\*: presets + CDS Sesame |
| Coordinate formats | Yes: decimal, sexagesimal, J-names, gal/ecl/B1950 [DE-search] | same panel | Yes [IV-images] | decimal, hms/dms [WV] | ESASky: equatorial or galactic [ESA-int] | Part.: decimal and sexagesimal Done; gal/ecl/B1950/J-names Missing |
| Presets | No | 3I/ATLAS exposure table [3I]; SPLICES catalogue [MISSION] | No | default view is WISE 0855 [WV-code] | ESASky and Legacy: random targets [ESA-help] [LS-tips] | Done: the five challenge targets |
| Proper-motion-aware target | No (inf.) | SP: no recentring, user must check high-PM sources [SP] | No (inf.) | Part.: shift-and-add at a user pm [WV] | n/d | Done: position propagated to every exposure |
| HiPS + MOC sky map, click to pick target | Yes: SPHEREx HiPS with wide and deep MOCs [DE-search] | SP and Mosaic use the same screen [SP] [MOSAIC] | Yes; Data Collections MOCs [IV-dce] | No | Yes, all three [AL] [ESA-int] [LS-tips] | Missing |
| Footprints of found exposures | Yes: Coverage tab [DE-results] | MOST: footprints + orbit plot [MOST] | Yes [IV-dce] | No | ESASky footprints [ESA-int] | Missing (TAP returns `poly`) |
| Search geometry | 5 query types, cone or polygon [DE-search] | MOST: ephemeris crossing [MOST] | cone, box, polygon, multi [DE-catalogs] | point + FOV [WV] | ESASky cone/polygon [ESA-api] | Part.: footprint contains the 2026.0 position (Done); FOV-intersects Missing |
| Time constraints | Yes: UTC/MJD range or last N days [DE-search] | SP: split by date when slow [SP]; Mosaic: none (inf.) | ObsCore time constraints [DE-othersearches] | NEOWISE-only toggle [WV] | n/d | Missing |
| Band or wavelength constraints | Yes [DE-search] | SP wavelength ranges [SP]; Mosaic range [MOSAIC] | Yes (ObsCore) | W1/W2 [WV] | ESASky wavelength slider [ESA-int] | Part.: fetches D1–D6, band chosen in viewer |
| Multi-object lists | Yes, union of images [DE-results] | SP: up to 20 per job [SP] | Yes [DE-catalogs] | No | ESASky target lists [ESA-help]; Legacy catalogue upload [LS-tips] | Missing |
| Releases | DR1 + QR3, wide + deep [DE-results] [AUG-access]; QR2 no longer searchable [AUG-releases] | SP: QR2/QR3/DR1 [SP]; Mosaic: wide only [MOSAIC] | through SIA/TAP | unWISE epochs [WV] | n/a | Done: DR1 + QR3 wide + deep, configurable |
| New-data awareness | Part.: "last N days" search; weekly ingestion [DE-search] [AUG-access] | No | No | No | No | Missing (Planned: release monitor) |

### Image display and interaction

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Animation of a time series | Part.: "Auto play" through loaded images; cube planes stepped by hand [DE-vis] | No | Part.: auto play [IV-vis] | Yes: blink with interval slider [WV] | Legacy: blink two layers [LS-tips]; ESASky: play through skies [ESA-int]; Aladin: n/d | In progress: epochs and single exposures |
| Per-epoch co-adds | No (inf.) | Mosaic co-adds all epochs [MOSAIC] | No | Yes: sliding window [WV] | No | Done |
| Difference images | No (inf.) | SDT: D2 − D4, colour not time [SDT] | No (inf.) | Yes: window minus the rest, exclusion window [WV] | No | In progress: against a reference epoch |
| Stack; first and last only | No | No | No | Yes: skip middle epochs, skip duplicate windows [WV] | No | In progress: `stack`, `ends=1` |
| False colour | Part.: image set (2 bands) [DE-vis] | No | Yes: any three images, hue-preserving stretch [IV-images] [DE-vis] | Yes: W1+W2 [WV] | colour HiPS, blending [ESA-int] [AL-src] | In progress: 3 detector groups |
| Stretch and colour maps | Yes: many tables, histogram editor, bias/contrast, NaN colour, invert [DE-vis] | same viewer | Yes [IV-vis] | asinh, min/max, invert, max dynamic range [WV] | Aladin: linear/asinh/log/sqrt/pow [AL-src]; ESASky: stretch and cuts [ESA-int] | In progress: asinh/linear/log, gray/ice, limits over all epochs; invert Missing |
| Same field across frames | Part.: WCS lock works only with cutouts on [DE-cutouts] | IBE cutouts keep parent axes [IBE] | Part. [IV-vis] | Yes (inf.) | HiPS always sky-aligned | Done: one north-up TAN grid for all frames |
| Zoom, pan, recentre | Yes [DE-vis] | No | Yes | zoom slider, click to recentre [WV] | Yes | In progress |
| Rotate and flip | Yes [DE-vis] | No | Yes | No | n/d | Missing (not needed on a north-up grid) |
| Compass, coordinate grid, scale bar | compass, grid [DE-vis] | No | Yes | dashed border at loop start, epoch legend [WV] | grid [AL] [ESA-api] | In progress: compass, scale bar; grid Missing |
| Pixel readout | Yes: 8 coordinate systems, value, **wavelength and bandwidth**, click-lock and copy [DE-vis] | No | Yes (λ for SPHEREx only in the DE, inf.) | n/d | cursor coordinates [ESA-int] | In progress: RA, Dec, value; λ and lock/copy Missing |
| Flag and mask overlays | Yes: per-bit mask layer with colours [DE-vis] | SP and Mosaic: inspect bits with it [SP] [MOSAIC] | Yes, mask cubes [FF-rel] | No | n/d | In progress: flags overlay with chosen bits; bitmask per epoch/detector Done |
| Tiles, side by side | Yes: up to 8 per page, image-set view [DE-vis] | SP: 1/3/5/7 linked cutouts [SP] | Yes; TS tool 1/3/5/7 [IV-TS] | unWISE + Pan-STARRS [WV] | No | Missing |
| Other HDUs (VARIANCE, ZODI, EPSF) | Yes [DE-vis] | Cutouts keep all HDUs [AUG-products] | Yes | n/a | n/a | Missing (used internally) |
| Wide-field context | Yes: HiPS coverage image [DE-images] | HiPS cube [HIPS-nb] | Yes | max 2000″ [WV-code] | Yes | Missing (FOV ≤ 30′) |

### Time domain

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Epoch (visit) grouping | No (inf.) | No (inf.) | No | sliding windows [WV] | No | Done |
| Light curve | Part.: plot any column [DE-plots] | Part.: SP output has `mjd` [SP] | Yes: TS tool [IV-TS] | No | ESASky TS viewer [ESA-int] | Done (data); chart In progress |
| Periodogram, phase folding | No | No | Yes: Lomb–Scargle, slider, folded table and images [IV-TS] | No | ESASky folded view with period lookup [ESA-int] | Missing |
| Spectrum per visit | Part.: colour spectrum by MJD [DE-spectra] [DE-examples] | Part. [SP] | Part. | No | ESASky SED view [ESA-int] | In progress: spectrum per epoch |
| Time points linked to images | Part.: sortable tables [DE-results] | SP: cutouts linked to points [SP] | Yes: images linked, sorted by phase [IV-TS] | epoch legend [WV] | No | In progress: timeline, frames table |
| Shift-and-add | No | No | No | Yes: pmra/pmdec [WV] | No | Missing |
| Older epochs (2MASS, WISE) | No (separate images only) | No | Part.: 2MASS, WISE, DSS images [IV-images] | Yes: unWISE 2010–2024 + Pan-STARRS [WV] | Legacy unWISE layers + blink [LS-viewer]; ESASky multi-survey player [ESA-int] | Missing (catalogue overlays only) |
| Synthetic object injection | No | No | No | Yes: two objects [WV] | No | Missing |

### Spectral

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Photometry in every exposure | through SP | SP: Tractor PSF fit, minutes to hours [SP] | No | n/a | n/a | Done: aperture photometry with per-pixel λ |
| PSF fitting, deblending | through SP | SP: simultaneous fit of listed sources [SP] | No | n/a | n/a | In progress: PSF for saturated stars; deblending Missing |
| Quick-look spectrum anywhere | drill on mosaic cubes [DE-vis] [MOSAIC] | drill on HiPS FITS cubes [HIPS-nb] | drill and point extraction; HiPS extraction [IV-vis] [FF-rel] | n/a | Aladin: cube slices only (inf.) | Missing (target only) |
| 102-channel images | Mosaic cube [MOSAIC] | HiPS cube [HIPS-props] | displays cubes | n/a | Aladin HiPS cube [AL] | Missing: 6 detector co-adds |
| Line lists, redshift | Yes: SPHEREx/Spitzer/Herschel + upload [DE-spectra] | No | Yes [FF-rel] | No | No | In progress: fixed molecular features |
| Quality per point | SP flags, `fit_ql`, `flux_bkg` [SP] | same | n/a | n/a | n/a | Done: `flagged`, `ap_flags`, `masked_frac` |
| Calibration (solid angle, wavelength) | SP: pixel solid-angle map [SP] | SDT: solid-angle map [SDT]; WCS-WAVE for display, CWAVE/CBAND for science [AUG-products] | n/a | n/a | n/a | Part.: `OMEGA_MEDIAN` + WCS-WAVE Done; solid-angle map and CWAVE/CBAND Missing |

### Catalogues and overlays

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Catalogue overlays | Yes: IRSA catalogues, TAP, cone, NED, uploads [DE-catalogs] [DE-overview] | SDT: SExtractor catalogue in Firefly [SDT] | Yes | Gaia DR3 [WV] | Aladin: SIMBAD, NED, VizieR [AL]; ESASky: many [ESA-help]; Legacy: Gaia, PS1, SDSS, DESI, Tycho-2 [LS-viewer] | Done\*: Gaia DR3, CatWISE2020, 2MASS (≤30′, ≤3,000 sources) |
| Positions moved to the image date | n/d (inf. no) | No | n/d | Yes: per frame, with pm path [WV-code] | Aladin: pm-vector example [AL] | In progress |
| High-PM highlighting | No (inf.) | No | No | pm paths [WV-code] | Aladin example [AL] | In progress |
| Predicted target track | target reticle only [DE-vis] | MOST predictions [MOST]; 3I `comet_ra/dec` [3I] | No | No | No | In progress |
| Large catalogues | hierarchical display [DE-catalogs] | No | Yes | n/a | Aladin progressive catalogues [AL-chlog] | Missing (not needed at ≤30′) |
| User catalogues and regions | upload, DS9 import/export [DE-upload] [DE-vis] | No | Yes | No | Legacy: catalogue upload, `mark=`/`poly=` [LS-tips] | Missing |
| Instrument footprints | Spitzer, SOFIA, HST, JWST, Roman [DE-vis] | No | Yes | No | ESASky JWST planning [ESA-help] | Missing (low value) |

### Measurement and analysis

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Distance and offset tool | Yes: ΔRA, ΔDec, angle [DE-vis] | No | Yes | n/d | n/d | Missing |
| Region statistics, centroid | Yes [DE-vis] | No | Yes | n/d | ESASky selection tool [ESA-int] | Missing |
| Proper motion measured from images | No (inf.) | No | No | No | No | Missing (validated offline [OURS-methods]) |
| Source detection | No | SDT: SExtractor [SDT] | No | No | No | Missing (Planned: ML moving-source candidates) |
| Table operations | Yes: filter, sort, derived columns [DE-tables] | same | Yes | n/a | n/d | In progress: frames table |
| Charts | scatter, heatmap, histogram, linked [DE-plots] | same | Yes | n/a | ESASky plots [ESA-api] | In progress: spectrum, light curve |

### Mosaics and cutouts

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Cutouts around a target | Yes: scissors, cutout downloads [DE-cutouts] | IBE URL API, full MEF [IBE] [CUT] | Yes | ≤2000″ [WV-code] | Legacy `cutout.jpg/.fits` [LS-tips]; ESASky EDDIE [ESA-help] | Done: S3 range reads, 1–30′ |
| Reprojected mosaics | No | Mosaic: to 5°×5°, 6.15″–3600″ pixels, rotation [MOSAIC] | No | No | HiPS only | Part.: north-up TAN at 6.15″, ≤30′ |
| Time-resolved mosaics | No | No (inf.) | No | sliding window [WV] | No | Done: per-epoch co-adds |
| Zodiacal light | ZODI HDU viewable [DE-vis] | Mosaic and HiPS cube subtract it [MOSAIC] [AUG-products]; SP fits local background [SP] | n/a | n/a | n/a | Done: local sigma-clipped background |

### Moving objects

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Solar-system objects | No: inertial targets only [DE-results] | MOST, SPHEREx included [MOST]; 3I table [3I] | through MOST links [MOST] | No | ESASky: HST, Herschel, XMM [ESA-int] | Missing |
| Orbit or track plot | No | MOST orbit plot [MOST] | No | No | ESASky orbit [ESA-int] | Missing for SSOs (star track In progress) |
| Object-frame stacking | No | No | No | constant-pm shift-and-add [WV] | No | Missing |
| High-proper-motion stars | No | No | No | shift-and-add, Gaia pm [WV] | n/a | Done |

### Data access and download

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Original files | download scripts (curl, wget, URL list) [DE-downloads] | 3I scripts [3I]; MOST FITS + DS9 tarballs [MOST] | "Prepare Download" [IV-TS] | No (inf.) | Legacy FITS cutouts [LS-tips] | Missing (paths are in `meta.json`) |
| Save the displayed image | FITS (cropped), PNG, DS9 region [DE-vis] | Mosaic: from image toolbar [MOSAIC] | Yes | No (inf.) | Aladin PNG [AL-chlog]; ESASky snapshot [ESA-help] | Missing |
| Table export | many formats; SP as VOTable [SP] [DE-tables] | same | Yes | n/a | n/a | Part.: JSON `meta.json` Done; CSV/VOTable Missing |
| Raw arrays over HTTP | No | No | No | No | No | Done\*: float32/uint arrays |
| Background jobs | Job Monitor, email, 14-day retention [DE-downloads] [SP] | SP and Mosaic: 2 running per user [SP] [MOSAIC] | Job Monitor | n/a | n/a | Done\*: jobs shared by request hash, not persistent |
| Data source | on-prem copy [DE-faq] | SP, Mosaic, Cutout on-prem [DE-faq]; SDT reads S3 [SDT] | IRSA services | unWISE PNGs on AWS [WV-code] | n/a | Done: `nasa-irsa-spherex` S3 range reads |

### Sharing and embedding

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| View state in the URL | Part.: search panel prefill or execute only [DE-api] | MOST is a GET API [MOST-test] | URL API [FF-rel] | Yes: all settings in the hash [WV-code] | ESASky bookmark URL [ESA-int]; Legacy "Link Here" [LS-tips]; Aladin share URL [AL] | In progress |
| Embeddable | n/d | No | firefly.js on a Firefly server [FF-api] [FF-readme] | n/d | Aladin JS/npm [AL]; ESASky iframe + postMessage [ESA-api] | In progress: `embed=1` |
| Links to other tools | SIMBAD/NED searches from regions [DE-vis] | MOST → IRSA Viewer [MOST] | No | Legacy, PS1, SIMBAD, VizieR, Zooniverse [WV] | Legacy: SIMBAD, LEDA, SDSS [LS-viewer] | Missing |

### Programmatic access

| Capability | SPHEREx Data Explorer | IRSA SPHEREx tools | IRSA Viewer | WiseView | Aladin / ESASky / Legacy | Ours |
|---|---|---|---|---|---|---|
| Public API | URL API; SIA2, TAP, cutouts, DataLink, S3 underneath [DE-api] [AUG-access] | MOST API + astroquery; IBE cutouts; HiPS and cube URLs; SP and Mosaic: none documented [MOST-nb] [IBE] [HIPS-nb] | `firefly_client`, JS API [FF-api] | No (undocumented JSONP endpoint) [WV-code] | Aladin JS/npm/ipyaladin; ESASky JS API + astroquery; Legacy URL patterns [AL] [ESA-api] [LS-urls] | Done\*: HTTP API, OpenAPI at `/docs` |
| Interface for AI agents | n/d | n/d | n/d | n/d | n/d | Missing (Planned: MCP server) |

## 3. Parity gaps ranked

Ranked by value for the challenge. Its objective is a public tool where users enter RA/Dec and get a short, video-like series of images of that patch in which moving objects are easy to spot, tested on Barnard's Star, ε Indi B, WISE 0855, RW Aurigae and R Draconis. The suggestions name per-detector co-adds, spectra on the fly, adding or subtracting epochs, a choice of field of view, overlays from Gaia or WISE, coordinates in the URL for embedding, contamination flags, easy updates with new releases, and tools such as Astropy and Aladin [CHALLENGE].

Not ranked here because they are already in progress, but they are parity-critical: blink, difference, stack, RGB, single-exposure playback, flags overlay, catalogue overlays at each epoch's date, frames table, charts, URL state and embed mode. WiseView and the Data Explorer already ship their equivalents [WV] [DE-vis].

### 1. Long-baseline comparison: 2MASS, AllWISE and NEOWISE epochs in the movie (M; L for NEOWISE visits)

- **Who has it:** WiseView (unWISE 2010–2024 + Pan-STARRS) [WV]; the Legacy Survey Viewer (unWISE layers, blink) [LS-tips]; IRSA Viewer (2MASS, WISE, DSS image search, tiles, auto play) [IV-images]; ESASky (multi-survey player) [ESA-int].
- **Why it matters:** SPHEREx pixels are 6.15″ and the data span about 1.5 years, so even the fastest targets move only 1–2 pixels; Barnard's Star moved 10″ (1.6 pixels) in a year [OURS-data] [OURS-methods]. Against 2MASS (about 1998–2001) or AllWISE (2010), the high-proper-motion targets move tens of arcseconds to arcminutes; Barnard's Star is already 4.5′ from its J2000 position [OURS-data]. The challenge asks for WISE overlays [CHALLENGE].
- **Implementation:**
  - Find images with one TAP query on `caom.observation/plane/artifact` for collections `twomass_allsky`, `wise_allwise` and `neowiser` (all present on 2026-11-14 [TAP-test]), using `CONTAINS(POINT('ICRS', ra, dec), p.poly)` at the target position propagated to each survey's epoch. AllWISE rows carry `time_bounds_*` (MJD 55273–55458 at Barnard's Star) [TAP-test].
  - Get pixels from IBE cutouts (`<access_url>?center=<ra>,<dec>&size=<fov>arcmin&gzip=false` [IBE]), or read AllWISE and NEOWISE-R frames with our range reader from `s3://nasa-irsa-wise/` (us-west-2; prefixes `wise/allwise/images/p3am_cdd/` and `wise/neowiser/images/p1bm_frm/`) [CLOUD]. Note the bucket region: run these reads near us-west-2 or accept higher latency (inf.).
  - Resample onto our north-up TAN grid, or a 2.75″ WISE grid with the same centre, so the blink can jump from "2MASS 1999" through "WISE 2010" to the SPHEREx epochs, with the predicted target track drawn.
  - For NEOWISE, group single exposures into about 6-month visits with our existing epoch code (`gap_days`) and co-add with the same outlier rejection, which gives about 20 W1/W2 epochs from 2014 to 2024 (inf.).
  - Quick win (S): an "Open in WiseView" link, `http://byw.tools/wiseview#ra=<deg>&dec=<deg>&size=<arcsec>&band=3&gaia=1&border=1`, using the hash keys in [WV-code].

### 2. Sky context map with Aladin Lite (M)

- **Who has it:** the Data Explorer search screen (SPHEREx HiPS, wide and deep MOCs, click or region to choose) and its Coverage tab [DE-search] [DE-results]; IRSA Viewer [IV-dce]; Aladin, ESASky and Legacy [AL] [ESA-int] [LS-tips].
- **Why it matters:** the challenge names Aladin [CHALLENGE]. A map shows where SPHEREx has data (including the unreleased Galactic Centre gap [DE-search]), gives context beyond our 30′ maximum, and lets users choose a target by clicking.
- **Implementation:**
  - `npm i aladin-lite@3.8.2` (stable; 3.9.x is beta [NPM-AL]). Keep the Aladin logo and link, which the licence terms require [AL].
  - Base layer: `A.imageHiPS('https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube')` with a channel slider through `setSliceNumber()` (frame = 17 × (band − 1) + (channel − 1)) [HIPS-props] [AL-src]. Check whether Aladin's slice index is 0- or 1-based; the HiPS declares `hips_cube_firstframe=1` and Firefly's `cubeIdx` is 0-based [HIPS-nb] (inf.).
  - Context layers from IRSA's mirrors, which avoid CDS outages: `https://irsa.ipac.caltech.edu/data/hips/CDS/2MASS/Color`, `…/CDS/AllWISE/W1`, `…/CDS/AllWISE/W2`, `…/CDS/DSS2/color` [HIPS-list]. CORS `*` is confirmed for the SPHEREx cube and 2MASS colour; the AllWISE and DSS mirrors are on the same server (inf.).
  - Draw our cutout box and every exposure footprint: add `p.poly` to the TAP SELECT and return it per exposure in `meta.json`, then use `A.graphicOverlay` and `A.polygon`.
  - Gaia sources from our `/api/catalogs/gaia` as an `A.catalog` with proper-motion vectors (the "show proper motions" example) [AL].
  - A click sets the target and starts a build.
  - Optional: a coverage MOC for "is there data here?" (inf.: IRSA's public MOC URL was not found; build one from TAP footprints or skip).

### 3. Solar-system objects through MOST (M)

- **Who has it:** MOST (SPHEREx catalogue) [MOST]; the Data Explorer FAQ points to MOST for SSO cutouts [DE-faq]; the 3I/ATLAS page lists exposures with predicted comet positions [3I]; ESASky SSO search [ESA-int].
- **Why it matters:** the challenge names comets and asteroids as moving objects [CHALLENGE]. None of the analogs stacks SPHEREx in an object's rest frame.
- **Implementation:**
  - `GET https://irsa.ipac.caltech.edu/cgi-bin/MOST/nph-most?catalog=spherex&input_type=name_input&obj_name=<name>&obj_type=Asteroid|Comet&obs_begin=YYYY-MM-DD&obs_end=YYYY-MM-DD&ephem_step=0.25&output_mode=Brief`. Tested: about 3 s and 160 DR1 rows for 3I/ATLAS [MOST-test]. Handle "multiple matches" by retrying with `input_type=naifid_input&obj_naifid=` [MOST-test], or call astroquery `Most.query_object()` [MOST-nb].
  - Parse `ra_obj`, `dec_obj`, `mjd_start`, `uri` (IPAC table; strip `ibe/data/spherex/` from `uri` for the S3 key, as we already do [OURS-data]).
  - Add a "track" target type that interpolates the ephemeris instead of a linear proper motion. Then build two views: the star-fixed blink (the object moves) and an object-centred co-add (the stars trail, the object stacks).
  - Add a 3I/ATLAS preset, cross-checked against `obscore_3I_Atlas.vot` [3I].

### 4. Shift-and-add and richer time arithmetic (S)

- **Who has it:** WiseView: shift and add at pmra/pmdec, sliding-window co-adds, difference of the window against everything outside it, exclusion window [WV].
- **Why it matters:** faint movers (the "Planet X" use case) need S/N from several epochs; the challenge suggests adding and subtracting images taken at different times [CHALLENGE].
- **Implementation:**
  - The pipeline already propagates a proper motion per exposure; add `comoving=true` so each exposure's grid centre follows (pmra, pmdec) × (t − t_ref) before co-adding.
  - Rebuilds from the permanent native-cutout cache take seconds (5.2 s for Barnard's Star [OURS-methods]).
  - Client side, for small fields, shift `exposures.bin` frames by sub-pixel bilinear offsets.
  - Add `mode=diff` with `ref=others`: epoch minus the noise-weighted mean of all other epochs.
  - Optional trial-motion grid search, the classic synthetic tracking (inf.), as a seed for the planned ML candidate ranking.

### 5. Spectrum and light curve for any position, on the fly (M)

- **Who has it:** the Spectrophotometry Tool (any position, up to 20 per job, minutes to hours) [SP]; Firefly drill and point extraction (quick look) [DE-vis]; the Source Discovery Tool (aperture spectra of selected detections) [SDT].
- **Why it matters:** the challenge gives extra credit for generating spectra on the fly [CHALLENGE]. Today we measure only the target.
- **Implementation:**
  - New endpoint `POST /api/sequences/{id}/measure {ra, dec, pmra, pmdec, ref_epoch}` that re-runs our forced photometry on the cached native cutouts and wavelength maps (both cached permanently [OURS-arch]); expected seconds (inf.).
  - Click on the canvas, or on a Gaia or CatWISE source (with its catalogue proper motion), to add a coloured spectrum and light curve.
  - Batch mode for every catalogue source in the field enables an SDT-style colour search (bright in D5/D6, faint in D1–D3, the signature WISE 0855 needs [OURS-methods]).

### 6. Measurement tools (S–M)

- **Who has it:** Firefly in the Data Explorer and IRSA Viewer: wavelength and bandwidth readout, click-lock with Python-format copy, distance tool with offsets, region statistics with centroid [DE-vis].
- **Why it matters:** seeing becomes measuring: how far did it move, in which direction, at what wavelength.
- **Implementation:**
  - Readout: add the epoch-and-detector wavelength range from `meta.epochs[].bands[]` (S). For per-pixel λ in single-exposure mode, precompute per-exposure wavelength maps on the grid (WCS-WAVE through the exposure WCS), stored as float16 like `exposures.bin` (M).
  - Click-lock copies `"RA Dec"` in decimal, sexagesimal and `SkyCoord(…)` form.
  - Distance and offset tool on the SVG overlay: Δα cos δ, Δδ, position angle.
  - Box centroid per epoch (flux-weighted, as validated in methods.md), then a weighted linear fit of position against time, giving µα\*, µδ ± σ, compared with Gaia DR3 from our catalogue endpoint.

### 7. Periodogram and phase folding (S)

- **Who has it:** IRSA Viewer Time Series Tool (Lomb–Scargle through the NExScI service, slider, folded table and plot, images sorted by phase) [IV-TS]; ESASky folded view with automatic period lookup [ESA-int].
- **Why it matters:** two of the five test targets are variable stars. R Draconis has Deep Survey coverage, 2,493 images with almost continuous sampling [OURS-data], so its period should be recoverable.
- **Implementation:**
  - `astropy.timeseries.LombScargle` on our per-detector relative fluxes (the light curve is already flux relative to the median spectrum [OURS-web]). Consider `LombScargleMultiband` (added in astropy 5.3 [ASTROPY-CHANGES]) across D1–D6.
  - Period range from twice the median Δt to half the baseline, as IRSA's defaults [IV-TS].
  - Plot the window function: aliases at the about 98-minute orbit and the 6-month visits are likely (inf.).
  - A slider and phase-folded plot, with points linked to the exposure frame they came from.

### 8. Exports (S–M)

- **Who has it:** the Data Explorer (FITS, PNG, region; download scripts) [DE-vis] [DE-downloads]; the Spectrophotometry Tool (VOTable) [SP]; Legacy (`cutout.fits`) [LS-tips]; Aladin (PNG) [AL-chlog].
- **Why it matters:** a public tool should let people keep and share what they found, and scientists will want FITS. The Time Series Tool takes uploaded light curves as IPAC tables (mission "Other") [IV-TS]; IRSA Viewer and the Data Explorer also read VOTable, CSV and Parquet [DE-upload].
- **Implementation:**
  - `GET /api/sequences/{id}/sequence.fits`: one image HDU per epoch and detector with a TAN WCS (CRVAL = grid centre, CDELT1 = −6.15″, CDELT2 = +6.15″, north up), coverage and flag HDUs, and BINTABLEs for exposures and photometry (`astropy.io.fits`).
  - Photometry as CSV, ECSV, VOTable or IPAC table (`astropy.table.Table.write(format='votable')` or `format='ascii.ipac'`).
  - Provenance script: curl lines for the S3 objects plus IBE cutout URLs, like the Data Explorer's script [DE-downloads].
  - Client: PNG with overlays (`canvas.toBlob`), animated GIF (`gifenc` on npm) or WebM/MP4 from the canvas (`MediaRecorder`).

### 9. ePSF photometry with neighbours; science-grade calibration (M–L; calibration S)

- **Who has it:** the Spectrophotometry Tool: Tractor PSF fits, simultaneous companions, `fit_ql`, `flux_bkg`, flag bits 32 and 33, µJy from the solid-angle map [SP].
- **Why it matters:** blends within about 10″ contaminate our apertures (WISE 0855 shows flux in D1–D3 [OURS-methods]); bright stars saturate. PSF photometry for saturated stars is already in progress; this extends it to neighbours.
- **Implementation:**
  - Use the EPSF binary table in each file (columns `BINX`, `BINY`, `XCENTER`, `YCENTER`, `NEFF_MEAN`, `C_MEAN`, `EPSF`; header `OVSMPX`/`OVSMPY`; 21×21 zones, 11×41 for D3; 33×33 at 5× oversampling). Take the zone nearest the parent-pixel position [PSF-nb] [AUG-products].
  - Fit target plus Gaia and CatWISE neighbours propagated to each exposure's MJD, plus a constant background, by linear least squares with 1/VARIANCE weights. Report `fit_ql` as IRSA does.
  - Calibration (S): read `solid_angle_pixel_map/cal-sapm-v3-2026-191/{k}/…` (the path used by the SDT [SDT]) and `spectral_wcs/…` CWAVE/CBAND once per detector and cache them like the wavelength maps. The user guide says WCS-WAVE is meant for visualisation and CWAVE/CBAND for science analysis [AUG-products].

### 10. Release monitor, freshness, filters (S)

- **Who has it:** the Data Explorer ("completed in the last N days", date and band constraints) [DE-search]; weekly ingestion [AUG-access].
- **Why it matters:** the challenge asks for a tool that is easy to update as new releases arrive [CHALLENGE].
- **Implementation:**
  - A scheduled TAP query (`SELECT MAX(p.time_bounds_upper), COUNT(*) FROM spherex.plane p JOIN spherex.observation o ON p.obsid = o.obsid WHERE o.collection IN (…)`), stored per collection.
  - For an open sequence, re-run the cheap search and show "N new exposures since this build" with a rebuild button.
  - `date_min`, `date_max` and `detectors` request fields that cut reads for large fields.

### 11–20. Lower-ranked gaps

| # | Gap | Who has it | Effort | Implementation note |
|---|---|---|---|---|
| 11 | Links out to other tools | WiseView, Legacy, Data Explorer [WV] [LS-viewer] [DE-vis] | S | Data Explorer `?api=spectral-images&ra=&dec=&spatialRegionOperation=contains_point&execute=true` [DE-api]; WiseView hash [WV-code]; Legacy `?ra=&dec=&layer=unwise-neo7&blink=ls-dr10&mark=ra,dec` [LS-tips]; ESASky `?action=goto&target=ra%20dec&fov=` [ESA-int]; MOST for SSOs |
| 12 | 102-channel narrow-band images and line maps | Mosaic Tool, HiPS cube [MOSAIC] [HIPS-props] | M | Assign each pixel to a channel with the Spectral Channels product (`spectral_channels/cal-sch-v*`, index per pixel [AUG-products]) or per-pixel λ; co-add per (epoch, channel); continuum-subtracted maps (CO₂ 4.27 µm, H₂O ice 3.05 µm, PAH 3.3 µm). Coverage per channel per visit will be sparse (inf.) |
| 13 | Search geometry and input formats | Data Explorer [DE-search] | S | `1 = INTERSECTS(CIRCLE('ICRS', ra, dec, r), p.poly)` for wide fields; parse `gal`/`ecl`/`b1950`, J-names and `G` names (`astropy.coordinates`); fill proper motion for resolved names from SIMBAD TAP `basic.pmra/pmdec` when CDS is up, or the nearest Gaia DR3 match (inf.) |
| 14 | Target lists | Data Explorer, SP (≤20) [DE-results] [SP] | M | Upload CSV with `ra`, `dec`, optional pm; queue one sequence per row (ids are request hashes, so duplicates are shared [OURS-api]) |
| 15 | Choosing the mask policy | Data Explorer mask layer per bit [DE-vis] | S | Expose the image-mask and photometry-bad bit sets (ADR-0008) as request fields; part of the cache key |
| 16 | Line lists, redshift, upload | Data Explorer and Firefly [DE-spectra] [FF-rel] | S | Selectable lists (SPHEREx-relevant ices and molecules, H I lines) and a CSV upload; a redshift field for galaxies |
| 17 | Persistent jobs and history | Data Explorer Job Monitor [DE-downloads] | S–M | Persist job state (SQLite) so restarts resume; "recent targets" list in localStorage |
| 18 | Region files and user catalogues | Firefly, Legacy [DE-vis] [LS-tips] | S | Import CSV/FITS with RA, DEC, NAME, COLOR, RADIUS like Legacy; export DS9 regions of overlays |
| 19 | Synthetic source injection | WiseView [WV] | M | Add PSF-shaped sources (ePSF) with magnitude and motion to cached exposures before co-adding; useful for completeness tests and ML training labels |
| 20 | Light curves from other missions | IRSA TS tool (WISE, ZTF), ESASky (Gaia, XMM, CHEOPS) [IV-TS] [ESA-int] | M | NEOWISE-R single-exposure source table (HEALPix Parquet, `s3://nasa-irsa-wise/wise/neowiser/catalogs/p1bs_psd/healpix_k5/`) and the unWISE NEO7 time-domain catalogue (`unwise/neo7/catalogs/time_domain/healpix_k5/`) [CLOUD]; Gaia DR3 epoch photometry through the Gaia archive (inf.) |

Not worth chasing now: arbitrary rotation and flip, instrument footprints (HST, JWST, Roman), access to ZODI/VARIANCE/EPSF HDUs in the UI, hierarchical catalogue rendering, mosaics up to 5° (use the HiPS cube for context instead), login, email notification.

## 4. What none of them has

Things our tool does, or could do, that no analog offers for SPHEREx:

**Done or in progress**

- **Proper-motion-aware SPHEREx sequences.** Each exposure is measured at the propagated position, so fast movers stay in the field [OURS-data]. The Spectrophotometry Tool neither recentres nor propagates [SP]; WiseView shifts at a user-given motion but does not track a catalogue target [WV].
- **Epochs, co-adds and differences for SPHEREx.** Visits grouped automatically, co-added per detector with outlier rejection, then blinked, differenced or stacked [OURS-methods]. The Data Explorer only auto-plays single exposures [DE-vis]; the Mosaic Tool and the HiPS cube average all epochs [MOSAIC] [AUG-products].
- **Spectrum per visit plus light curve from one request,** with the exact wavelength of every pixel and flags per point [OURS-methods]. In IRSA's tools this takes a Spectrophotometry job (about 8 to 70 minutes for our five targets by IRSA's run-time formula and our image counts [SP] [OURS-data]) and manual colouring by MJD [DE-spectra].
- **Catalogues moved to each epoch's date** (Gaia DR3, CatWISE2020, 2MASS) with high-proper-motion highlighting [OURS-web]. WiseView does this for Gaia only, on unWISE [WV-code].
- **Independence from IRSA's busy services.** Range reads from the public S3 bucket fetch about 2 MB per exposure for a 6′ field: Barnard's Star's 358 exposures in 37.7 s, while the cutout service answered `503` [OURS-data].
- **Full view state in the URL and an embed mode** for a SPHEREx time sequence [OURS-web]. The Data Explorer's URL API only fills in a search [DE-api].
- **Raw arrays over an open-CORS HTTP API** [OURS-api], and an MCP server for AI agents (Planned). No analog offers an agent interface.

**Could have**

- **One motion movie from 1999 to 2026:** 2MASS → AllWISE → NEOWISE visits → SPHEREx epochs on one grid with the predicted track (gap 1). WiseView covers WISE only; IRSA Viewer shows surveys side by side without alignment in time.
- **Object-frame stacks for comets and asteroids** from MOST ephemerides (gap 3), with coma spectra in molecular bands (CO₂ 4.27 µm) per visit.
- **Spectral variability views:** a wavelength × time "dynamic spectrum" image, and spectrum differences between visits for RW Aur and R Dra. The analogs only colour points by date [DE-spectra].
- **Narrow-band "molecule movies"** (ice, CO₂, PAH line maps per epoch; gap 12), a direct answer to the challenge's call to display the 102 bands creatively [CHALLENGE].
- **Brown-dwarf finder:** combine motion (co-moving stacks, gap 4) with SPHEREx colour (D5/D6 bright, D1–D3 faint) across the field. The SDT does colour without motion [SDT]; WiseView does motion without spectra [WV].
- **Measured proper motion with uncertainty** from per-epoch centroids, checked against Gaia (gap 6).
- **ML ranking and citizen-science loop:** candidate movers ranked real/bogus, with a "flag this" button feeding a shared list, the Backyard Worlds model the challenge cites [CHALLENGE]. WiseView only links to Zooniverse subjects [WV].
- **Shareable GIF/MP4 movies** for a public audience (gap 8); none of the analogs exports animations (inf.).

How the analogs fit the five test targets:

| Target | Analog experience | Our edge |
|---|---|---|
| Barnard's Star | Saturated in SPHEREx [OURS-methods]; the FAQ attributes alternating spectra of targets near 1 Jy to saturation [DE-faq]; SP needs an exact position and does not propagate [SP] | Motion visible after proper-motion propagation; per-visit positions match to < 1″ [OURS-methods]; PSF photometry in progress |
| ε Indi B | Data Explorer and SP work per exposure; no epoch view (inf.) | 16′ preset that keeps ε Indi A in view [OURS-api]; epoch blink |
| WISE 0855−0714 | WiseView's default field, in W2 (inf. from the default coordinates) [WV-code]; in SPHEREx we see flux only where expected in D5/D6, while D1–D3 apertures are contaminated [OURS-methods] | Motion across SPHEREx epochs plus a WISE baseline (gap 1); deblending (gap 9) |
| RW Aurigae | Spectra coloured by MJD reveal epoch-to-epoch changes [DE-spectra] | Spectrum per visit and light curve; periodogram (gap 7) |
| R Draconis | Deep Survey coverage: an SP run would take about 70 min for 2,493 images by IRSA's formula [SP] [OURS-data] | Near-continuous light curve; period (gap 7) |

## Checks we ran

All read-only, on 2026-11-14.

- **MOST, SPHEREx** [MOST-test]: `nph-most?catalog=spherex&input_type=name_input&obj_name=3I&obj_type=Comet&obs_begin=2025-08-01&obs_end=2025-08-20&ephem_step=0.25&output_mode=Brief` → HTTP 200 in 2.9 s, 160 rows; the first rows are `ra_obj` 252.664, `dec_obj` −17.4905 with `uri` under `ibe/data/spherex/dr1/level2/2025W31_2A/…`. `obj_name=Ceres` → HTTP 400 "Multiple matches" with NAIF ID hints; `input_type=naifid_input&obj_naifid=20000001` for 2025-09-01 to 2025-10-15 → 200, no matched SPHEREx images in that window.
- **IRSA TAP collections** [TAP-test]: `SELECT DISTINCT o.collection FROM caom.observation o WHERE …` lists `neowiser`, `wise_allwise`, `wise_unwise`, `twomass_allsky`, `spherex_dr1`, `spherex_dr1_cube`, `spherex_dr1_cal`, `spherex_dr1_deep`, `spherex_qr3`, `spherex_qr3_deep` and others. A query at Barnard's Star returns AllWISE Atlas tiles with time bounds and unWISE coadds without time bounds.
- **HiPS CORS**: `properties` and an `Norder3` PNG tile of the SPHEREx cube, and the 2MASS colour HiPS properties, return `access-control-allow-origin: *`.
- **3I/ATLAS table** [3I-table]: 259 rows, 26 columns, collection `spherex_qr2`, MJD 60888.3–61024.0, detectors D1 46, D2 44, D3 41, D4 48, D5 44, D6 36.

## Sources

SPHEREx Data Explorer online help (base `https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/`):

- [DE-overview] [overview.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/overview.html)
- [DE-search] [searching.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/searching.html)
- [DE-results] [results.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/results.html)
- [DE-images] [images.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/images.html)
- [DE-vis] [visualization.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/visualization.html)
- [DE-cutouts] [cutouts.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/cutouts.html)
- [DE-downloads] [downloads.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/downloads.html)
- [DE-api] [api.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/api.html)
- [DE-examples] [examples.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/examples.html)
- [DE-faq] [faq.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/faq.html)
- [DE-spectra] [spectra.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/spectra.html)
- [DE-catalogs] [catalogs.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/catalogs.html)
- [DE-tables] [tables.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/tables.html)
- [DE-plots] [plots.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/plots.html)
- [DE-upload] [upload.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/upload.html)
- [DE-othersearches] [othersearches.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/othersearches.html)
- [SP] [sp.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/sp.html) (Spectrophotometry Tool)
- [MOSAIC] [mosaic.html](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/mosaic.html) (Mosaic Tool)

SPHEREx Archive User Guide and IRSA pages:

- [AUG-access] [SPHEREx Data Access](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-access)
- [AUG-products] [SPHEREx Data Products](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products)
- [AUG-releases] [SPHEREx Data Releases](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-releases)
- [MISSION] [IRSA SPHEREx mission page](https://irsa.ipac.caltech.edu/Missions/spherex.html)
- [CUT] [SPHEREx Cutout Capabilities](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/cutout_tool.html)
- [IBE] [IRSA Image Server Cutouts](https://irsa.ipac.caltech.edu/ibe/cutouts.html)
- [3I] [SPHEREx observations of Comet 3I/ATLAS](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/index.html)
- [3I-table] [obscore_3I_Atlas.vot](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/obscore_3I_Atlas.vot), downloaded and summarised by us
- [CLOUD] [IRSA cloud data access](https://irsa.ipac.caltech.edu/cloud_access/)
- [HIPS-list] [IRSA HiPS list](https://irsa.ipac.caltech.edu/data/hips/list)
- [HIPS-props] [SPHEREx all-sky cube HiPS properties](https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube/properties)
- [MOST] [MOST application](https://irsa.ipac.caltech.edu/applications/MOST/) and [instructions](https://irsa.ipac.caltech.edu/applications/MOST/instructions.html)
- [MOST-test], [TAP-test]: our own calls, described in [Checks we ran](#checks-we-ran)

IRSA tutorial notebooks (sources on GitHub, [Caltech-IPAC/irsa-tutorials](https://github.com/Caltech-IPAC/irsa-tutorials)):

- [SDT] [SPHEREx Source Discovery Tool demo](https://caltech-ipac.github.io/irsa-tutorials/spherex-source-discovery-tool-demo/)
- [HIPS-nb] [SPHEREx HiPS cubes](https://caltech-ipac.github.io/irsa-tutorials/spherex-hipscube/)
- [MOSAIC-nb] [SPHEREx mosaic cubes](https://caltech-ipac.github.io/irsa-tutorials/spherex-mosaic/)
- [PSF-nb] [ePSF in SPHEREx cutouts](https://caltech-ipac.github.io/irsa-tutorials/spherex-psf/)
- [MOST-nb] [MOST queries](https://caltech-ipac.github.io/irsa-tutorials/most-queries/)
- [ASTROQUERY-MOST] [astroquery `ipac/irsa/most.py`](https://github.com/astropy/astroquery/blob/main/astroquery/ipac/irsa/most.py) and its `__init__.py` (server URL)
- [ASTROPY-CHANGES] [astropy CHANGES.rst](https://github.com/astropy/astropy/blob/main/CHANGES.rst), version 5.3: `LombScargleMultiband` added

IRSA Viewer and Firefly:

- [IV-overview] [overview.html](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/overview.html)
- [IV-images] [images.html](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/images.html)
- [IV-vis] [visualization.html](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/visualization.html)
- [IV-dce] [dce.html](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/dce.html)
- [IV-TS] Time Series Tool help: [overviewTSV](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/overviewTSV.html), [loadingTSV](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/loadingTSV.html), [main1TSV](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/main1TSV.html), [findpTSV](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/findpTSV.html), [main2TSV](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/main2TSV.html), [saveTSV](https://irsa.ipac.caltech.edu/onlinehelp/irsaviewer/irsaviewer/saveTSV.html)
- [FF-readme] [Firefly README](https://github.com/Caltech-IPAC/firefly/blob/dev/README.md) and [standalone install](https://github.com/Caltech-IPAC/firefly/blob/dev/docs/using-firefly-standalone.md)
- [FF-api] [Firefly JavaScript API overview](https://github.com/Caltech-IPAC/firefly/blob/dev/docs/firefly-api-overview.md)
- [FF-rel] [Firefly release notes](https://github.com/Caltech-IPAC/firefly/blob/dev/docs/release-notes.md) (2026.1–2026.4)

WiseView:

- [WV] [WiseView page](http://byw.tools/wiseview): control labels, tooltips, Help and About tabs
- [WV-code] [WiseView script `static/flash3.js`](http://byw.tools/static/flash3.js): URL hash, cutout endpoint, Gaia overlay

Aladin Lite, ESASky, Legacy Survey:

- [AL] [Aladin Lite README](https://github.com/cds-astro/aladin-lite/blob/master/README.md)
- [AL-chlog] [Aladin Lite CHANGELOG](https://github.com/cds-astro/aladin-lite/blob/master/CHANGELOG.md)
- [AL-src] [Aladin Lite `src/js/HiPS.js`](https://github.com/cds-astro/aladin-lite/blob/master/src/js/HiPS.js) (`setSliceNumber`, stretches)
- [NPM-AL] [npm registry entry for `aladin-lite`](https://registry.npmjs.org/aladin-lite)
- [ESA-help] [ESASky help](https://www.cosmos.esa.int/web/esdc/esasky-help)
- [ESA-int] [ESASky interface](https://www.cosmos.esa.int/web/esdc/esasky-interface)
- [ESA-api] [ESASky JavaScript API](https://www.cosmos.esa.int/web/esdc/esasky-javascript-api)
- [LS-tips] [Legacy Survey Sky Viewer tips](https://www.legacysurvey.org/svtips/)
- [LS-viewer] [Legacy Survey viewer page source](https://www.legacysurvey.org/viewer): layer and overlay definitions, blink handler
- [LS-urls] [Legacy Survey URL patterns](https://www.legacysurvey.org/viewer/urls)

Challenge and our own documents:

- [CHALLENGE] [Planet X and SPHEREx challenge page](https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/): objectives, potential considerations, resources (text read from the page source)
- [OURS] [README.md](../../README.md) and the research brief
- [OURS-data] [docs/data.md](../data.md)
- [OURS-methods] [docs/methods.md](../methods.md)
- [OURS-api] [docs/api.md](../api.md)
- [OURS-arch] [docs/architecture.md](../architecture.md)
- [OURS-web] [web/README.md](../../web/README.md)
