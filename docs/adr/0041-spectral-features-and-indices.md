# ADR-0041: Measure curated features against power-law side windows, plus automatic detections

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

SPHEREx spectra (R 35–130) show molecular bands (H₂O, CH₄, CO, CO₂), ices (H₂O 3.05, CO₂ 4.27, CO 4.67 µm),
PAH 3.3 µm and recombination lines (Paα, Brγ, Brα). A first version interpolated the continuum linearly and
"detected" water at 2.7 µm at 11σ in a G star: a Rayleigh–Jeans F_ν ∝ λ⁻² SED is convex, so linear
interpolation between distant side windows fakes absorption.

## Decision

`spectral/features.py` keeps a curated list (16 features) with band and side windows. Two sides: the continuum
is interpolated as a power law (log-log). One side (where the other side is itself absorbed in cool dwarfs):
the side flux is carried to the band centre with the local log-log slope of a smooth pseudo-continuum.
Window means include a 2 % systematic floor. Every feature reports depth, error, equivalent width and
significance, detected or not. Flux-ratio indices (H₂O 1.4/1.9, CH₄ 1.6/2.2/3.3, CO 2.3, 4 µm peak/J,
K/4.5 µm) need a detected denominator. Automatic detection fits a cubic spline continuum in log-log with
asymmetric clipping and reports runs of bins beyond 3σ (narrower than 0.5 µm), identified against the list;
a `continuum.reliable` flag says when molecular bands cover so much of the spectrum (cool dwarfs) that the
automatic detections are not meaningful. Ice and PAH detections are suppressed in the summary of cool dwarfs,
whose molecular bands occupy the same windows.

## Consequences

No false detections on the G=13.9 comparison star; the T dwarf ε Indi B shows all its bands at high
significance. Narrow lines are diluted at this resolution, so their depths are lower limits.

## Alternatives considered

- Fitting each feature with a Gaussian on a local polynomial: too few bins per feature at R≈40.
- Using the best template as continuum: templates stop at 2.5 µm.
