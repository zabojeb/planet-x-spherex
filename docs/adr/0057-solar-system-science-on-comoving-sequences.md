# ADR-0057: Subtract the static sky from co-moving sequences with SPHEREx's own other visits, and analyse comets, asteroids and unknown movers as jobs

- Status: Accepted (extends 0045, 0048, 0049, 0052)
- Date: 2026-11-15

## Context

A co-moving sequence (`object_name` / `naif_id`) puts every exposure on a grid centred on the solar-system object,
so the object stays still while the stars stream through. Measured on the 3I/ATLAS August 2025 visit (a Milky Way
field, 6′ and 16′ grids): the 99th percentile of |pixel| / noise per exposure is 31–43σ, a 1′ aperture on the
nucleus contains stars in about a third of the exposures, and in the co-add the streaks raise the outer noise 2–3×.
A comet's coma, its CO2 4.26 µm band image and an asteroid's light curve all need the object without that sky.

SPHEREx observes every patch of sky again: in other passes of the same visit (hours to days later) and in the
visits six months apart. Those images were taken when the object was elsewhere, through the same detector and,
thanks to the LVF, at the same wavelength if the patch fell on the same detector row. They are the natural
template: same PSF, same calibration, same wavelength.

The brief: "different background, same comet", plus comet activity measures (km apertures, Afρ, CO2/CO/H2O,
water ice, activity vs r_h), asteroid light curves (period, colours, taxonomy) and unknown movers (unmatched
tracklets with their evidence, ADES export), all through `services.py` for HTTP and MCP, process-heavy work on the
existing queues.

## Decision

1. **Static sky on the build queue.** `SequenceParams.static_sky` (only with an object) makes the build call
   `solar.static_sky.add_static_sky` after `patch_comoving_meta`; the sequence key includes the static-sky version,
   so the clean build is its own cached, deduplicated, cancellable job on `spherex_core.jobs`, and the plain
   sequence is untouched. `POST /api/sequences/{id}/static-sky` submits the clean twin of an existing co-moving
   sequence.
2. **Templates per exposure.** For exposure i on grid G_i (centre c_i, wavelength λ_i, resolution element dλ_i):
   - candidates: Level 2 images of the same detector covering c_i (one footprint search per 0.5° cluster of
     centres: IRSA TAP, or IRSA MOST with a fixed-point pseudo-orbit — a = 10⁵ au, i = 90°, Ω = λ_ecl,
     ω = β_ecl, H = −35 — when TAP fails; a cluster whose search fails twice only loses its own templates);
   - not the exposure's own file, and the object more than the grid half-diagonal plus a coma margin (2′ comets,
     0.5′ asteroids) away: interpolated inside a visit, extrapolated along the nearest visit up to 30 days with a
     10 % allowance for curvature, assumed gone after that;
   - wavelength at c_i from the footprint corners (inverse bilinear map, or the header WCS) and the detector's
     wavelength map; candidates within 1.5 dλ_i are read (at most 4, the nearest first, and at least one on each
     side when available) and aligned on G_i with the same reprojection as the science frames, the wavelength
     re-measured on the aligned template;
   - one template: the median of the candidates within 0.5 dλ_i when there are two or more, else a linear
     interpolation in wavelength between the nearest below and above, else the nearest one
     (`ss_method` = median | interp | single | nearest | none per exposure).
3. **Cleaning.** clean = aligned − template − offset (sigma-clipped, measured beyond 0.6 of the half-width).
   The residual fraction ε = median |clean| / template over bright template pixels away from the object
   (clipped to 0.01–0.5) sets the star mask: pixels where the template exceeds max(3, 3/ε) × noise, dilated by one
   pixel, become NaN. Per exposure the meta records method, candidates, Δλ used, offset, ε, masked fraction and
   p99 before/after. The clean stack is the usual weighted co-add of the clean exposures per epoch and detector.
   New arrays: `clean.f32`, `clean_coverage.u16`, `clean_exposures.f16` (same layout as `coadds`, `coverage`,
   `exposures`), and `meta.static_sky`.
