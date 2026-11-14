# ADR-0003: Discover images with IRSA TAP, not SIA

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The tool must find every Level 2 image whose footprint contains a position, and new SPHEREx data arrive weekly. IRSA offers two interfaces. SIA2 is simpler, but the IRSA tutorials state that SIA availability lags ingestion by about a day. TAP exposes the CAOM tables `spherex.observation`, `spherex.plane` and `spherex.artifact` immediately after ingestion. Current collections are `spherex_dr1`, `spherex_dr1_deep`, `spherex_qr3` and `spherex_qr3_deep`; QR2 is superseded. The challenge asks for a tool that is easy to update as releases arrive.

## Decision

Use one TAP query that joins `spherex.artifact` ⨝ `spherex.plane` ⨝ `spherex.observation`, selects images with `CONTAINS(POINT('ICRS', ra, dec), p.poly) = 1`, restricts to the configured collections and `producttype = 'science'`, and returns the file URI, detector (`energy_bandpassname`), time bounds (MJD), wavelength bounds, collection and observation id.

- The S3 URL is derived from the URI by dropping the `ibe/data/spherex/` prefix.
- Results are de-duplicated per (observation id, detector), keeping the collection listed first. The default order is DR1, DR1 deep, QR3, QR3 deep.
- Collections come from the environment variable `SPHEREX_COLLECTIONS`, so a new release is a configuration change.
- For moving targets the query uses the position propagated to 2026.0 (ADR-0010). Footprints are about 3.5° wide, so arcminute motions do not change the result except at detector edges.
- Results are cached for 12 hours (ADR-0012).

## Consequences

- No ingestion lag beyond our own cache lifetime; the newest weekly data appear automatically.
- Measured query times were 2.7–14.7 s, so caching matters for interactive use.
- We depend on IRSA's table and column names (`time_bounds_lower`, `energy_bandpassname`, `poly`).
- For Barnard's Star we saw no exposure in both DR1 and QR3 (DR1 covered the visits up to May 2026, QR3 the August–September 2026 visit). The de-duplication is a safeguard, not a measured necessity.

## Alternatives considered

- **SIA2 through astroquery:** less code, but about a day of lag and less control over the returned columns.
- **A local metadata index refreshed weekly (for example DuckDB):** would make search instant and give a precise "what's new" feed. Deferred; it fits the release-monitor objective.
