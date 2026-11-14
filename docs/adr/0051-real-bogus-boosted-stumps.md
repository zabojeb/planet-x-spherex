# ADR-0051: Rank difference-image detections with NumPy boosted stumps trained on injections, validated leaving one field out

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Most difference-image peaks are residuals of stars, cosmic rays and artefacts. There is no labelled SPHEREx set and no ML dependency in the service.

## Decision

Nine features (log S/N, sharpness, roundness, dipole depth, distance to a bright star, artefact flags, masked fraction, edge distance, simultaneous-partner check). Gradient boosting with 80 depth-1 stumps (log-loss, Newton leaves, learning rate 0.3) in NumPy; the model is a readable JSON file (`field/models/realbogus.json`) with its validation. Training (`python -m spherex_core.field.train_realbogus`): 1,500 Gaussian point sources per field (FWHM 1.2–2.0 px, peak S/N 6–30, a 0–1.5× copy in the partner exposure) injected into real difference maps as positives; the detections our search finds in the same maps as negatives; leave-one-field-out over six fields.

## Consequences

Leave-one-field-out: mean precision 0.87, mean recall 0.90 at p ≥ 0.5 (precision is a lower bound because real asteroids among the negatives count as false positives; it is lowest, ~0.71, in the crowded ε Indi and R Dra fields). The model must be retrained when features change (a stale model is ignored and a heuristic is used).

## Alternatives considered

scikit-learn gradient boosting (new dependency, opaque pickle); a CNN on cutouts (needs far more labelled data).
