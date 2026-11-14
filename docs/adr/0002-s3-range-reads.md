# ADR-0002: Read Level 2 files straight from S3 with HTTP range requests

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

A sequence for one sky position needs a small cutout from every Level 2 image that covers it: 258–465 images for four of the test targets and 2,493 for R Draconis (IRSA, 2026-11-14). Each Level 2 file is a ~64 MB multi-extension FITS file (2040 × 2040 pixels per image extension).

On 2026-11-14 the IRSA cutout service (`https://irsa.ipac.caltech.edu/<uri>?center=<ra>,<dec>d&size=<deg>`) returned `503 Server busy` for 3 of 4 test requests; the one that succeeded took about 95 s for a 0.1° cutout and returned 2.06 MB, although the 58 × 58 pixel image in it is ~13 KB per extension.

The same files are in the public bucket `s3://nasa-irsa-spherex/` (us-east-1, anonymous). Byte-range requests work (HTTP 206). The image extensions are uncompressed float32; `FLAGS` is RICE_1 tile-compressed with one tile per detector row. See [docs/data.md](../data.md) for the layout.

## Decision

Implement our own asynchronous range reader (`aiohttp`) instead of using the cutout service. Per exposure it makes three rounds of requests:

1. The first 64 KiB of the file: primary header and the `IMAGE` header, which gives the WCS and the cutout window.
2. In parallel: the `IMAGE` rows that cover the window, and the `FLAGS` header together with its tile-descriptor table.
3. In parallel: the compressed `FLAGS` tiles for those rows, the `VARIANCE` header (its length is predicted per detector and verified), and the `VARIANCE` rows.

S3 has no multi-range requests, so whole 2040-pixel rows are read and the window is sliced locally. RICE tiles are decoded with astropy's codec. The per-detector `WCS-WAVE` table is read once by walking the extension headers. The reader is tested to return the same `IMAGE`, `VARIANCE` and `FLAGS` values as astropy (`backend/tests/test_reader_network.py`).

## Consequences

- Measured from the dev server in US-East: 67 ms median per request; Barnard's Star at a 6′ field built from 358 exposures in 37.7 s including a 12.3 s TAP query (2,190 requests, 718 MB).
- Values are bit-identical to astropy's for the tested file.
- Bandwidth is the cost: a 91-row window transfers 91 full rows, about 22 times the pixels we keep (~2 MB per exposure at a 6′ field).
- The reader depends on the current file layout (row tiles, 32-bit heap descriptors). Other layouts raise `NotImplementedError`; a fallback to astropy + fsspec is not implemented yet.
- Compute has to sit close to us-east-1 (ADR-0014): from the team's location each request took 1.5–10 s.

## Alternatives considered

- **IRSA cutout service:** overloaded during the hackathon, and every cutout carries ~2 MB of extra payload.
- **astropy + fsspec `section` reads:** correct and simple; measured 358 cutouts in 28.5 s with 32 workers (median 2.5 s per frame), with less control over the number and size of requests. Kept as the reference in tests.
- **Downloading whole files:** ~23 GB for Barnard's Star alone.
- **The IRSA all-sky HiPS cube:** a combined product without time resolution, so it cannot show motion.
