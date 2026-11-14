# ADR-0031: Release summary, freshness checks and optional build filters

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The brief asks for a tool that is easy to update as releases arrive (weekly QR3 ingestion). Users also want to restrict a build to dates or detectors (IRSA tools filter by date and detector).

## Decision

- `GET /api/releases`: per SPHEREx collection the image count and observation time span from one TAP aggregate over `spherex.plane` (14–29 s at IRSA), cached for an hour.
- `GET /api/sequences/{id}/freshness`: re-runs the sequence's footprint search without the cache (which also refreshes that cache, so a forced rebuild picks the new files up) and counts exposures newer than the sequence's latest one; cached for ten minutes.
- `SequenceParams.date_min/date_max` (MJD) and `bands`, exposed as `date_min`/`date_max` (UTC dates) and `bands` in `POST /api/sequences`. They enter the cache key only when set, so every existing sequence id stays valid.

## Consequences

The client can show "N new exposures — rebuild" and a data-freshness line (on 2026-11-14: QR3 up to 2026-10-05, DR1 up to 2026-05-14). The release summary is expensive for IRSA, hence the cache.

## Alternatives considered

- Polling on a schedule: belongs to the planned release monitor service; this is the on-demand building block for it.
- Filtering in the client only: a build over fewer exposures is faster and gives co-adds of exactly the chosen data.
