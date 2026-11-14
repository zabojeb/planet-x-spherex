# ADR-0040: Build spectra on one fixed grid of 17 geometric bins per detector

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Each SPHEREx exposure measures a source at one wavelength set by where it falls on the linear variable
filter, with a bandwidth of about λ/R. A target gets 250–2500 such points at scattered wavelengths. To
compare epochs, sources and templates we need spectra on common wavelengths with honest errors.

## Decision

`spherex_core/spectral/grid.py` defines 102 bins: 17 per detector, geometric between the detector's nominal
range widened by 1 %, so bin width ≈ the channel width (λ/R). `assemble.bin_spectrum` combines the points of
a bin with inverse-variance weights after a 4σ robust cut (median ± max(MAD, median error)); the bin error is
the larger of the formal error and the scatter error of the mean. Calibrated (PSF/aperture, unflagged)
points form absolute spectra; ring points of saturated stars (ADR-0020) are binned separately as relative
spectra. Spectra are built overall and per epoch.

## Consequences

Templates are integrated onto the grid once and cached; spectra of any two epochs or sources compare bin by
bin. Bins in detector overlaps (e.g. D1/D2 near 1.1 µm) are kept separate, which shows inter-detector
calibration offsets instead of hiding them. Wavelength resolution is capped at one channel; sub-channel
information from the LVF stepping is not used.

## Alternatives considered

- Kernel-smoothed spectrum on a fine grid: correlated errors, harder χ² against templates.
- The 102 "official" channel definitions from IRSA's spectral calibration product: more exact edges, but an
  extra download per detector for little gain at this stage.
