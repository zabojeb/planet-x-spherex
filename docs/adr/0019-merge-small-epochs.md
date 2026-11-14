# ADR-0019: Merge small epochs into close neighbours, otherwise mark them partial

- **Status:** Accepted
- **Date:** 2026-11-14
- **Extends:** [ADR-0007](0007-epochs-and-coadds.md)

## Context

Grouping by a 20-day gap with a 45-day maximum length (ADR-0007) leaves "mini-epochs" of 2–18 exposures on one or two detectors, made of stray exposures a few weeks after a visit. Examples: Barnard's Star 2025-09-30 (10), 2026-05-04 (8) and 2026-09-22 (2); WISE 0855 2025-12-11 (18); ε Indi B 2025-06-10. Their co-adds are noisy and miss detectors. The 10-exposure Barnard's Star epoch put the star 25″ off its track because of the co-add hole that [ADR-0017](0017-nan-aware-resampling-and-filled-coadds.md) now fixes. In the blink they look like real visits.

## Decision

After the gap and maximum-length grouping, `processing.epochs.merge_small_epochs` repeatedly folds the smallest epoch with fewer than 20 exposures (`MIN_EPOCH_FRAMES`) into its nearest neighbour in time. It does so only if the gap between their closest exposures is under 60 days (`MERGE_GAP_DAYS`); on equal gaps the larger neighbour wins. Epochs are renumbered in time order. An epoch still below 20 exposures afterwards is kept and marked `partial: true` in `meta.epochs`, so the client can skip it in the blink. Both constants are reported in `meta.processing`.

## Consequences

- Visits no longer split into a main epoch plus a noisy tail. A merged epoch may span more than `max_epoch_days`, which is acceptable because the extra exposures lie within two months.
- Isolated small epochs (for example a single early visit) stay visible but are labelled.
- The constants are not request parameters, so they do not change the sequence id. Changing them requires bumping `ALGO_VERSION`.

## Alternatives considered

- **Dropping small epochs:** this loses real data, sometimes the only data at a date.
- **A larger `gap_days`:** this would merge the tails but also join genuine visits near the ecliptic poles, where coverage is almost continuous.