4. **Analyses are field jobs** (`field.jobs`, kinds `c` comet, `a` asteroid, `u` unknown movers): deduplicated by
   options, cancellable, results stored as `<seq>/field/<key>.json`, polled with `GET /api/field/{job}`.
   - **Comet** (`solar.comet`): SPHEREx-centric r_h, Δ, phase from the MOST answer (Horizons with the SPHEREx
     centre `500@-163182` as fallback); apertures of fixed radius in km on the clean exposures, masked pixels
     filled with their ring mean (a coma is nearly symmetric) and apertures with > 35 % masked skipped; Afρ and
     A(0)fρ (Halley–Marcus phase) at λ ≤ 2.5 µm against the CALSPEC solar spectrum averaged over each channel;
     bands from a weighted linear continuum on sidebands — emission (H2O 2.7, organics 3.4, CO2 4.26, CO 4.67 µm)
     as integrated flux from the channel response, absorption (water ice 1.5, 2.0, 3.0 µm) as depth — with Monte
     Carlo errors, detected at ≥ 3σ; production-rate proxies Q = 2vN/(πρ), N = 4πΔ²F/(g hν),
     v = 0.85 r_h^−0.5 km/s, g at 1 au scaled by r_h^−2, and 3σ upper limits when not detected; radial profiles
     and their log slope; band images (window minus sideband exposures); dust colour; Afρ ∝ r_h^k over visits.
   - **Asteroid** (`solar.asteroid`): ePSF fluxes of the build (or clean apertures scaled to them), reduced
     reflectance magnitudes −2.5 log(F/F_sun) − 5 log(rΔ) at light-time corrected times; a model per visit with a
     quadratic in wavelength per detector, a phase slope and a Fourier series; Δχ² periodogram summed over
     visits, zoomed to 1/20 of the grid step at the peak; the rotation rule — one brightness maximum per cycle
     (topographic prominence ≥ 10 % of the amplitude) means the peak is the second harmonic, so P = 2 × peak, and
     the odd harmonics that make the two maxima unequal are tested (χ², 4 dof per visit); reflectance spectrum on
     the 17-bins-per-detector grid after removing the rotation, thermal tail (blackbody 120–445 K fitted at
     3.5–5 µm over a 1.5–2.5 µm continuum) removed first; 1/2/3 µm band depths, 0.8–2.4 µm slope, a rough
     complex (V/S/D/X/C rules in the result) with caveats.
   - **Unknown movers** (`solar.movers`, fixed-position sequences): the moving-object detection, detector-pair check
     and linking of ADR-0048, identification of ADR-0049/0054 (or a finished field job reused); tracklets with no
     JPL match become candidates with rate, PA, angle to the ecliptic, prograde/retrograde, detections and span,
     detector pairs, real/bogus, S/N, residual and a population hint, tiered strong / candidate / weak; when JPL
     failed they are "unverified", never "unidentified". The result keeps `candidates.tracklets`, so the existing
     ADES export (station 288, follow-up data, not a submission) writes them with trkSub `U<seq3><nnn>`.
5. **Interfaces.** `spherex_core/solar/service.py` (re-exported by `services.py`), `spherex_api/routes/solar.py`,
   MCP tools `build_object_sequence`, `clean_stack`, `comet_analysis`, `asteroid_lightcurve`, `unknown_movers`,
   with `job_result` summarising each kind.

## Consequences

- The clean build reads a few hundred to a thousand extra cutouts (3I/ATLAS 16′: 576–653 templates, 1.2–2.9 GB
  from S3 in 23–39 s). The footprint searches dominate: ~20 s per cluster on MOST when TAP is down, 8 clusters
  for 3I/ATLAS (3 min), 109 for near-Earth Eros, which sweeps tens of degrees per visit (~20 min); TAP answers
  in seconds when it is up, and searches are cached per position for a week.
- Star residuals drop from p99 31–43σ to 1.6–2.5σ per exposure; 8–11 % of pixels are masked (dense Milky Way
  fields at the upper end), and an aperture that meets a masked star is filled from its ring, which assumes
  symmetry.
- Templates from six months later cover the same sky only where the survey steps repeat: per visit the LVF
  samples each detector at some wavelengths only, so a band window can be empty in one visit (C/2025 A6 epoch 0
  has no channel inside 2.62–2.78 µm) and its band fit says so.
- The comet production rates are proxies (Haser with ρ ≪ scale length, fixed velocity law, literature g-factors,
  no optical-depth or temperature model); the taxonomy is a complex, not a Bus–DeMeo type, because SPHEREx starts
  at 0.75 µm and measures no albedo. Both results carry their caveats.
- The TAP → MOST fallback here is local to the static-sky search. A general TAP fallback for the archive layer is
  separate work; when it exists, `skyframes.frames_covering` should use it.

## Alternatives considered

- **Median of the co-moving stack itself** (the classical way to remove stars for a moving object): stars move by
  only a few pixels per hour relative to slow objects, so the median keeps them; and it removes the coma, which is
  what we want to keep.
- **The fixed-sky co-add re-projected into the object frame:** the co-add mixes all channels of a detector, so its
  stars have the wrong colour at any one wavelength; LVF wavelengths differ by up to ~25 % across a detector.
- **A model sky from Gaia/2MASS/WISE fluxes and the PSF:** spectra of field stars at 102 wavelengths are not known
  well enough, and galaxies and nebulosity are missing.
- **A fixed number of templates per exposure:** wavelengths, coverage and the object's own track vary too much;
  the method ladder records what each exposure got.
