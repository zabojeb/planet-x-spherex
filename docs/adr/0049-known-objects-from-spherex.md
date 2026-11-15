# ADR-0049: Compute known-object positions from SPHEREx's own position and identify tracklets with Astrometrica's criteria

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

SPHEREx orbits ~7,000 km from the geocentre, so an object at D AU appears up to ~9.7″/D away from its geocentric position (docs/research/astrometrica.md: 9″ for Eros at 0.98 AU). SkyBoT with code 288 returns geocentric positions.

## Decision

SPHEREx's geocentric ICRF state comes from JPL Horizons (`COMMAND=-163182`, `CENTER=500@399`, `REF_PLANE=FRAME`, km, km/s), cached by time with retries. Known asteroids and comets come from JPL `sb_ident` with that state as `xobs`, `two-pass=true&suppress-first-pass=true`, a V limit (default 21) and the field box, cached per query. A known object identifies a tracklet when its propagated position is within max(30″, 3× its estimated error) on average, its direction within ±15° and its speed within ±30 %; matches are ranked and carry O−C per detection.

## Consequences

Positions are good for any distance; each new time costs one Horizons call and two sb_ident calls (20–50 s; JPL also returns 503s under load, which leaves tracklets unidentified with the error recorded).

## Alternatives considered

Geocentric sb_ident (wrong by several arcseconds inside ~3 AU); SkyBoT with code 288 (geocentric); propagating MPCORB locally with kete (heavier, later).
