# Astrometrica features for SPHEREx

What Herbert Raab's Astrometrica does for minor-planet and comet observers, which of those features make sense for SPHEREx Level 2 data, what our tool already has, and what to build. Researched on 2026-11-14.

**Method.** We read Astrometrica's own pages (home, downloads, version history, star catalogues, the author's papers), copies of the online help (the Program-tab help reproduced by Durham AstroLab), four user tutorials (Durham, EURONEAR, UAI, BAA), observer guides that use it (Birtwhistle, iTelescope), and the MPC's astrometry and ADES documentation. The online help itself ships only inside the Windows installer; we did not download or run the program. The live site at astrometrica.at answers `404` to every GET from our client (HEAD says `200`), so its pages were read from the Wayback Machine snapshot of 2026-11-06 (site footer: "Last Update: 2025 Aug. 07"). A few read-only API calls checked facts about SPHEREx; they are marked "tested" and listed in [Checks we ran](#checks-we-ran). Source tags in square brackets resolve in [Sources](#sources); `[AM-VH 4.3.3]` means the version-history entry for that release. **(inf.)** marks our inference.

**Status of "ours"** comes from the [README](../../README.md), [methods](../methods.md), [API](../api.md), [web/README](../../web/README.md), [analogs.md](analogs.md) and the working tree on 2026-11-14 [OURS]. Done = ran on real data; Done\* = code runs, not checked end to end; **In code** = present in `backend/spherex_core/field/` (the "Analyze this field" package: detection, cross-match, motion search, intra-visit tracklets, real/bogus, ADES draft), which has no API route, ADR or UI yet; Missing.

**Effort** as in analogs.md: S = up to half a day, M = one to two days, L = more than two days, for one developer.

## Summary

