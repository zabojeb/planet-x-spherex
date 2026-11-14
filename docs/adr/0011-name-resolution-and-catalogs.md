# ADR-0011: Name resolution and reference catalogs

- **Status:** Accepted; extended by [ADR-0021](0021-local-object-index-for-search.md) (local object index, cached and raced Sesame lookups)
- **Date:** 2026-11-14

## Context

Users type object names or coordinates, and the viewer overlays reference catalogues (the challenge suggests Gaia and WISE). On 2026-11-14:

- CDS SIMBAD TAP answered with a 404 "This page is currently unavailable" page, and VizieR TAP returned nothing.
- CDS Sesame at `cds.unistra.fr` redirected to a mirror (`vizier.idia.ac.za`) and resolved names there, including proper motions.
- ESA's Gaia TAP and IRSA's TAP (which hosts `catwise_2020`, `fp_psc` for 2MASS, `allwise_p3as_psd` and `gaia_dr3_source`) both answered.

## Decision

**Resolution order** (`resolve(query)`):

1. Built-in presets by id or name (the five test targets).
2. Coordinates: decimal degrees or sexagesimal (`17:57:48.5 +04:41:36`, `17h57m48.5s 4d41m36s`).
3. CDS Sesame, following redirects to mirrors. It returns the position at J2000 and the proper motion when known.

**Overlay catalogues**, each source carrying its own reference epoch and proper motion so the client can place it at any epoch's date:

| Catalogue | Service | Table | Position epoch | Proper motion |
|---|---|---|---|---|
| Gaia DR3 | ESA Gaia TAP | `gaiadr3.gaia_source` | 2016.0 | mas/yr |
| CatWISE2020 | IRSA TAP | `catwise_2020` | MJD 57170 (2015.4) | arcsec/yr, converted to mas/yr |
| 2MASS PSC | IRSA TAP | `fp_psc` | each source's observation date | none |

Cones are limited to 0.5° radius and 3,000 rows, and cached for 7 days. Service URLs are environment variables (`SPHEREX_GAIA_TAP_URL`, `SPHEREX_TAP_URL`, `SPHEREX_SESAME_URL`).

## Consequences

- Demos of the test targets do not depend on CDS being up.
- IRSA serves SPHEREx, CatWISE and 2MASS, so one outage pattern to watch instead of three.
- WISE 0855 does not resolve through Sesame under any of the names we tried (`WISE J085510.83-071442.5`, `WISEA J085510.74-071442.5`, `WISE 0855-0714`), so it exists only as a preset.
- The catalogue endpoints are implemented; their responses had not been checked end to end when this ADR was written.

## Alternatives considered

- **SIMBAD TAP for names and VizieR for all catalogues:** one provider and rich metadata, but both were down during the hackathon.
- **astroquery wrappers:** convenient, but they hide the exact queries, retries and timeouts we need to control.
