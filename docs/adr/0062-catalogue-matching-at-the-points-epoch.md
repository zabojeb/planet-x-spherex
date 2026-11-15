# 0062 Match catalogue counterparts at the point's own epoch position

- Status: Accepted (amends 0053)
- Date: 2026-11-15

## Context

Point analysis (ADR-0053) looks up Gaia DR3, CatWISE2020, 2MASS and SIMBAD around the clicked position and calls
the nearest source within 4″ "the counterpart". The verdict then says "no Gaia counterpart → high-proper-motion
candidate" when there is none.

The request can carry a proper motion with a reference epoch: the web client sends the target's catalogue position
at J2000 with its motion. The step moved every catalogue source to the sequence's mean epoch (~2026) but compared
it with the request position at J2000. For ε Indi B (4.7″/yr) the two are ~122″ apart, so Gaia, CatWISE and SIMBAD
all reported no counterpart and the verdict presented a famous brown dwarf as a new candidate. Only 2MASS, whose
rows have no motion and sit near J2000, "matched". The point marker was also drawn at the J2000 position, ~20 px off.

## Decision

- The point is moved to the comparison epoch with its own motion (`_point_at`). A source with a proper motion and the
  point are compared at the sequence's mean epoch; a source without one (2MASS, some SIMBAD rows) is compared at its
  own epoch, where the point is moved instead.
- Each cone is centred on the point's position at that catalogue's epoch (Gaia 2016.0, CatWISE 2015.4, 2MASS and
  SIMBAD 2000.0), still on the 0.01° cache buckets.
- `point.x/y/inside` use the point's position at the mean epoch; `point.epoch_position` reports it.
- "No Gaia counterpart" is claimed only when Gaia answered completely: not on an error or timeout, and not when the
  cone came back at the 3000-row limit (`truncated`, possible in dense fields). Otherwise the verdict says Gaia was
  not (fully) checked and makes no novelty claim.
- Known objects and nearby tracklets are compared with the point at their epochs too. `POINT_VERSION` is 2, so old
  cached analyses are recomputed.

## Consequences

ε Indi B now matches Gaia DR3 6412596012146801152 at 0.0″ (and SIMBAD and 2MASS); the verdict reads "Known object:
* eps Ind B" and compares its measured motion with Gaia's (4.42 vs 4.68″/yr). Barnard's Star is unchanged; WISE 0855
still has no Gaia counterpart (its nearest Gaia star is 7.9″ away), as it should. Regression tests feed recorded
Gaia rows through each archive's parser (ESA/MAST/IRSA columns and VizieR's).
