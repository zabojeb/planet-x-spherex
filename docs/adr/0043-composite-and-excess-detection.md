# ADR-0043: Flag infrared excess and composite spectra from a short-wavelength fit

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

WISE 0855 sits 8″ from a G=16.3 star in 2025–26, so its SPHEREx photometry is a blend: a normal star below
2.5 µm and the cold Y dwarf at 4–5 µm. Discs around young stars and cold companions give the same signature.
A single label from templates (which stop at 2.5 µm) would call it a K star and miss the interesting part.

## Decision

A blackbody + extinction fit restricted to λ ≤ 2.45 µm is extrapolated to 3–3.8 and 3.8–5 µm; the observed /
predicted ratio and its significance (with 9 % extrapolation uncertainty) are reported as `ir_excess`. The
summary labels a source `composite` when templates prefer a normal star but the spectrum shows methane at
3.3 µm and a > 1.5×, > 5σ excess at 3.8–5 µm; `yso_candidate` when a star shows the excess and α > −1.6;
`brown_dwarf` when an L/T standard wins or methane is strong.

## Consequences

WISE 0855's sequence reads "composite: <star> + cold methane-rich source" instead of a wrong single type.
Saturated stars (ring photometry only) have no calibrated spectrum and get no classification.

## Alternatives considered

- Two-component template fits (star + brown dwarf): no brown-dwarf templates beyond 2.5 µm to make it work.
