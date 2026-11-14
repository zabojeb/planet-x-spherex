# ADR-0029: Lomb–Scargle periodograms on the client's relative light curve

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The challenge's variable stars need periods, and IRSA Viewer's Time Series tool offers periodograms and phase folding. SPHEREx samples each source in visits ~6 months apart with dense sampling inside a visit, and each exposure is at a different wavelength.

## Decision

`GET /api/sequences/{id}/periodogram` and `POST /api/periodogram` (client-supplied records) normalise fluxes exactly like the web client's light curve (102 log-spaced wavelength bins, calibrated and ring measurements separately, upper median of unflagged points; `analysis/lightcurve.py`), drop flagged points unless asked, and run astropy `LombScargle` (Baluev false-alarm probability) or `LombScargleMultiband` (one model per detector, for amplitudes that change with wavelength). Default period range 0.5 d to the baseline, 10 samples per peak. The response has the best period, top peaks, a downsampled periodogram (maxima kept), phase-folded points and the model curve.

## Consequences

R Dra: 235.7 d (standard, power 0.46) and 246.9 d (multiband, power 0.54) vs the catalogue ~245 d, in 0.1–0.3 s. The same normalisation on server and client means the folded curve matches the plotted light curve. With three visits (RW Aur) the "best" period (131 d) is mostly the visit spacing; the false-alarm probability does not know about the window function, so short baselines need caution.

## Alternatives considered

- Periodogram in the browser: possible, but astropy's multiband model and FAP are not available in JS.
- Raw fluxes without normalisation: the wavelength dependence of the spectrum would dominate the periodogram.
