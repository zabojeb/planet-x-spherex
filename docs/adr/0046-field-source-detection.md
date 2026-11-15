# ADR-0046: Detect field sources on the co-adds and fold bright-star halos into their star

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Every later search needs a source list. SPHEREx pixels are 6.15″; saturated stars (Barnard's Star, ε Indi A, RW Aur) leave filled cores, rings and halo peaks several pixels out, which a plain peak finder lists as separate "sources" with absurd motions.

## Decision

Peaks above S/N 5 in the all-epoch mean and S/N 6 in each epoch's co-add, per detector, merged within 2 px. A peak is folded into a brighter host when the host is ≥ 5× brighter and the peak lies within 2 + 2·log10(S/N/50) px (≤ 6) or inside the host's saturated/filled footprint (from the FLAGS cube) plus 2 px. Sources within 5 px of a ≥ 5× brighter one, or inside its footprint, are marked `blended_with` and skipped by motion and variability searches. Fluxes per detector come from the all-epoch mean with r = 2 px and a nominal 1/0.80 aperture correction (approximate, for colours).

## Consequences

Spurious movers around saturated stars disappear; close faint companions of bright stars are lost to the blend flag, which is reported rather than hidden.

## Alternatives considered

photutils segmentation with deblending (slower, still splits saturated halos); a fixed merge radius (either merges real neighbours or keeps halo peaks).
