# ADR-0021: Add 2MASS, AllWISE and NEOWISE epochs on the sequence's own grid

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

SPHEREx covers 2025–2026, so even the fastest test targets move only 1–2 SPHEREx pixels in its data. The
analog review ([docs/research/analogs.md](../research/analogs.md), gap 1) ranks a longer baseline first:
WiseView's value comes from a decade of WISE epochs. IRSA lists `twomass_allsky`, `wise_allwise` and `neowiser`
in its CAOM TAP tables; the AllWISE Atlas tiles (4095² float32, 67 MB) and NEOWISE-R frames (1016², ~650
science frames per position over 2014–2024) are uncompressed in the public `nasa-irsa-wise` bucket
(us-west-2); 2MASS scan images are 1.5 MB gzip files on IRSA's file server. The IRSA cutout service was
overloaded (ADR-0002).

## Decision

- **Discovery:** one CAOM TAP query per survey (`spherex_core/archival/sources.py`); 2MASS and AllWISE with
  `INTERSECTS(p.poly, CIRCLE(...))` (IRSA accepts INTERSECTS only with the column first), NEOWISE with
  `CONTAINS(POINT, p.poly)`. Cached for a week.
- **Pixels:** AllWISE and NEOWISE through our range reader from S3 (only the rows covering the field); 2MASS
  downloaded whole and decompressed (`archival/reader.py`).
- **Calibration:** DN → MJy/sr with the header MAGZP and Vega zero points (2MASS: Cohen et al. 2003; WISE:
  Jarrett et al. 2011), so archival frames share units with the SPHEREx co-adds.
- **Resampling:** background-subtract, Gaussian-smooth to the SPHEREx scale (σ = 0.35 × pixel-scale ratio,
  NaN-aware), bilinear resample onto the **sequence's grid** (same TAN projection, centre and 6.15″ pixels),
  optionally a wider concentric grid (`fov_arcmin`) for targets that left the field long ago.
- **Epochs:** 2MASS images within 3 days are mosaicked into one epoch per band; all AllWISE tiles touching the
  field are mosaicked into one epoch; NEOWISE single exposures are median-combined per visit (new visit after
  20 days or once a visit spans 30 days; at least 3 frames).
- **Storage and API:** a separate result under `<cache>/archival/<sequence id>/<n>/` (`archival/store.py`);
  `GET /api/sequences/{id}/archival` (202 while building, first call starts it) and `.../archival.bin`.
  Each frame records the target's predicted pixel and whether it is inside the field.

## Consequences

- Barnard's Star on a 12′ grid: 47 frames from 2000 to 2024 (2MASS, AllWISE, 20 NEOWISE visits × W1/W2);
  the light-weighted centroid sits 0.1–2.5″ from the proper-motion prediction in every frame; 134 s and
  1.1 GB for the first build. ε Indi B at 16′: 50 frames, 0.2–1.4″ offsets, 130 s.
- Builds are seconds-to-minutes and read up to ~1.8 GB for 20′ fields, so they are background tasks
  (ADR-0023) and cached; the bucket is in us-west-2, ~70 ms from the US-East server.
- Not for solar-system objects (404). 2MASS coverage can be partial at wide fields; `coverage` reports it.

## Alternatives considered

- **unWISE time-domain co-adds:** not in the IRSA bucket (only `unwise/neo7/catalogs/`); IRSA's `wise_unwise`
  collection holds full-depth co-adds without time bounds, which smear movers.
- **IRSA IBE cutouts:** simpler, but the service was returning 503s and ~95 s per cutout during the hackathon.
- **Separate native-resolution viewer for archival images:** sharper, but the client could not blink them
  against SPHEREx without a second grid; one grid keeps the movie consistent.