Top 12 to build, ranked by value for finding moving and variable objects in SPHEREx (details in [section 3](#3-ranked-build-list)):

| # | Build | Astrometrica feature it mirrors | Effort |
|---|---|---|---|
| 1 | Known-object overlay computed from SPHEREx's own position (Horizons state vector → JPL `sb_ident` with `xobs`) | Known Object Overlay, MPCOrb | M |
| 2 | Moving Object Detection in the product: expose the intra-visit tracklet search, mark candidates in the blink, candidate list | Moving Object Detection | M |
| 3 | Track & Stack inside a visit at a user motion (″/h, PA): sum/mean/median, re-stack, sub-stacks | Track & Stack, Re-Stack | M |
| 4 | Verification panel: click → ePSF fit of position and flux on the native exposures, SNR, FWHM, χ², σ, stare/track strip | Object Verification window | S–M |
| 5 | Simultaneous-pair and spectrum-aware checks for detections (D1/D4, D2/D5, D3/D6 see the same sky at the same time) | "star = seen at the same place on ≥ 2 images"; FWHM and PSF-RMS limits | S |
| 6 | Identification against known orbits with motion criteria and O–C | Object identification, designation browser | S |
| 7 | Plate-solution QA against Gaia per exposure: residuals, `FINAST`, local LSPC refit | Data Reduction results, reference-star residuals | M |
| 8 | ADES (and 80-column) export for tracklets that is correct for a space-based station | MPCReport / ADES output | M |
| 9 | Per-exposure photometric zero-point check and ensemble (differential) light curves | Photometric reduction, PhotReport | M |
| 10 | Image Catalog: every detection in a table with type, flux, FWHM, SNR, residuals; CSV | Image Catalog | S |
| 11 | Measuring tools: distance and position angle, centroid and pixel modes, click-lock copy | Shift-drag offsets, Ctrl/Shift+Ctrl click | S |
| 12 | Display and markings: invert, log and apply-to-all limits, loupe, marking toggles and a "find them yourself" mode | Background and Range, Magnifier, Select Markings | S |

Main findings:

- **Most of Astrometrica's pipeline is already done for us by the SPHEREx Level 2 processing.** Darks, flats, gain, non-linearity and persistence are applied, and every image gets a Gaia-based WCS (Astrometry.net then SCAMP) with a `FINAST` quality keyword; images reach IRSA only with `FINAST = 0` (rms < 1″), and the median alignment precision is 0.1–0.4″ [SPX-PIPE]. Calibration, star-pattern matching, focal length and orientation settings have no role. What remains useful is Astrometrica's **detection, verification, identification, stacking and reporting** layer.
- **SPHEREx is a space-based observer, and that matters for known-object positions.** Its geocentric distance is about 7,030 km (tested), so an object at Δ AU can appear up to ≈ 9.7″/Δ (1.6 px/Δ) away from its geocentric position. Tested for 433 Eros at 0.98 AU: Horizons from SPHEREx and from the geocentre differ by 9″ [HZ-test]. IRSA MOST's `ra_obj`/`dec_obj` are SPHEREx-centred (they match Horizons with SPHEREx as centre to < 0.01″, while the geocentre is off by up to 3.7″) [MOST-test]. SkyBoT with the MPC code 288 (SPHEREx) returns the geocentric position [SKYBOT-test]. Our draft known-object check (`field/transients.py`) queries `sb_ident` geocentrically and assumes "at most a few arcseconds"; that holds only beyond about 2–3 AU.
- **The same parallax moves nearby objects around the 98-minute orbit,** so a straight-line, constant-rate tracklet in sky coordinates is only an approximation: ≈ 1 px of wobble at 1.5 AU and ≈ 16 px at 0.1 AU (inf., from the 7,030 km radius). Linking near-Earth objects needs the observer position in the model; Astrometrica, built for ground observers with a few frames over an hour, never faced this.
- **The interesting SPHEREx time scales are inside a visit.** Asteroids move degrees between the 6-month epochs, so Astrometrica's three-to-six-frame logic maps onto the exposures within a visit (minutes to days apart), not onto the epochs. A distant object at quadrature (SPHEREx's viewing geometry) moves only about 0.5–1″/h (inf.), so a multi-day Track & Stack within one visit is the "Planet X" search; between epochs it shifts by degrees.
- **SPHEREx has properties Astrometrica cannot use and we should:** pairs of detectors image the same field simultaneously (a free coincidence test against cosmic rays and artefacts), every frame is a narrow band (brightness changes between frames are expected and follow the spectrum, so Astrometrica's equal-brightness assumption must become a spectral one), and each exposure carries `VARIANCE`, `FLAGS` and an ePSF.
- **Reporting:** MPC observatory code 288 is SPHEREx [MPC-CODES]. The MPC prefers ADES; ADES submissions go by web form or cURL, not e-mail [MPC-REP]. ADES has a note `Z`, "astrometry from a survey reported by a non-survey measurer/pipeline", and space-based position fields (`sys` `ICRF_KM`) [MPC-ADESV]. SPHEREx's 102 narrow bands are not valid MPC magnitude bands [MPC-80] [MPC-ADESV]. Treat exports as follow-up files unless the team decides with the MPC to submit.
- **Caveat for precision:** SPHEREx pixels are 6.15″ with a PSF 1.2–1.5 px wide [OURS-data]; the MPC recommends at most 2″/px, 3″ at worst [MPC-GS]. By Raab's own estimate σ_pos ≈ σ_PSF / SNR [AM-PSF], a SNR 10 detection with FWHM 8″ gives about 0.34″ plus the WCS error. Useful, but measurements must carry honest uncertainties.

## SPHEREx versus the data Astrometrica expects

| | Astrometrica's input [AM] [AM-SET] [AM-VH] | SPHEREx Level 2 [OURS-data] [SPX-PIPE] |
|---|---|---|
| Calibration | raw or calibrated CCD frames; user darks and flats | calibrated MJy/sr; dark, flat, gain, non-linearity applied; persistence, transients and outliers flagged |
| Astrometry | solved by Astrometrica from catalogue stars (pattern match, plate constants) | per-image Gaia solution with SIP distortion; `FINAST` = 0 for rms < 1″; 0.1–0.4″ median |
| Pixels | typically 1–3″, well sampled | 6.15″, PSF FWHM 7.5–9″ (1.2–1.5 px), undersampled |
| Bands | one filter per night (R, V, clear…) | 102 channels from linear variable filters; each detector covers 17; a small cutout is one narrow band |
| Frames per object | 3–6 for moving-object detection, up to 25 loaded [AM-VH 4.4.0] | hundreds per field; within a visit, exposures minutes to days apart; visits ~6 months apart |
| Exposure | seconds to minutes; timestamp keywords set by the user | 113.6 s (`XPOSURE`), mid-time `MJD-AVG` |
| Observer | a fixed site with an MPC code (or roving) | low Earth orbit, MPC code 288, ~7,030 km from the geocentre (tested) |
| Simultaneity | none | D1+D4, D2+D5, D3+D6 see the same field at the same time [OURS-data] (via analogs.md) |

## 1–2. Feature inventory and fit for SPHEREx

Each table lists Astrometrica's feature with its source, whether it applies to SPHEREx Level 2, what we have, and what to build (effort and implementation note). IDs (A1, B2…) are used in section 3.

### A. Image loading and calibration

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| A1 | Reads FITS (8/16/32-bit integer, 32-bit float since 4.4.0) and SBIG/PixCel files; size limited by memory; up to 25 images at once [AM] [AM-VH 4.1.0, 4.4.0] | Yes, as "load all exposures of a field"; the format question is solved | Done: S3 range reads of `IMAGE`/`FLAGS`/`VARIANCE` for every exposure of a position, up to 30′ [OURS-methods] | None |
| A2 | Dark-frame and flat-field calibration, automatic on load; asks whether to continue when sizes differ; zero flat pixels left unchanged [AM] [AM-VH 4.3.0, 4.3.2] [UAI] | No: Level 2 is calibrated [SPX-PIPE]. The equivalent is background and masking | Done: flag masking, sigma-clipped local background; zodiacal light not subtracted but removed by the local background [OURS-methods] | None |
| A3 | Time from the header: start, middle or end of exposure; offset from UT; precision; "check time after loading"; constant time-delay correction (4.16.1); `TIME-OBS`/`UT` as string or decimal hours [AM-VH 4.0.7, 4.1.1, 4.3.3, 4.16.1] [BAA] [DUR] | Partly: `MJD-AVG` is the mid-time, so there is nothing to configure; but stacks and tracklets need correct mid-times, and MOST gives positions at exposure start | Done: `MJD-AVG` used for photometry and positions; the ephemeris interpolation accounts for MOST's start times [OURS-code] | S: show `MJD-BEG`/`MJD-AVG`/`MJD-END` and `XPOSURE` in the exposure table; verify `TIMESYS` (UTC versus TDB) before any MPC output (inf.: not yet checked) |
| A4 | Image centre from `OBJCTRA`/`OBJCTDEC`, `RA`/`DEC`, `PT-RA`… or from WCS; binning keywords scale the pixel size; flip horizontal/vertical; auto-rotate from `PIERSIDE` [AM-VH 4.1.1, 4.1.2, 4.3.2, 4.8.2] [AM-SET] | No: every exposure has a full WCS | Done: common north-up TAN grid for all frames [OURS-methods] | None |
| A5 | Save as FITS with WCS (auto-save option), "Save All FITS", export JPEG/BMP; stacked images saved with an accurate centre [AM-VH 4.0.9, 4.1.1, 4.4.0] [BAA] | Yes | Done: co-adds as multi-extension FITS with WCS; photometry CSV/VOTable; download script [OURS-api] | S: FITS of a Track & Stack result and of difference images, with `MJD-AVG` of the stack and the motion used in the header (see G1) |

### B. Image display and blink

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| B1 | Blinking with automatic image alignment by star patterns ("Number of Stars", "Alignment Area", maximum shift) [AM] [HELP-PROG] [AM-VH 4.0.7] [AM-SET] | Yes; alignment comes from the WCS instead of star patterns | Done/In progress: one grid for every frame; blink of epochs and of single exposures, difference, stack, RGB [OURS-web] | None for alignment |
| B2 | Blink controls: start, stop, step forward and back, hotkeys; frames sorted by time; each frame shown in full with uncovered areas black [AM-VH 4.0.9, 4.0.10, 4.3.0] | Yes | Done: play, step, scrubber, speed, keyboard, partial visits optional [OURS-web] | None |
| B3 | "Background and Range" dialog with sliders, linear or logarithmic scaling, optionally for all images; "Invert Display"; flips apply to all images [AM-VH 4.0.5, 4.2.0, 4.3.0] [ITEL] | Yes | Done: asinh/linear/log, black and white points over all epochs, colour maps [OURS-web]. Missing: invert | S: invert (negative display helps faint detections [ITEL]); per-frame versus common limits toggle |
| B4 | Zoom 25–400 %, a zoom window following the cursor, a "Magnifying Glass" [AM] [AM-VH 4.0.6, 4.0.8] | Yes | Done: zoom and pan around the cursor [OURS-web]. Missing: loupe | S: loupe showing the native exposure (not the co-add) under the cursor |
| B5 | "Select Markings": colours for good and bad reference stars, stars, moving objects, manual measures and unidentified detections; packed designations on movers during blink [AM-VH 4.0.6, 4.0.7, 4.1.1] [AM-SET] | Yes | Done\*: target, track, Gaia/CatWISE/2MASS at each epoch's date, flags, compass [OURS-web]. Missing: candidate and known-object layers | S: layers for field detections, tracklet candidates and known objects with toggles (part of #2 and #12) |
| B6 | Measuring directly in the blink window [AM-VH 4.0.8] | Yes | Part.: click selects a catalogue source; "Measure spectrum & light curve" [OURS-web] | See H1 |

A self-test from the iTelescope guide translates directly into a citizen-science feature: hide the known-object overlay, mark every mover you see, then switch it on to check what you missed [ITEL].

### C. Star detection and reference catalogues

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| C1 | Object detection: "Aperture Radius" (centroid and flux pixels; 2–3 × FWHM), "Detection Limit" (peak SNR of the central pixel, 4–5 recommended, 3 for faint), "Minimum FWHM" (1.0 rejects hot pixels and most cosmic rays), "PSF-Fit RMS" (≈ 0.25), Gaussian PSF fit of position, height, width and background [HELP-PROG] [AM-PSF] [AM-SET] [AM-VH 4.0.7]. Background from the PSF fit (robust, default) or from an aperture (mean of the central 60 % of pixels) [UAI] [BAA] | Yes, adapted: the PSF is undersampled, so the FWHM cut must be in units of the ePSF and a fit χ² replaces the Gaussian RMS | In code: peaks in S/N maps of co-adds and of exposure-minus-co-add images, real/bogus model with injected sources [OURS-field]. Done: ePSF fits at given positions with χ² [OURS-methods] | S: per-detection ePSF fit quality (χ², PSF weight) as features and as user-visible cuts, as in C1's settings (see #5, #10) |
| C2 | "Image Catalog": every detection with type (S star, R reference, M moving, H manual, ? unidentified), RA/Dec, magnitude, x/y, flux (ADU), FWHM, peak SNR, PSF-fit RMS, residuals; save as text [AM-VH 4.3.2] | Yes | In code: field sources with per-detector fluxes and catalogue matches, JSON only [OURS-field] | S: table and CSV (#10) |
| C3 | Reference catalogues: Gaia DR3, ATLAS REFCAT 2, PPMXL, UCAC 4, CMC-15 via VizieR or local copies; upper and lower magnitude limits (avoid saturated and faint stars); obsolete catalogues removed in 4.16.0 [AM-CAT] [AM-VH 4.16.0] [HELP-PROG] | Yes for overlays and QA; Gaia DR3 is the right reference, as for the SPHEREx pipeline [SPX-PIPE] [MPC-GS] | Done\*: Gaia DR3, CatWISE2020, 2MASS cones (≤ 30′, ≤ 3,000 sources) with positions moved to each epoch [OURS-api] | S: magnitude limits on overlays; flag catalogue stars predicted to saturate (about 0.1–0.2 Jy in D1–D3 [OURS-val]) |
| C4 | Reference-star matching: automatic pattern match ("Number of Stars", quadratic cost; "Search Radius" a few arcseconds), focal length, position angle and pointing tolerances; manual match by dragging a red overlay [HELP-PROG] [AM-SET] [DUR] [EURO] | No: the WCS is given | n/a | None. Keep a "WCS looks wrong" warning instead (see D2) |

### D. Astrometric plate solution

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| D1 | "Data Reduction": least-squares plate constants, linear, quadratic or cubic; falls back to a lower order with few stars and reports the order used [HELP-PROG] [AM-VH 4.3.2]. The MPC says to distrust software without a proper LSPC solution [MPC-GS] | Partly: the WCS is solved, but a local refit of a few constants against Gaia stars in our cutout is a cheap check and can improve positions locally | Done: positions via the exposure WCS with SIP; alignment between epochs ≤ 0.27″ on field stars [OURS-val] | M: plate-solution QA and optional local linear refit (#7) |
| D2 | Residual rejection ("Astrometric Limit" in ″); good (green) and rejected (yellow) reference stars; residual vectors on the image; results window with stars, reference stars, mean dRA/dDec, Ref/Phot count and dmag [HELP-PROG] [AM-VH 4.0.8, 4.3.2] [DUR] [EURO] [BAA] | Yes, as QA of the given WCS | Missing; `FINAST` is not read [OURS-code] | M (#7); S for reading `FINAST` and flagging exposures |
| D3 | Log file: per reference star residuals and FWHM; per object residuals O–C against MPCOrb; estimated uncertainty = centroid error + catalogue error from the reference residuals; time stamps and stack mid-times [AM-VH 4.0.6, 4.0.7, 4.0.8, 4.3.1] | Yes | Part.: motion fits report per-point residuals and σ [OURS-api] | S: a per-measurement uncertainty that adds the WCS residual RMS from #7 |
| D4 | Error model: σ_pos = σ_PSF / SNR (peak SNR), σ_mag from the total SNR [AM-PSF]; ADES uncertainties improved in 4.15 [AM-VH 4.15.0] | Yes | Part.: centroid σ with a 0.02 px floor [OURS-code] | S: use the ePSF fit covariance for x, y; report rmsRA/rmsDec in exports |

### E. Photometric reduction

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| E1 | Zero point fitted to reference stars in a chosen band (B, V, R, I, Gaia G/BP/RP, Sloan from REFCAT, APASS in UCAC 4), colour corrections for UCAC 4/URAT, "Photometric Limit" rejects outliers; Ref/Phot and dmag in the results [AM-CAT] [AM-VH 4.6.5, 4.10.5] [BAA] [UAI] | Partly: images are flux-calibrated, so the zero point becomes a **check** and a per-exposure correction for systematics | Done: ePSF, aperture and ring photometry in MJy/sr → mJy; one field star agrees with 2MASS and WISE to 8 %; synthetic 2MASS/WISE photometry module [OURS-methods] [OURS-code] | M: per-exposure ensemble zero-point QA and differential light curves (#9) |
| E2 | Aperture radius doubles as photometric aperture (BAA standardises 5.65″ for comet 29P); background from PSF fit or aperture [BAA] [UAI] | Yes | Done: aperture r = max(1.5 px, FWHM) with annulus; ring photometry for saturated stars [OURS-methods] | None |
| E3 | Magnitudes in the MPC report ("Include Magnitude", 0.01 mag); `PhotReport.txt` with JD, mag, filter, SNR, zero point, catalogue, designation [AM-VH 4.0.7, 4.6.5] [BAA] | Yes | Done: photometry CSV/VOTable with provenance [OURS-api] | S: add per-exposure zero point and SNR columns once #9 exists |
| E4 | Colour band and filter (`fltr`) in ADES 2022 [AM-VH 4.16.0] | Partly: SPHEREx bands are not MPC bands [MPC-80] [MPC-ADESV] | n/a | S: omit magnitudes in MPC exports, or give synthetic J/H/K from the spectrum (`spectral/synphot.py`), marked as such (inf.: needs MPC agreement) |
| E5 | Warning: photometry on a fresh stack uses only the first image's reference stars [BAA] | Yes, as a lesson: a stack's flux must come from all frames or from per-frame measurements | Done: photometry is per exposure [OURS-methods] | None |

### F. Moving objects: detection, identification, known objects, ephemerides

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| F1 | "Moving Object Detection" (Ctrl+M) on 3–6 images: detections found at the same place (within "Search Radius") on at least two images are stars; the rest that move consistently are candidates; works up to the speed at which objects trail; candidates labelled and shown in a verification dialog [AM] [AM-VH 4.0.5, 4.0.8, 4.4.0] [HELP-PROG]. Users find it reliable for bright objects, not faint ones, and use it next to visual blinking [ITEL] | **Yes, within a visit.** Exposures minutes to days apart; 113.6 s exposures trail only above ~190″/h (inf.) | In code: exposure-minus-epoch-co-add S/N images, real/bogus scores, tracklets of ≥ 3 detections on a straight line with constant rate (tolerance 1.5 px, rms ≤ 1 px, 15 min–3 d), strong pairs [OURS-field] | M: API, job, candidate list and markings (#2); S: simultaneous-pair and spectral checks (#5); M: observer-aware linking for near-Earth objects (part of #2) |
| F2 | Identification against MPCOrb: an identity is suggested when the position agrees within 15′, the motion PA within ±15° and the speed within ±30 % (4.0.6); the designation browser lists objects within 1° with offsets, magnitude, speed and direction (4.1.1); O–C in the log [AM-VH 4.0.6, 4.0.7, 4.1.1] [EURO] | Yes | In code: nearest `sb_ident` object within 40″ after propagating with its rates [OURS-field] | S: Astrometrica's motion criteria plus the SPHEREx-centred positions from #1 (#6) |
| F3 | "Known Object Overlay": nominal positions, packed designations and predicted magnitudes of all MPCOrb minor planets and comets on the loaded images; two-body positions without perturbations when introduced (4.2.0); limiting magnitude option (4.16.1); toolbar button [AM-VH 4.2.0, 4.3.1, 4.16.1] [ITEL] [EURO] [UAI]. The UAI guide warns not to use it to judge a measurement [UAI] | **Yes**, the single most useful identification aid; must be computed for SPHEREx's position (tested offsets) | In code: geocentric `sb_ident` first pass at V ≤ 21 [OURS-field]. Done: MOST ephemeris of one named object [OURS-api] | M (#1) |
| F4 | MPCOrb database: "Download MPCOrb", "Update MPCOrb" (daily update, NEAs, comets, distant and unusual objects), "Reload", extra elements in `User.dat`, `Comet.dat` [AM-VH 4.2.0, 4.4.0, 4.9.10] [EURO] | Partly: we should not mirror MPCOrb; JPL and the MPC run current, perturbed services | n/a | S: allow user elements or an MPC one-line orbit for MOST's `mpc_input`/`manual_input` [MOST] (an "own orbit" option like `User.dat`) |
| F5 | Ephemerides: topocentric with parallax (fixed in 4.0.6), mid-exposure; hyperbolic orbits for interstellar objects (4.16.1) [AM-VH 4.0.6, 4.16.1] | Yes, observer = SPHEREx | Done: MOST positions are SPHEREx-centred (tested); co-moving builds for named objects, 3I/ATLAS and Eros [OURS-methods] [MOST-test] | S: per-object Horizons ephemeris with `CENTER='500@-163182'` as a fallback when MOST is slow or down (tested working) |

### G. Track and Stack

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| G1 | "Track & Stack" (Astrometry menu, Ctrl+S): add images (with thumbnail preview, 4.16.2), enter speed (″/min) and PA by hand or from the MPCOrb browser (a separate browser sets only the motion, 4.3.3); combine by Add (after subtracting a pedestal), Average or Median (median removes stars); the measured position refers to the mean time of the frames (4.0.11) [AM] [AM-VH 4.0.11, 4.3.1, 4.3.3, 4.16.2] [EURO] [UAI] [DUR] | **Yes**: within a visit for asteroids and for slow distant objects | Done: co-moving sequence for a named object from MOST (stars trail, object stacks) [OURS-api]. In code: trial-motion shift-and-stack across epochs for proper motions of 1–15″/yr [OURS-field]. Missing: an arbitrary motion vector inside a visit | M (#3) |
| G2 | "Re-Stack Images" with a new motion vector (4.3.3) and "Re-Stack all" (4.5.0); useful when several objects move differently or the first vector trails the object [AM-VH 4.3.3, 4.5.0] [ITEL] | Yes | Missing | Part of #3: re-stack from cached aligned exposures in seconds |
| G3 | Observer techniques with Track & Stack: three sub-stacks plus one stack of all frames, blinked together; interlaced stacking in rich fields; NEOCP variant orbits [BIRT]; stack at half the object's rate so stars and object trail equally for photometry [BAA]; mark candidates in the stacked images, then verify in the sub-stacks [EURO] [ITEL] | Yes | Missing | S once #3 exists: "split visit into thirds + all" preset; half-rate option |

### H. Measurement tools

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| H1 | Click to measure: PSF fit, then the "Object Verification" window with coordinates, magnitude, date and time, SNR, FWHM, fit RMS and graphs of the profile and fit; zoom 1×–8×, "stare" on one frame or "track" the object; Ctrl-click = simple centroid at the clicked pixel; Shift+Ctrl = raw pixel coordinate; arrow keys and Enter [AM] [AM-VH 4.0.6, 4.0.10, 4.2.0, 4.3.1] [DUR] [EURO] | Yes | Part.: per-epoch centroids and a proper-motion fit (`/motion`), photometry of any position (`/photometry`), click-to-select catalogue sources [OURS-api] [OURS-web] | S–M (#4) |
| H2 | Centroid window to recentre a faint or blended object by hand, with adjustable radius [EURO] | Yes, for blends like WISE 0855 next to a G = 16.3 star [OURS-val] | Missing | Part of #4: drag the fit position; fit two sources at once (ePSF, linear least squares) |
| H3 | Shift-drag shows Δx, Δy, ΔRA, ΔDec, distance and PA (tail length, offsets from a nucleus) [AM-VH 4.3.2] | Yes | Missing | S (#11) |
| H4 | Accepting a measure writes it to the report and log; manual measures are marked in magenta with their designation [AM-VH 4.0.7, 4.3.2] [EURO] | Yes, as "add to my measurements" | Missing | S: a client-side measurement list feeding the exports (#8, #10) |
| H5 | "Display Header" of the FITS or SBIG file [AM-VH 4.0.7] | Yes | Part.: header keys shown in the Data tab (inf.) | S: link the IRSA cutout of the exposure (already in `download.sh?kind=cutout`) |

### I. Reports

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| I1 | `MPCReport.txt` in the 80-column MPC1992 format: header with `COD`, `CON`, `OBS`, `MEA`, `TEL`, `NET` per reference catalogue, `AC2`, `ACK`; contact details optional; roving-observer second line for code 247; magnitudes optional; extra digit in RA/Dec; time to 5 or 6 decimals; packed designations; nuclear `N` flag for comets [AM-VH 4.0.7, 4.2.0, 4.3.1, 4.3.2, 4.10.5] [EURO] | Partly: SPHEREx needs the satellite form (Note 2 `S`/`s` with a second line giving the spacecraft position) [MPC-80] | Missing | Part of #8 |
| I2 | ADES output since 4.13 (2023), "replacing" the 80-column file; `ADESReport.psv` read by the ADES Astrometry Parser (which dates ADES output to 4.12, 2018); position uncertainties (4.15); ADES 2022 with `fltr` (4.16) [AM-VH 4.13.0, 4.15.0, 4.16.0] [BIRT-ADES] | Yes | In code: PSV draft with `stn 500`, `mode CCD`, fixed rms 3″, `band` = `D1`… (not valid ADES values) [OURS-field] | M (#8) |
| I3 | Send the report to the MPC by e-mail from the program (SMTP settings) [UAI] [AM-VH 4.3.2] | No: ADES cannot be sent by e-mail; web form or cURL only [MPC-REP] | n/a | None. Link the MPC's PSV validator instead [MPC-ADES] |
| I4 | Log file, `PhotReport.txt`, "Reset Files", view files with auto-reload [AM-VH 4.1.0, 4.1.1, 4.6.5] [BAA] | Yes, as provenance | Done: `meta.json`, exports, download script [OURS-api] | None |

### J. Settings and environment

| ID | Astrometrica feature | Applies to SPHEREx L2? | Ours | Build |
|---|---|---|---|---|
| J1 | Program settings in tabs (Observing Site, CCD, Program, Environment, Internet, Catalogs); save and load `.cfg` files per telescope/camera; explicit Save; "Check Output Files at Startup" [AM-VH 4.0.7, 4.1.0, 4.4.0] [EURO] [UAI] [BAA] | Partly: no instrument settings are needed; detection and linking thresholds are worth exposing | Done: processing parameters in the request and the URL [OURS-methods] [OURS-web] | S: "detection profile" (detection S/N, real/bogus cut, link tolerance, rate range) as `FieldOptions` in the URL, with presets "bright", "faint", "slow movers" |
| J2 | Internet tab: VizieR server choice, MPCOrb server, SMTP [AM-VH 4.4.2] [UAI] [EURO] | No | Done: catalogue errors mapped to readable `502`s, retries [OURS-api] | None |
| J3 | Marker colours [AM-VH 4.1.1] | Partly | Done: fixed dataviz palette [OURS-web] | None |
| J4 | Built-in tutorials with sample images (2000 AC6) [AM-DL] [DUR] | Yes | Done: five challenge presets [OURS-api] | S: an asteroid preset (Eros, 3I/ATLAS) with a guided "find the mover" walkthrough |

**Not applicable:** darks and flats, star-pattern and manual reference matching, focal length, pixel size, position angle and flips, saturation level in ADU, binning and `PIERSIDE`, SBIG format, VizieR server choice, SMTP mail, shareware licence.

## 3. Ranked build list

### 1. SPHEREx-centred known-object overlay (M)

- **Mirrors:** Known Object Overlay with limiting magnitude, labels and predicted magnitudes [AM-VH 4.2.0, 4.16.1].
- **Why first:** it answers "is this mover known?" for any field, shows users where asteroids are before they search, and is the input to identification (#6) and to the real/bogus training set.
- **How:**
  - For each epoch (and, in exposure mode, each exposure time) get SPHEREx's geocentric state from Horizons: `EPHEM_TYPE='VECTORS'`, `COMMAND='-163182'`, `CENTER='500@399'`, km and km/s (tested) [HZ-test]. Cache by time; one request can return a table.
  - Call `https://ssd-api.jpl.nasa.gov/sb_ident.api` with `xobs=<x,y,z,vx,vy,vz>`, `obs-time`, the field box, `two-pass=true&suppress-first-pass=true` (numerical propagation), `vmag-lim`, `sb-kind=a` and `c` [JPL-SBI]. Rates come back when the velocity is given.
  - Draw names, V and a short motion arrow on the blink; in exposure mode move each label to the exposure time. Add a V-limit slider like Astrometrica 4.16.1.
  - Replace the geocentric query in `field/transients.py` (`mpc-code=500`, first pass only) with this one.
  - Cross-check: for a named object, MOST's `ra_obj`/`dec_obj` are already SPHEREx-centred (tested) and Horizons with `CENTER='500@-163182'` works for single objects (tested). SkyBoT with `-loc=288` returned the geocentric position in our test, so do not use it for SPHEREx without its own observer state [SKYBOT-test].
  - Later (L): run all of MPCORB with `kete` (the propagator the SPHEREx pipeline uses for its own solar-system list [SPX-PIPE] [KETE]) to avoid per-field API calls. The pipeline's SSO list is an "ancillary parquet file" [SPX-PIPE] but we found no such product in the public bucket (tested) [S3-test].

### 2. Moving Object Detection in the product (M)

- **Mirrors:** Moving Object Detection (Ctrl+M), markings and the verification dialog [AM-VH 4.0.5–4.0.8] [HELP-PROG].
- **What exists:** `field/transients.py` already detects in exposure-minus-co-add S/N images, scores with the boosted-stump real/bogus model and links ≥ 3 detections per epoch [OURS-field].
- **How:**
  - Route and job: `POST /api/sequences/{id}/field` with the existing `FieldJobs` runner (dedup, heartbeat, cancel) and `GET` for results; an ADR.
  - UI: a candidate list (tracklet id, n, rate ″/h, PA, mean SNR, real/bogus, known match) and a "movers" marking layer in exposure mode; clicking opens #4.
  - Advise wide fields: an asteroid at 30″/h crosses a 6′ field in 12 h (inf.), so most tracklets need 15–30′.
  - Observer-aware linking (part of this item, can come second): fit tracklets as topocentric = geocentric linear motion + SPHEREx parallax(t; Δ) over a few trial distances Δ (0.05–5 AU), or subtract the parallax at each trial Δ before the straight-line fit (inf.: a simplified form of distance-hypothesis linking used by heliocentric linkers). This matters below about 1.5 AU, where the orbital wobble reaches ≥ 1 px.

### 3. Track & Stack inside a visit (M)

- **Mirrors:** Track & Stack with Add/Average/Median, Re-Stack, sub-stacks [AM-VH 4.0.11, 4.3.3, 4.5.0] [BIRT] [BAA] [EURO].
- **Why:** faint asteroids and comets that are invisible in single exposures; slow distant objects (0.5–1″/h at quadrature, inf.) need several days of a visit, which is the "Planet X" use case of the challenge.
- **How:**
  - Input: speed (″/h) and PA, or the motion of a known object from #1 (Astrometrica's "motion browser"), epoch, detectors, combine mode, reference time.
  - Server: shift every aligned exposure of the visit by v × (t − t_ref) with the same NaN-aware bilinear resampling as the build; combine by weighted mean (1/`rms`²), median, or sum. Reuse the cached cutouts (rebuilds take seconds [OURS-methods]). Record `t_ref` as the weight-mean mid-time, as Astrometrica 4.0.11 does.
  - Client quick look for small fields: shift `exposures.f16` planes in the browser for interactive re-stacking (S–M).
  - Search mode: a grid of trial motions within a visit (the in-code epoch version uses 1–15″/yr in 15° steps [OURS-field]); report the S/N peak per trial vector. Follow the MPC warning on stacked noise from synthetic tracking: require detection in at least two independent sub-stacks [MPC-GS].
  - Presets: thirds plus all-frames stack for blinking [BIRT]; half-rate stack for photometry [BAA]; FITS export with the motion in the header.

### 4. Verification panel (S–M)

- **Mirrors:** Object Verification window, Ctrl-click centroid, centroid window [AM-VH 4.0.6, 4.0.10, 4.2.0] [EURO].
- **How:**
  - On click (in any mode), fit the detector's ePSF with free x, y, flux and background on each native exposure near that time (the build fits flux only at a fixed position [OURS-methods]); weights 1/`VARIANCE`; report SNR, χ², PSF weight, FWHM of the co-add, the position and σ from the fit covariance; RA/Dec through the exposure WCS.
  - Show a strip of cutouts in "stare" (fixed sky position) and "track" (following the candidate's motion) modes, 1×–8×, as Astrometrica does.
  - Modes: fit (default), centroid (Ctrl-click), raw pixel (Shift+Ctrl-click).
  - Option to fit a neighbour simultaneously (Gaia position at the exposure date) for blends.
  - "Accept" adds the measurement to a session list used by #8 and #10.

### 5. Coincidence and spectral checks for detections (S)

- **Mirrors:** "found at the same place on at least two images is a star" and the FWHM/PSF-RMS cuts [AM-VH 4.0.5, 4.0.7] [HELP-PROG].
- **How:**
  - For each exposure-difference detection, look for a counterpart in the simultaneous partner detector (D1↔D4, D2↔D5, D3↔D6) at the same time and position (± 1 px); a real moving source appears in both (if bright enough at both wavelengths), a cosmic ray or detector artefact in one (inf.). Feed it to the real/bogus model as a feature and as a hard filter option.
  - Brightness consistency per wavelength instead of "similar brightness": compare each tracklet point with a smooth reflected-plus-thermal SED (asteroids brighten at 4–5 µm, like NEOWISE W2; inf.) rather than requiring equal flux across frames.
  - Expose detection S/N, real/bogus and χ² cuts as the "detection profile" (J1).

### 6. Identification with motion criteria (S)

- **Mirrors:** identification by position, PA ±15° and speed ±30 % [AM-VH 4.0.6]; designation browser within 1° [AM-VH 4.1.1]; O–C in the log [AM-VH 4.0.7].
- **How:** for each tracklet, rank known objects from #1 by a score from position offset (in units of its 3σ uncertainty when SBDB gives one; `https://ssd-api.jpl.nasa.gov/sbdb.api` returns orbit uncertainty data [JPL-SBDB]) and Astrometrica's motion tolerances; show O–C per detection; "unknown" when nothing passes. Offer "check with MPChecker" and "MPC NEO rating" links as the EURONEAR guide recommends [EURO].

### 7. Plate-solution QA against Gaia (M)

- **Mirrors:** Data Reduction results (reference stars, mean residuals, rejected stars), residual display, plate-constant order [HELP-PROG] [AM-VH 4.0.8, 4.3.2] [DUR].
- **How:**
  - Read `FINAST` (and any other astrometric keywords) per exposure into `meta.json`; flag exposures with a non-zero value, which IRSA should not serve but QR data may differ (inf.) [SPX-PIPE].
  - Per exposure, select Gaia DR3 stars like the pipeline does (good astrometry, SNR > 10, not saturated, no neighbour within 2 px brighter than 10 %) [SPX-PIPE]; propagate to `MJD-AVG` with proper motion (and parallax, which we ignore today [OURS-methods]); fit their positions with the ePSF on the native cutout; residuals in RA/Dec through the exposure WCS.
  - Report N, mean offset, RMS, residual vectors (an overlay like Astrometrica's residual display), and reject 3σ outliers ("Astrometric Limit").
  - Optional local refit: a 6-constant linear correction on top of the SIP WCS within the cutout (`astropy.wcs.utils.fit_wcs_from_points` for a fresh TAN/SIP fit [ASTROPY-FIT], or a direct least-squares affine on residuals). Use it for moving-object positions; lower the order with few stars, as Astrometrica does.
  - Use the RMS as the systematic term in measurement uncertainties (D3, D4).

### 8. ADES and 80-column export for space-based tracklets (M)

- **Mirrors:** MPCReport and ADES output, NET per catalogue, error estimates [AM-VH 4.3.1, 4.13.0, 4.15.0, 4.16.0].
- **How:**
  - ADES PSV and XML from tracklets and accepted measures (#4): `stn` 288 [MPC-CODES]; observatory position per observation with `sys` `ICRF_KM`, `ctr` 399 and `pos1`–`pos3` from the Horizons state at `obsTime` (field names from the ADES description [ADES-GH]; `sys` values from [MPC-ADESV]); `obsTime` = `MJD-AVG` in UTC ISO (after checking `TIMESYS`); `astCat` `Gaia3`; `rmsRA`/`rmsDec` from #4 and #7; `trkSub` per tracklet; notes `Z` (survey data measured by a non-survey pipeline) and `K` for stacked measures [MPC-ADESV].
  - `mode`: the SPHEREx detectors are HgCdTe arrays, which match none of `CCD`, `CMO`, `VID`… exactly; ask the MPC before choosing (inf.) [MPC-ADESV].
  - `band`/`mag`: leave out, or give a synthetic J/H/K value marked as such; `D1`…`D6` are invalid (E4).
  - 80-column variant: Note 2 `S` on the first line and the spacecraft position on a second line [MPC-80].
  - Validate with the MPC PSV checker [MPC-ADES]; follow the MPC rules on ≥ 3 observations per object per night and one position per time [MPC-REP]. Label the files "for follow-up" unless the team agrees a submission route with the MPC.

### 9. Photometric zero-point check and ensemble light curves (M)

- **Mirrors:** photometric reduction with Ref/Phot, dmag and the photometric limit; PhotReport [AM-CAT] [BAA].
- **How:**
  - For each exposure, forced ePSF photometry (existing code [OURS-methods]) of 20–200 unsaturated, isolated Gaia or 2MASS stars in the field.
  - Predicted flux at each star's pixel wavelength: interpolate the 2MASS J/H/Ks and CatWISE W1/W2 fluxes in log F against log λ (or the star's own all-visit SPHEREx median spectrum for a purely relative check).
  - Per exposure: median log ratio = zero-point offset, robust scatter, N used after a "photometric limit" clip; store as exposure columns; flag exposures whose offset is outliers.
  - Ensemble light curves: divide the target's flux by its exposure's offset; expect a lower scatter for constant stars than the ±5 % of ring photometry today (inf.) [OURS-methods]. Validate on the G = 13.9 comparison star and on R Dra's period [OURS-val].

### 10. Image Catalog (S)

- **Mirrors:** "Image Catalog" with S/R/M/H/? types, flux, FWHM, SNR, fit RMS and residuals, saved as text [AM-VH 4.3.2].
- **How:** a Data-tab table from the field-analysis JSON: source id, RA/Dec at the mean epoch, type (catalogue star / reference star used in #7 / mover / tracklet / manual / unidentified), per-detector flux and S/N, χ², catalogue match and motion; CSV and VOTable through `astropy.table` like the existing photometry exports [OURS-api].

### 11. Measuring tools (S)

- **Mirrors:** Shift-drag distance and PA, Ctrl and Shift+Ctrl clicks [AM-VH 4.2.0, 4.3.2].
- **How:** on the SVG overlay, drag to show Δα cos δ, Δδ, separation and PA east of north (`astropy.coordinates` conventions mirrored in `astro.ts`); click-lock copies `RA Dec` in decimal, sexagesimal and `SkyCoord(...)` form (analogs gap 6 [ANALOGS]).

### 12. Display and markings (S)

- **Mirrors:** Background and Range (log, apply to all), Invert, Magnifier, Select Markings [AM-VH 4.0.5, 4.2.0, 4.3.0, 4.0.6].
- **How:** invert; a loupe showing the native exposure; marking toggles and colours for stars, references, movers, known objects and manual measures; a "find them yourself" mode that hides the known-object and candidate layers until the user has marked movers, then scores their marks (the iTelescope self-test [ITEL]), which also yields labelled data for the real/bogus model.

### Lower-ranked

| Item | Astrometrica feature | Effort | Note |
|---|---|---|---|
| Own orbit input for MOST (`mpc_input`, `manual_input`) | `User.dat` elements [AM-VH 4.4.0] | S | MOST already accepts one-line elements and manual elements [MOST] |
| Horizons fallback for named objects | ephemerides [AM-VH 4.0.6] | S | `CENTER='500@-163182'`, `QUANTITIES='1,3'` (tested) |
| Exposure timing columns and `TIMESYS` check | time settings [AM-VH 4.1.1, 4.16.1] | S | Needed before any MPC export |
| Asteroid presets and a guided tutorial | built-in tutorial images [AM-DL] | S | Eros (954 files, 6 epochs) and 3I/ATLAS builds exist [OURS-methods] |
| FITS of stacks and differences | Save as FITS with WCS [AM-VH 4.1.1] | S | Stack mid-time and motion in the header |
| Detection profile in the URL | `.cfg` settings files [AM-VH 4.0.7] | S | Presets for bright, faint, slow movers |
| Comet aperture photometry at a fixed physical radius | BAA's 5.65″ standard aperture for 29P [BAA] | S | Aperture in km at the comet's distance from MOST's `geo_dist` |

## Checks we ran

All read-only, on 2026-11-14.

- **Astrometrica site** [AM-SITE-test]: `HEAD https://www.astrometrica.at/` → `200`, `GET` → `404` (with and without a browser user agent). Pages read from Wayback (`/web/2026id_/http://www.astrometrica.at/…`); the frame page loads `content.html` and `astrometrica.html`.
- **Horizons, SPHEREx as observer** [HZ-test]: the lookup API returns `SPHEREx (spacecraft)`, SPK-ID `-163182`. 433 Eros at 2025-08-13 00:00 UT: from `500@-163182` RA 01 39 42.82, Dec +25 14 54.3; from `500@399` RA 01 39 42.70, Dec +25 15 03.2 (≈ 9.0″ apart, Δ = 0.979 AU). SPHEREx's geocentric distance over 80 minutes on that date: 7,014–7,043 km.
- **MOST positions** [MOST-test]: `nph-most?catalog=spherex&input_type=naifid_input&obj_naifid=20000433&obs_begin=2025-06-01&obs_end=2025-09-30&output_mode=Brief` answered in 4 s, `exposure_time` 113.58 s. For three rows (MJD 60851.008, 60865.953, 60880.427) `ra_obj`/`dec_obj` equal Horizons from `500@-163182` at `mjd_start` to < 0.01″; the geocentric positions differ by 1.3″, 0.7″ and 3.7″ (Δ = 1.51, 1.34, 1.18 AU).
- **SkyBoT with code 288** [SKYBOT-test]: `conesearch.php?-ep=2460900.5&-ra=24.928&-dec=25.249&-rd=0.2&-loc=288` lists Eros at 01 39 42.70 +25 15 03.1, the geocentric position, plus four fainter asteroids in the 0.2° cone (2.7 s).
- **MPC observatory codes** [MPC-CODES]: `288 SPHEREx` in ObsCodesF.html, without site coordinates (space-based); also `C51 WISE`, `245 Spitzer`, `258 Gaia`, `274 JWST`.
- **Public bucket** [S3-test]: `s3://nasa-irsa-spherex/dr1/` and `qr3/` contain `level2/`, calibration products (`epsf/`, `sip/`, `solid_angle_pixel_map/`, `spectral_wcs/`, …) and, for DR1, `hips5_cube/`; no solar-system-object product.

## Sources

Astrometrica (Herbert Raab), read from the Wayback Machine snapshots of 2026 unless noted:

- [AM] Home page with the feature list: [astrometrica.html](https://web.archive.org/web/2026/http://www.astrometrica.at/astrometrica.html)
- [AM-DL] [download.html](https://web.archive.org/web/2026/http://www.astrometrica.at/download.html): current version 4.16.4.468, Windows 7–11, tutorials included, 100-day shareware
- [AM-VH] [versions.html](https://web.archive.org/web/2026/http://www.astrometrica.at/versions.html): version history 4.0.5 (2001) to 4.16.3 (2025-08-07)
- [AM-CAT] [catalogs.html](https://web.archive.org/web/2026/http://www.astrometrica.at/catalogs.html)
- [AM-SET] H. Raab, "Finding the right settings for your Images", Workshop on Photometry and Astrometry, La Palma 2008: [Astrometrica-Settings.pdf](https://web.archive.org/web/2020/http://www.astrometrica.at/Papers/Astrometrica-Settings.pdf)
- [AM-PSF] H. Raab, "Detecting and measuring faint point sources with a CCD", MACE 2002: [PointSources.pdf](https://web.archive.org/web/2020/http://www.astrometrica.at/Papers/PointSources.pdf)
- [HELP-PROG] Durham AstroLab, "Settings – Program" (the Program-tab text of Astrometrica's online help): [programmesetting.html](https://astrolab.awh.durham.ac.uk/programmesetting.html)

Tutorials and observer guides:

- [DUR] Durham AstroLab, "Astrometrica: A Brief Tutorial": [astrom_notes_updated.html](https://astrolab.awh.durham.ac.uk/astrom_notes_updated.html)
- [EURO] R. Toma, O. Vaduvescu, "How To Measure Asteroids using Astrometrica", EURONEAR, 2019: [PDF](http://www.euronear.org/manuals/Astrometrica-UsersGuide-EURONEAR.pdf)
- [UAI] G. Baj, "Astrometrica software, guida pratica di base", UAI: [PDF](http://asteroidi.uai.it/PDF/Tutorial_Setting_Astrometrica.pdf)
- [BAA] R. Miles, "Observing outbursting comet 29P", BAA tutorial, 2019: [PDF](https://britastro.org/wp-content/uploads/sites/29p_tutorial_Miles.pdf)
- [BIRT] P. Birtwhistle, "Stacking Techniques": [StackingTechniques.htm](https://birtwhistle.org.uk/StackingTechniques.htm)
- [BIRT-ADES] P. Birtwhistle, ADES Astrometry Parser overview: [Overview.html](https://birtwhistle.org.uk/Docs/ADESAstrometryParser/Overview.html)
- [ITEL] iTelescope, "Discovery astrometry using stacked images (part 7)": [article](https://support.itelescope.net/support/solutions/articles/233094-discovery-astrometry-using-stacked-images-part-7-of-discovering-asteroids-at-itelescope-net)

Minor Planet Center:

- [MPC-GS] [Getting Started with Astrometry](https://docs.minorplanetcenter.net/mpc-ops-docs/astrometry/getting-started/)
- [MPC-REP] [Reporting Observations](https://docs.minorplanetcenter.net/mpc-ops-docs/astrometry/reporting-observations/)
- [MPC-ADES] [ADES Data Submission](https://docs.minorplanetcenter.net/mpc-ops-docs/observations/ades-format/)
- [MPC-ADESV] [Valid ADES Field Values](https://docs.minorplanetcenter.net/mpc-ops-docs/observations/valid-ades-values/)
- [MPC-80] [MPC1992 80-column format](https://docs.minorplanetcenter.net/mpc-ops-docs/observations/mpc1992-format/)
- [MPC-CODES] [Observatory codes list](https://minorplanetcenter.net/iau/lists/ObsCodesF.html)
- [ADES-GH] [IAU-ADES/ADES-Master](https://github.com/IAU-ADES/ADES-Master) (ADES_Description.pdf)

SPHEREx, ephemerides and libraries:

- [SPX-PIPE] R. Akeson et al., "The SPHEREx Image and Spectrophotometry Processing Pipeline", [arXiv:2511.15823](https://arxiv.org/abs/2511.15823), section 4.2
- [MOST] [IRSA MOST](https://irsa.ipac.caltech.edu/applications/MOST/) and its `nph-most` API (see analogs.md)
- [JPL-SBI] [JPL Small-Body Identification API](https://ssd-api.jpl.nasa.gov/doc/sb_ident.html) (observer by MPC code, geodetic position, or geocentric/heliocentric state `xobs`/`xobs-hel`)
- [JPL-HZ] [JPL Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html); Python: [astroquery.jplhorizons](https://astroquery.readthedocs.io/en/latest/jplhorizons/jplhorizons.html)
- [JPL-SBDB] [JPL SBDB API](https://ssd-api.jpl.nasa.gov/doc/sbdb.html)
- [SKYBOT] [IMCCE SkyBoT](https://ssp.imcce.fr/webservices/skybot/); Python: [astroquery.imcce](https://astroquery.readthedocs.io/en/latest/imcce/imcce.html)
- [KETE] [Caltech-IPAC/kete](https://github.com/Caltech-IPAC/kete): MPC-catalogue propagation and field-of-view checks
- [ASTROPY-FIT] [`astropy.wcs.utils.fit_wcs_from_points`](https://docs.astropy.org/en/stable/api/astropy.wcs.utils.fit_wcs_from_points.html)
- [PHOTUTILS] [photutils PSF photometry](https://photutils.readthedocs.io/en/stable/user_guide/psf.html) and [detection](https://photutils.readthedocs.io/en/stable/user_guide/detection.html) (alternatives to our own ePSF and peak code)

Our project:

- [OURS] [README](../../README.md), [api.md](../api.md), [methods.md](../methods.md), [web/README.md](../../web/README.md)
- [OURS-data] [data.md](../data.md); [OURS-methods] [methods.md](../methods.md); [OURS-api] [api.md](../api.md); [OURS-web] [web/README.md](../../web/README.md); [OURS-val] [analysis/validation/REPORT.md](../../analysis/validation/REPORT.md)
- [OURS-code] `backend/spherex_core/` (e.g. `pipeline/frame.py`, `moving/ephemeris.py`, `analysis/motion.py`, `spectral/synphot.py`)
- [OURS-field] `backend/spherex_core/field/` (`sources.py`, `crossmatch.py`, `movers.py`, `transients.py`, `realbogus.py`, `variability.py`, `colours.py`), working tree on 2026-11-14
- [ANALOGS] [analogs.md](analogs.md)
