# ADR-0030: Exports as FITS with WCS, CSV/VOTable photometry and a download script

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The Data Explorer and IRSA Viewer let users download data; researchers need to take results into their own tools.

## Decision

- `GET .../export/coadds.fits`: one co-add (`epoch` + `band`) or a multi-extension file with one image per epoch and detector (`EXTNAME E<epoch>_<band>`), optionally with coverage; the grid's TAN WCS, `BUNIT = MJy/sr`, epoch dates, wavelength range, frame count and provenance (sequence id, algorithm version, target and motion, collections, SPHEREx acknowledgement).
- `GET .../export/photometry.csv|vot`: the photometry with exposure provenance (obs id, collection, file), units and column descriptions (VOTable).
- `GET .../export/download.sh`: one `curl` per original Level 2 file, from IRSA or the public S3 bucket, or IRSA cutouts of the field.

## Consequences

Barnard's Star: 366 kB FITS, 83 kB CSV, 192 kB VOTable, 359-line script. Exports are generated on demand from the stored result (no extra storage). The script needs the footprint search to recover file paths (TAP cache or a ~15 s query).

## Alternatives considered

- A zip of single-image FITS files: one MEF is easier to open in DS9/astropy and keeps provenance in one primary header.
- ECSV instead of CSV: CSV opens everywhere; VOTable carries the units.
