# ADR-0047: Search movers with blind per-source motion fits that several detectors agree on, plus shift-and-stack

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Nearby brown dwarfs and "Planet X"-type objects show as proper motion across the 6-month visits. With 3–4 visits the motion error is ~300 mas/yr (ADR-0026). Colour blends and halos shift centroids differently per detector and fake motions.

## Decision

Every unblended source with S/N ≥ 8 (up to 200) gets the ADR-0026 fit without a prior. A mover needs total motion ≥ 4σ and ≥ 500 mas/yr, below 15″/yr (faster than any known star: a centroid jump), and at least two detectors whose own fits are ≥ 3σ and point within 35° of the combined motion. Movers are compared with Gaia DR3 / CatWISE: within 4σ → `known_mover`, otherwise `discrepant_motion`; no catalogue motion → `unknown_mover` (ranked highest). Faint movers: static-subtracted epoch S/N maps (co-add minus median over epochs, summed over detectors) are shifted and stacked over 1–15″/yr in 15° steps; a peak must reach S/N 6 and beat zero motion by 2; saturated footprints and bright stars are masked.

## Consequences

Barnard's Star and ε Indi B come out as known movers matched to Gaia. The detector-agreement rule removes most halo artefacts but also rejects movers seen in one detector only (the coldest dwarfs, visible only in D6); those are caught by the red-excess map (ADR-0046 photometry) instead.

## Alternatives considered

Gaia-prior fits only (cannot find unknown movers); epoch-to-epoch source cross-matching (fails at 1–2 px motions).
