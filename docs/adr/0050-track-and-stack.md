# ADR-0050: Track & Stack within one visit, and require two of three sub-stacks in the synthetic-tracking search

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Faint asteroids and slow distant objects (0.5–1″/h at quadrature) are invisible in single 113.6 s exposures. Astrometrica stacks along a motion; the MPC warns that stacked noise can look like detections.

## Decision

`trackstack.stack` shifts every aligned exposure of a visit by v·(t − t_ref) (NaN-aware bilinear), combines by weighted mean (1/rms²), median or sum, optionally in 2–3 time-ordered sub-stacks, t_ref = weighted mean time, on raw or static-subtracted images. `trackstack.search` runs synthetic tracking on static-subtracted S/N maps within the densest window (default 48 h) of a visit: a velocity lattice (≤ 41 per axis, half-pixel end-to-end steps where possible) on 2× upsampled maps with integer shifts; a candidate needs S/N ≥ 6 and S/N ≥ 3 in at least two of three independent time thirds at its best velocity.

## Consequences

Interactive stacks take well under a second for a 6′ field; searches take seconds per visit. Wide windows with slow objects leave the field; the window is reported.

## Alternatives considered

Stacking across visits (objects move degrees); FFT-based shift search (more code, no gain at our sizes).
