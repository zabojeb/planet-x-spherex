# ADR-0018: Flag policy v2: HALO informational, 20 % masked threshold, saturation excluded rather than bad

- **Status:** Accepted
- **Date:** 2026-11-14
- **Supersedes:** [ADR-0008](0008-flag-policy.md)

## Context

The ADR-0008 policy flagged 16–24 % of the measurements of unsaturated stars. The validation measured how far flagged points deviate from the median flux at the same wavelength:

| Condition | Median deviation |
|---|---|
| Normal points | 2.3 % |
| `TRANSIENT`, `SUR_ERROR`, `NONFUNC`, `PERSIST` | 4–6 % |
| `HALO` | 3.3 % (vs 2.5 % without it), yet set on ~18 % of points |
| Masked fraction 0.05–0.2 | only mildly worse |

Only the first group really marks outliers. In addition, ePSF fits ([ADR-0016](0016-epsf-psf-photometry.md)) can leave saturated pixels out instead of being spoiled by them.

## Decision

All definitions live in `spherex_core/flags.py`.

- **Image mask (unchanged).** `MASK_DEFAULT` = `TRANSIENT`, `SUR_ERROR`, `NONFUNC`, `MISSING_DATA`, `HOT`, `COLD`, `PERSIST`, `OUTLIER`, `STREAK`, `SNOWBALL`. `SOURCE` is never masked, and `OVERFLOW` / `NONLINEAR` stay visible in images.
- **Photometry-bad bits.** `PHOT_BAD` = `MASK_DEFAULT` | `SATURATION` (`OVERFLOW`, `NONLINEAR`) | `ARTIFACTS` (`GHOST`, `BLOOM`, `CROSSTALK`). `HALO` is informational.
- **Masked-fraction threshold.** A measurement is flagged when more than 20 % of its aperture or PSF weight sits on masked pixels (`MAX_MASKED_FRACTION`, was 5 %).
- **ePSF fits** do not treat saturation as bad. Saturated pixels are excluded and recorded as `saturated`. The fit is flagged by PSF weight, masked fraction or artifact bits (ADR-0016).
- **Aperture values** stay flagged when they contain any `PHOT_BAD` bit, saturation included.
- **Pipeline bits** use the top of the `uint32` flag word, which SPHEREx leaves unused (bits 0–28 are defined). Bit 31 `FILLED` marks co-add pixels filled from masked values ([ADR-0017](0017-nan-aware-resampling-and-filled-coadds.md)).

## Consequences

- Fewer good measurements are hidden, so spectra and light curves of normal stars keep more points.
- `meta.flag_bits` now lists `FILLED` with `pipeline: true`, and `phot_bad` is false for `HALO`. Clients should take the bit list from `meta.flag_bits` rather than hard-coding it.
- The policy is still fixed per build. Users can display any bit but cannot change the masks.

## Alternatives considered

- **Dropping flagged points from the outputs:** we keep every measurement and mark it, so the viewer can show it hollow and an analyst can decide.
- **Making `GHOST_FPA` and `GHOST_EXT` photometry-bad as well:** not supported by the validation numbers. They are rare at our targets and stay informational.
