# ADR-0048: Detect moving objects in exposure-minus-co-add images, check the simultaneous detector, link tracklets per visit

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Asteroids move degrees between visits, so their search scale is inside a visit: exposures minutes to days apart. Astrometrica links three or more frames; SPHEREx adds simultaneous detector pairs (D1/D4, D2/D5, D3/D6 image the same sky at the same moment).

## Decision

For each stored aligned exposure, subtract its epoch co-add (which rejects per-pixel outliers, so a mover is not in it) and work in S/N units. Mask bright stars (co-add S/N > 10, dilated 2 px) and saturated footprints ×1.5 + 2 px. Peaks above S/N 6 that are not single-pixel hits (sharpness ≤ 0.75) get nine features including `partner` (1 if the partner exposure with the same obs_id has S/N ≥ 3 within 1 px, 0 if not, 0.5 unknown) and a real/bogus probability (ADR-0051). Detections with p ≥ 0.3 are linked per epoch: seed pairs 0.01–3 d apart moving 7.8–3000 px/day, third and later detections within 1.5 px of the line, rms ≤ 1 px, span ≥ 2 px, monotonic progress. Two-detection pairs with S/N ≥ 10 and p ≥ 0.7 are listed separately.

## Consequences

Tracklets are straight lines in sky coordinates; below ~1.5 AU SPHEREx's orbital parallax adds ≥ 1 px of wobble that is not modelled (observer-aware linking is future work). Small fields catch few tracklets: a 30″/h asteroid crosses 6′ in 12 h.

## Alternatives considered

Detection in co-add differences between epochs (asteroids are gone); linking across visits (no orbit fitting yet).
