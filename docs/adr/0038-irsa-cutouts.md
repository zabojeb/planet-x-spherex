# ADR-0038: Ask IRSA's cutout service first and read from S3 when it is busy

- **Status:** Accepted
- **Date:** 2026-11-14
- **Extends:** [ADR-0002](0002-s3-range-reads.md), [ADR-0037](0037-per-file-chunk-cache.md)

## Context

- **IRSA cuts SPHEREx Level 2 files on its side.** The IBE cutout service ([IRSA SPHEREx cutout tool](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/cutout_tool.html), [IBE cutouts](https://irsa.ipac.caltech.edu/ibe/cutouts.html)) takes a centre and a size on the file's IRSA URL:

  ```
  GET https://irsa.ipac.caltech.edu/ibe/data/spherex/.../level2_....fits?center=<ra>,<dec>deg&size=<n>pix
  ```

  It answers with a MEF in which every extension is cut to the same window. One request replaces the three rounds of range reads of a new file (ADR-0037), and FLAGS arrives as plain int32, so nothing is decoded.
- **The answer is not small.** Measured from the dev server for a 6′ field (95 px):
  - The answer holds PRIMARY, IMAGE, FLAGS, VARIANCE, ZODI, then the **EPSF table unmodified** and WCS-WAVE: **4.16 MB**, more than the ~1.7 MB the range reader reads for the same exposure.
  - IMAGE, FLAGS and VARIANCE come first. Reading the stream only up to the end of the VARIANCE data and then closing the connection transfers **~160 KB**.
  - A `Range` header is answered with 503, so the cut-off has to be done by closing the stream.
- **The service is often busy.** It answers "503 Server busy" to a fair share of requests:

  | Requests in flight | 503 answers | Latency of the rest |
  |---|---|---|
  | 1–8 | 20–35 % | 0.45–0.65 s unloaded, 1.7–2.9 s when the build loads it |
  | 16 | ~65 % | — |

  At 8 slots and ~2 s per answer, serving all 358 exposures of Barnard's Star from IRSA alone would take about a minute. S3 does it in seconds.
- **The pixels are the same.** Cutouts were compared against the range reader value by value: IMAGE, FLAGS and VARIANCE are identical, and the cutout's alternate WCS "A" gives its detector offset (`CRPIX1A = 1 − x0`, `CRVAL1A = 0`, `CDELT1A = 1`).

## Decision

- **`SPHEREX_CUTOUT_SOURCE=irsa` (the default) asks the service first.** A window that the chunk cache cannot serve, for a file whose layout is not known yet, is asked from IRSA when one of `SPHEREX_IRSA_IN_FLIGHT` (8) request slots is free. It is read from S3 when:
  - no slot is free (callers never wait for one);
  - the service answers anything other than 200, including 503;
  - the request takes longer than `SPHEREX_IRSA_TIMEOUT` (15 s);
  - the answer cannot be parsed, or does not cover the window.

  `SPHEREX_CUTOUT_SOURCE=s3` reads from S3 only.
- **Slots are shared by the whole build:** a semaphore across the process-pool workers (ADR-0039), or one in-process semaphore when there is no pool. So at most 8 requests are open at once, however many batches run.
- **Read the stream up to VARIANCE and close it** (`io.irsa.IrsaCutouts.fetch`). The headers are parsed as they arrive. Once IMAGE, FLAGS and VARIANCE are complete, the reader stops and the connection is dropped, so the EPSF table is never transferred.
- **Restore the detector frame.** The slim IMAGE header gets `CRPIX1/2` shifted by the cutout's offset and `NAXIS1/2` set to 2040. With that header, the window is computed exactly as it is for S3, and the cutout becomes a chunk like any other (ADR-0037), with the file's FLAGS bit definitions. The request asks for 2 px more than the window on each side (`IRSA_MARGIN`), because the service centres by its own rounding.
- **EPSF and WCS-WAVE** are still read from S3, once per detector, as before.
- **An IRSA chunk has no file layout.** A later, wider field is asked from IRSA again. If it falls back to S3, the reader learns the layout with one header read.

## Consequences

- **Same results.** Builds with IRSA first and with S3 only gave the same output files.
- **On the dev server it neither helps nor hurts.** The server is in the same AWS region as the bucket, so S3 is fast. A cold Barnard's Star 6′ build (358 exposures, 29 pool workers) took:
  - S3 only: 6.8–8.0 s;
  - IRSA first, 8 slots: 6.6–7.4 s, with 22–27 exposures served by IRSA;
  - IRSA first, 16 slots: 9.6 s, because most requests were answered with 503 and the slots were held by slow answers.

  With the opening epoch read first (ADR-0039), three runs each gave 6.6–7.9 s from S3 only and 7.8–8.0 s with IRSA first, which served 18–20 exposures.
- **Off AWS it should help.** A laptop or another region pays for every byte from S3 (87 % of a cold build on a laptop is downloading, ADR-0037). Each exposure IRSA serves is ~160 KB instead of ~1.7 MB. How much a cold build gains there depends on how many exposures find a free slot, and has not been measured yet.
- **The service is a dependency only for speed.** When it is down or busy, every exposure is read from S3, and the build takes as long as it did before.
- **Watch the format.** The parser checks the extension types, the WCS "A" keywords and that the window fits the detector, and falls back to S3 otherwise. A change of the cutout format shows up as `irsa_failed` in the build log, not as wrong pixels.

## Alternatives considered

- **IRSA only:** about a minute per build at the request rate the service accepts, and nothing at all when it is down.
- **More slots:** at 16 in flight, about 65 % of requests were answered with 503, and the build was slower than with 8.
- **Read the full answer:** 4.2 MB per exposure, more than the range reads from S3.
- **Ask with a `Range` header to skip the EPSF table:** the service answers it with 503.
