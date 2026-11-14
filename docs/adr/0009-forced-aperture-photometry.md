# ADR-0009: Forced aperture photometry for spectra and light curves

- **Status:** Superseded by [ADR-0016](0016-epsf-psf-photometry.md)
- **Date:** 2026-11-14

## Context

The challenge gives extra credit for generating spectra on the fly, and the variable-star targets need light curves. Each exposure samples one narrow wavelength band at the target's position, so the set of exposures from one visit is a low-resolution spectrum with up to 102 points. High-proper-motion targets move between exposures. The point-spread function is undersampled (1.2–1.5 pixels across).

## Decision

For every exposure:

1. **Position.** Propagate the target to the exposure's `MJD-AVG` (ADR-0010) and convert to pixel coordinates with the exposure WCS.
2. **Aperture.** Circle of radius r = max(1.5 px, 1 × FWHM), with FWHM = `PSF_FWHM` (header, arcsec) / √`OMEGA_MEDIAN`.
3. **Background.** Sigma-clipped (3σ) median in an annulus from max(4 px, 2.5 × FWHM) to 3 px further out, ignoring masked pixels.
4. **Flux.** Exact-overlap aperture sum (photutils) with errors from `VARIANCE`, times a Gaussian aperture correction 1/(1 − exp(−r²/2σ²)), converted from MJy/sr to mJy with the pixel solid angle `OMEGA_MEDIAN`. AB magnitude is derived from it.
5. **Wavelength.** Central wavelength and bandwidth at the target pixel from the detector's `WCS-WAVE` lookup table: bilinear interpolation in raw FITS pixel coordinates, without the celestial SIP distortion.
6. **Quality.** Flagged according to ADR-0008.

Results go into `meta.json` (`photometry`), one entry per exposure, with epoch, detector, MJD, wavelength, flux, error, AB magnitude, aperture flags and masked fraction.

## Consequences

- Spectra and light curves come from the same pass that builds the images, at no extra download cost.
- The wavelength lookup equals astropy's FITS `-TAB` result once the SIP keywords are removed from the header. With them present, astropy applies the celestial distortion to the wavelength axes and is off by up to 0.006 µm at the detector corners.
- The Gaussian aperture correction is approximate for an undersampled PSF, and absolute fluxes have not been validated against a reference.
- Saturated stars give unusable fluxes (they are flagged).
- Blends are not handled: with 6.2″ pixels a neighbour within about 10″ adds its light. This is a live issue for WISE 0855 ([docs/methods.md](../methods.md#known-limitations)).

## Alternatives considered

- **PSF photometry with the `EPSF` extension:** more accurate for undersampled data, but each file's `EPSF` table is 3.9 MB, which would multiply the download per exposure.
- **IRSA Spectrophotometry Tool:** authoritative, but a separate batch service rather than part of an interactive view.
