# ADR-0042: Classify with Pickles and SpeX standards on the bin grid, below 2.45 µm

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

We want a spectral type with probabilities for any source. No public template library with real spectra
covers 0.75–5 µm for both stars and brown dwarfs; model grids (Sonora, BT-Settl) are gigabytes.

## Decision

`spectral/templates.py` downloads on first use and caches as one .npz: Pickles (1998) UVKLIB, 131 stars
O5–M10 of all luminosity classes, 0.115–2.5 µm, from STScI CDBS (public domain); and the SpeX Prism spectral
standards M0–T9 (Burgasser 2014 and references) from the SPLAT repository (MIT licence). Each template is
converted to F_ν and averaged per bin over a Gaussian with the channel width plus the bin width. `classify.py`
fits a free scale per template on the bins below 2.45 µm with a 3 % systematic floor, ranks by χ²/dof and
converts Δχ² into relative probabilities after inflating errors so the best fit has χ²/dof = 1; it also
sums probabilities per letter class. A blackbody + extinction grid (Indebetouw 2005 law), an IR spectral
index α (2–5 µm, Lada classes, indicative) and the 3.3 µm methane index cover what templates cannot.

## Consequences

ε Indi B (T1+T6) comes out as a T3 standard; the comparison star as G2V, with synthetic J/H/Ks/W1 within
0.02 mag of 2MASS/CatWISE. No QSO or galaxy templates yet: such sources get a stellar best fit with a poor χ²
and should be read through α and the features. The first classification on a fresh cache downloads ~7 MB
(about 15 s).

## Alternatives considered

- Sonora/BT-Settl model grids to 5 µm: right physics, far too large to download per deployment.
- SPLAT as a dependency: heavy, pulls its whole data tree; we only need 30 standards.
