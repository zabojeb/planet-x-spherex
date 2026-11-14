# ADR-0044: Spectral variability as ratio spectra between epochs, ring points included

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

RW Aur and R Dra are saturated: their photometry is ring flux, relative within a detector (ADR-0020), so no
absolute spectrum exists, yet their variability is the point of the test. Ratios between epochs are
scale-free.

## Decision

`spectral/variability.py` bins every epoch on the grid (calibrated and ring points separately), divides each
epoch by a reference epoch (default: the one with most bins) bin by bin with a 3 % systematic per bin, drops
bins with S/N < 3 in either epoch, and reports per-bin ratios with z-scores, per-detector weighted ratios,
χ²/dof against "constant" and the detectors that changed by ≥ 3σ.

## Consequences

RW Aur's 4–5 µm fade and short-wavelength brightening come out of the same endpoint as a constant star's
flat ratios. Blends whose relative position changes between epochs (WISE 0855 and its neighbour) can show
spurious ratios; the S/N cut removes the worst cases but the result must be read with the summary's caveats.

## Alternatives considered

- Per-epoch absolute spectra only: impossible for saturated stars.
