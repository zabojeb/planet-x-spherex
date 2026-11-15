# ADR-0013: Process pool and slim FITS headers for per-frame work

- **Status:** Accepted, extended by [ADR-0039](0039-batched-fetch-and-previews.md) (reading and parsing moved onto the pool too)
- **Date:** 2026-11-14

## Context

With every cutout already cached, rebuilding Barnard's Star (358 exposures) still took 19.2 s, so CPU work, not the network, was the bottleneck. Profiling 40 exposures gave 43 ms per exposure, of which about 34 ms was building an astropy `WCS` from the full ~300-card `IMAGE` header (card verification and SIP parsing). The per-exposure work ran in a thread pool, where Python-heavy code (astropy, photutils) serialises on the GIL.

## Decision

- **Slim headers.** Right after parsing, keep only the celestial WCS and SIP keywords plus the metadata we use: `EXTNAME`, `DETECTOR`, `OBSID`, `MJD-AVG`, `MJD-BEG`, `MJD-END`, `DATE-AVG`, `PSF_FWHM`, `OMEGA_MEDIAN`, `XPOSURE`, `BUNIT`. The WCS built while choosing the cutout window is reused.
- **Process pool.** Run per-exposure processing (masking, background, alignment, photometry) in one shared `ProcessPoolExecutor` (spawn start method, `SPHEREX_CPU_WORKERS`, default 24). Network reads stay in the asyncio event loop, so reading and processing overlap.

## Consequences

- Cached rebuild of Barnard's Star: 19.2 s → 11.9 s with slim headers → 5.2 s with the process pool. The last figure includes starting the pool, which the command-line script pays on every run and the API pays once.
- Each worker is a separate Python process with its own memory (astropy, SciPy, photutils loaded).
- Arguments and results are pickled; with slim headers and small cutouts this is cheap.
- Full headers are no longer kept with cutouts. Code that needs another keyword must add it to the slim-header list.

## Alternatives considered

- **More threads:** no gain while the GIL is the limit.
- **Rewriting the hot path in a compiled language:** not needed once the WCS cost was removed.
