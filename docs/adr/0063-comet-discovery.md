# 0063 Comet discovery: a comet search over any sequence and a known-comet watch

- Status: Accepted (extends 0048, 0049, 0054, 0060)
- Date: 2026-11-15

## Context

The solar-system tools (ADR-0060) analyse comets that are already known: a co-moving sequence from IRSA MOST, Afrho,
CO2/CO bands. Finding a comet nobody has catalogued needs the opposite direction: start from a field, find what
moves, and ask which movers look like comets. The field analysis' tracklets (ADR-0048) only cover straight lines
inside one visit faster than ~2"/h with detections at least 15 min apart, and nothing ranks them by how comet-like
they are. SPHEREx makes this harder and easier at once: it sees a field in ~20 short passes (often ~7 min) spread
over 10-50 days, from a low orbit whose 98-min wobble shifts an object at 1 au by 9.7", and every exposure is a
different wavelength, so the movers come with a spectrum.

## Decision

A new package `spherex_core.comets` and one job kind, `h` (comet search), on the field-job runner:

- **Movers** on a fixed-position sequence: the field analysis' tracklets (reused from a finished field job or
  detected again), *pass tracklets* (>= 4 detections at >= 3 times inside one pass of minutes, 2-300"/h) and *slow
  links* over days. Slow links use group maps (each pass's static-sky subtracted exposures combined, the per-visit
  median removed) and the model x(t) = x0 + v dt + w B(t), where B is the apparent displacement of an object at
  1 au from the observer's heliocentric motion (Earth from astropy, SPHEREx's own orbit from Horizons, cached) and
  w = 1/D; pairs vote in a Hough space, fits grow by prediction, and a link needs >= 4 passes and a significant
  motion. Fainter slow movers come from a parallax-aware shift-and-stack over (vx, vy, w) inside 6-day windows,
  capped at 8,000 trials per window and 60,000 per visit, with a threshold on the best-trial map's own spread.
- **Static sky** for every exposure is the median co-add of the *other* visits (a mover is elsewhere then), with
  bright stars masked as in the field analysis.
- **Coma test**: the object's stamps, shifted onto its track, are co-added and compared with field stars measured
  the same way (single exposures, same detectors, the object's flux per detector setting the mix). Size
  (Gaussian-weighted second moment) and concentration (annulus / core), with bootstrap errors over exposures and
  stars, a 5 % floor, the track's jitter and the different bilinear-interpolation smoothing (f (1 - f) per axis)
  corrected. Tail: the first azimuthal moment in units of the core flux, compared with the projected anti-solar
  direction (independent of distance) and the trail direction.
- **Spectral signature**: forced photometry along the track at each exposure's wavelength; CO2 4.26, CO 4.67, H2O
  2.7 um bands and 3 um ice with the band fits of ADR-0060; a 4-5 um reflectance excess as thermal context.
- **Orbit classes**: digest2-style ranging over distance and range rate with coarse population densities in
  (a, e, i), the Delaunay phase-space Jacobian and a detectability weight; constrained by the distance when an
  ephemeris (co-moving) or a parallax fit gives one.
- **Comet-likeness** 0-100 = logistic(logit P_orbit + morphology + spectrum), hand-weighted; the motion alone is
  capped at even odds, and spectral evidence counts only for a source detected along its track.
- **Identification** of the best 12 measured candidates against JPL sb_ident (asteroids and comets),
  SPHEREx-centric at each candidate's first and last time, interactive timeouts and a 150 s budget; ADES rows
  (station 288, follow-up data, not a submission) for the measured candidates.
- On a **co-moving sequence** the same tests run on its own object per visit ("is it a comet?"), which is also how
  we validate the score.
- **Known-comet watch**: JPL SBDB comets with perihelion within 2 years, two-body positions and predicted
  magnitudes now; a background refresh (one at a time across workers) follows the pinned comets (3I, C/2025 A6,
  29P by default) and the brightest others through MOST, a pinned prewarm-priority co-moving build, the comet
  analysis and the object check, and keeps per comet Afrho at 10^4 km, CO2 S/N, the activity slope and outburst
  flags (a visit >= 2x above the power law of the others at >= 3 sigma).
- API `POST /api/sequences/{id}/comet-search`, `GET /api/comets/watch`, `POST /api/comets/watch/refresh`; MCP tools
  `comet_search`, `comet_candidates`, `comet_watch`; a "Comet hunt" rail panel in Explore and Research.

## Validation (2026-11-15, server, cached sequences)

Object checks (co-moving, static sky subtracted):

| Object | Visits | Coma | Tail | CO2 | Orbit class | Score |
|---|---|---|---|---|---|---|
| 3I/ATLAS (Aug 2025, r 3.2 au) | 1 | possibly extended, 4.0 sigma (FWHM 16.9" vs stars 11.6") | - | 71 sigma | hyperbolic | 99.9 |
| C/2025 A6 Lemmon | 2 | extended 21 and 11 sigma | 4.1 / 4.9 sigma, 9 deg / 41 deg from anti-solar | 192 / 36 sigma | near-parabolic | 100 / 100 |
| 433 Eros | 6 (4 measurable) | point-like, -0.5 to +0.05 sigma | - | none | NEO | 0.1-0.2 (1.0 for the 2 saturated visits) |
| 16 Psyche | 2 | point-like, +0.3 / -0.1 sigma | - | none | main belt | 0.1 / 0.1 |

Each object check takes 1-3 s. Field hunts and their false-positive counts are in the PR description; crowded
low-latitude fields remain the weak spot (see Consequences).

## Consequences

- Known comets separate cleanly from asteroids when the object is measured on a co-moving sequence: coma, tail
  alignment and CO2 agree, and point-like asteroids score near zero.
- Field hunts are the noisy part. In crowded fields (|b| < 10 deg) static-sky residuals line up by chance; pass
  tracklets are therefore limited to 300"/h, need four detections at three times and confirmation by forced
  photometry along the track (co-added S/N >= 5), and the orbit term alone cannot make a candidate comet-like.
  A trained real/bogus model for pass tracklets and slow links is the next step.
- A comet that crosses a fixed field within one ~7-min pass gives only a handful of frames: too few for a
  morphology verdict (6 are required), so it ranks on its spectrum and motion; the co-moving check is the way to
  confirm it.
- The orbit-class prior is crude and hand-set; numbers are rankings. The watch's SBDB positions are two-body and
  good to about a degree; the co-moving builds use MOST/Horizons ephemerides.
- Cost: a comet search is a field job (cancelled when nobody polls); slow linking and the stack search are capped;
  the watch builds at prewarm priority and never displaces interactive users.
