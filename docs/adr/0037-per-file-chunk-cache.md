# ADR-0037: Cache rows per Level 2 file and read VARIANCE only near the target

- **Status:** Accepted
- **Date:** 2026-11-14
- **Extends:** [ADR-0002](0002-s3-range-reads.md); supersedes the native-cutout layer of [ADR-0012](0012-caching.md)

## Context

- **Field changes re-read everything.** The cutout cache of ADR-0012 was keyed by file URL, RA, Dec and half-size. Any change of the field of view missed for every exposure and read the same rows from S3 again. On the dev server (vast.ai, US-East, the same region as the bucket), going from a 6′ to a 10′ field for Barnard's Star took 26.3 s and read 1132 MB, although the 6′ rows were already on disk.
- **Downloading dominates a cold build.** On a laptop, 87 % of a cold build is spent downloading bytes. The archive query (~12 s, IRSA side) and the processing are small next to it.
- **Most VARIANCE rows are never used.** The reader fetched IMAGE, FLAGS and VARIANCE for the whole cutout. VARIANCE is only used by the photometry, and no measurement looks farther than 21 px from the target: the ring background annulus is 16–21 px (ADR-0020), and the ePSF fit is smaller.
- **Rows come full-width anyway.** IMAGE and VARIANCE are row-major float32, and FLAGS is RICE_1 compressed with one tile per row. A byte range therefore always covers whole detector rows, so a wider window in columns costs no extra transfer, only extra disk.

## Decision

- **One chunk per file.** `cache.chunk(url)` is `chunks/<aa>/<sha1(url)>.npz`, with a format version (`CHUNK_FORMAT`). It holds:
  - the slim IMAGE header;
  - the file's byte layout: the IMAGE data offset, the FLAGS tile descriptors and heap start, and the VARIANCE header and data offset;
  - the IMAGE and FLAGS rows read so far, as a half-open range of detector rows, and the VARIANCE rows read so far, as a separate range;
  - a band of columns for a field of `SPHEREX_CUTOUT_CACHE_FOV` (30′ by default), centred on the first window read.

  An exposure where the target is off the frame keeps a header-only chunk, so it is never read again.
- **A request** (`Level2Reader.cutout(..., keep_half=, variance_at=, variance_radius=)`) is answered from the chunk if possible:
  - **Window inside the held rows and band:** no request.
  - **Window overlapping or touching the held rows:** only the missing row ranges are read, all in one round trip, because the layout is known. The new rows are merged into the chunk.
  - **Window disjoint from the held rows, or outside the band:** the chunk starts over with the new window. The layout is kept, so no header is read.
- **First read of a file** still takes three round trips:
  1. the IMAGE header;
  2. the IMAGE rows, together with the FLAGS header and tile index;
  3. the FLAGS tiles, the VARIANCE header and the VARIANCE rows.

  The VARIANCE header length is predicted per detector and checked on every read. A wrong guess costs one more round trip.
- **VARIANCE window.** VARIANCE is read only for rows within `photometry.VARIANCE_RADIUS` (22 px) of the target's position at the exposure's `MJD-AVG`. That radius is the farthest any measurement looks, plus one pixel. The rest of the returned variance is NaN.
- **Each chunk keeps its own file's FLAGS bit definitions** (`MSKN`/`MSKD`). Files of different pipeline versions define different bits (bit 29 `SATELLITE_HALO` only in newer ones), so a build lists every bit any of its files defines, merged in frame order (`flags.merge_flag_defs`), and the result does not depend on which file happened to be read first.
- **Writes are atomic:** each chunk is written to a uniquely named temporary file and then renamed into place. Old `cutouts/` entries are still read on an exact key hit and converted to chunks, which learn the layout on their first wider read.

## Consequences

- **Same results.** Builds at 4′, 6′, 10′ and 16′, and a 6′ build served from 16′ chunks, gave the same bytes in every output file and the same `meta.json` (apart from run statistics) as the old reader. `test_variance_is_read_only_within_variance_radius` checks that the ePSF and ring photometry are unchanged with VARIANCE cut to the radius, and that they change when it is cut 3 px tighter.
- **Field changes are cheap.** Barnard's Star on the dev server (358 exposures, 64 in flight on 96 connections, old code against new code on the same machine):

  | Step | Old | New |
  |---|---|---|
  | Cold 6′ | 21.5–22.4 s, 744 MB | 19.8–20.4 s, 611 MB |
  | then 4′ | 16.2 s, 542 MB | 4.4 s, no requests |
  | then 10′ | 26.3 s, 1132 MB | 12.1 s, 252 MB |
  | then 16′ | 36.1 s, 1710 MB | 13.3 s, 349 MB |
  | back to 6′ | — | 4.5 s, no requests |

  The cold build reads 18 % fewer bytes, because VARIANCE is limited to the target's rows. The warm builds of ~4.4 s are mostly the start-up of 24 pool workers in a fresh process.
- **Disk.** A chunk at a 6′ field is ~0.45 MB, against ~0.1 MB for an ADR-0012 cutout, because the band is 30′ wide. Barnard's Star takes 162 MB after a 6′ build and 323 MB after growing to 16′. Old cutouts for the same four fields took 412 MB. There is still no eviction.
- **Fields wider than 30′** fall outside the band and restart the chunk on every change. Raise `SPHEREX_CUTOUT_CACHE_FOV` if the viewer ever allows them.
- **Wider photometry needs a larger radius.** A measurement that looks farther than 22 px from the target must raise `VARIANCE_RADIUS`, or it sees NaN variance. The test above fails if the radius is too small for the current measurements.
- **The dev server is now limited by the event loop, not by S3** (addressed by [ADR-0039](0039-batched-fetch-and-previews.md), which moves this work into the pool workers). A cProfile of a cold 6′ build there used 22.7 s of main-process CPU in 23.9 s of wall time:

  | Work | CPU |
  |---|---|
  | Building the WCS from each header | 5.3 s |
  | Slimming headers | 4.0 s |
  | Parsing headers | 2.9 s |
  | Decoding FLAGS tiles | 2.7 s |
  | TLS and socket reads | 2.6 s |

  More concurrency helps up to that limit. 256 connections with 128 frames in flight took 17.5–17.8 s, against 28.3 s at the defaults (96 and 64).

## Alternatives considered

- **Keep per-request cutouts, keyed by a rounded field:** every change of the field across a rounding step would still read everything again.
- **Cache whole files:** about 35 MB of IMAGE, FLAGS and VARIANCE per exposure, so ~12 GB and a long first read for Barnard's Star. A cutout read needs ~1.7 MB.
- **Full-width rows:** a 2040-column row is about 5× the 30′ band, and the extra columns are disk that no field uses.
- **Fixed blocks of rows (e.g. 64-row blocks):** simpler bookkeeping, but up to 63 extra rows at each edge of a window that is 91 rows tall at 6′.
