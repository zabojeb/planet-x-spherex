# ADR-0007: Epoch grouping and per-detector co-adds

- **Status:** Accepted; extended by [ADR-0017](0017-nan-aware-resampling-and-filled-coadds.md) and [ADR-0019](0019-merge-small-epochs.md)
- **Date:** 2026-11-14

## Context

SPHEREx sees most of the sky in visits about six months apart. One visit lasts from a day to about five weeks; the main Barnard's Star visits lasted 21–37 days. Near the ecliptic poles coverage is almost continuous: R Draconis has 2,493 images between April 2025 and September 2026.

Each exposure is effectively a narrow-band image, because wavelength changes across each detector. One full visit covers all 17 channels of every detector, and single exposures are noisy. The challenge suggests adding together the data from each detector to raise the signal-to-noise ratio.

## Decision

**Epochs.** Sort exposures by MJD. Start a new epoch after a gap longer than 20 days, or when the current epoch already spans 45 days. Both values are request parameters (`gap_days`, `max_epoch_days`).

**Co-adds,** for each epoch and detector:

1. Subtract each exposure's background: the sigma-clipped median (3σ, 5 iterations) of its unmasked pixels.
2. Set masked pixels (ADR-0008) to NaN and align to the common grid (ADR-0006).
3. With at least 4 exposures, reject pixels that differ from the per-pixel median by more than 5 × max(1.4826 × MAD, median exposure rms). This removes asteroids and cosmic rays that the flags missed.
4. Take the weighted mean with one weight per exposure, 1/rms², where rms is the exposure's sigma-clipped background scatter.

## Consequences

- Barnard's Star produces 6 epochs: three full visits of 108–117 exposures and three small groups of 2–10 exposures that cover only some detectors. R Draconis is split into roughly 45-day bins, which suits a light curve.
- One weight per exposure avoids biasing bright pixels, whose per-pixel variance includes the source's own noise.
- A detector co-add mixes 17 wavelengths. For a strongly coloured source, the co-add depends on which channels an epoch covered, so a partial epoch can look brighter or fainter without real variability.
- Small epochs are noisy and may have no data in some detectors. The client shows frame counts per epoch.
- The background estimate includes sources. This is fine for small fields but could bias crowded ones.

## Alternatives considered

- **Fixed calendar bins:** split visits arbitrarily.
- **Per-channel co-adds:** about one exposure per channel per visit, so no gain.
- **Median stacks:** about 25% worse noise than a mean for the same data, though robust; we keep a robust step (clipping) and use the mean.
