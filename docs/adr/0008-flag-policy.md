# ADR-0008: Contamination-flag policy

- **Status:** Superseded by [ADR-0018](0018-photometry-flag-policy.md)
- **Date:** 2026-11-14

## Context

The `FLAGS` extension is a per-pixel bitmask with 22 defined bits; names and descriptions are in the header keywords `MSKNnnnn` and `MSKDnnnn` (table in [docs/data.md](../data.md#flags)). Some bits mark pixels that are simply unusable (dead, hot, cosmic-ray hits, lost data). Others mark conditions around bright sources. `SOURCE` covers most pixels that belong to known sources. The cores of bright stars set `OVERFLOW` and `NONLINEAR`, so masking those bits would cut a hole in every bright star.

## Decision

Use two masks, defined in one place in the code:

- **Image mask** (pixels set to NaN before alignment): `TRANSIENT`, `SUR_ERROR`, `NONFUNC`, `MISSING_DATA`, `HOT`, `COLD`, `PERSIST`, `OUTLIER`, `STREAK`, `SNOWBALL`.
- **Photometry-bad bits**: the image mask plus `OVERFLOW`, `NONLINEAR`, `GHOST`, `BLOOM`, `HALO`, `CROSSTALK`. A measurement is marked `flagged` if any of these bits occurs inside its aperture, or if more than 5% of the aperture is masked.

`SOURCE`, `DICHROIC`, `FULLSAMPLE`, `PHANMISS`, `GHOST_FPA` and `GHOST_EXT` never mask anything.

Each result also ships the bitwise OR of all flags per epoch and detector (`flags.u32`), and the bit definitions read from the files, so users can inspect any flag in the viewer.

## Consequences

- Bright stars keep their shape in images.
- Photometry of saturated stars is clearly marked: all 358 measurements of Barnard's Star are flagged, and its median fluxes (1.3–2.6 Jy) are far below what its K ≈ 4.5 magnitude implies.
- Unmasked `OVERFLOW` pixels can leave residuals in difference images of bright stars.
- The policy is fixed on the server for now; it is not yet a request parameter.

## Alternatives considered

- **Mask every flagged pixel:** destroys bright stars and the many `SOURCE` pixels.
- **Mask nothing:** cosmic rays, hot pixels and streaks dominate blinks and differences.
