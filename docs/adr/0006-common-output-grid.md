# ADR-0006: Common output grid for all exposures

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Every exposure has its own pointing, rotation and SIP distortion, and within one visit the target falls on different parts of the detector as the spacecraft steps through the linear variable filter. Exposures must be resampled onto one grid before they can be co-added, blinked or subtracted. SPHEREx pixels are about 6.15″ (√`OMEGA_MEDIAN` = √37.86 arcsec²) and the point-spread function is about 1.2–1.5 pixels wide, so the data are close to undersampled.

## Decision

- **Projection:** gnomonic (TAN), north up, east left, at the native pixel scale of 6.15″.
- **Size:** n = ceil(FOV / 6.15″), at least 9, made odd so the target falls on the central pixel. A 6′ request gives n = 59 (6.05′).
- **Centre:** the target's position at the median MJD of all its exposures, after proper-motion propagation (ADR-0010).
- **Native cutout:** half-size ceil(n / 2 × 1.42) + 3 pixels, so any rotation of the grid is covered.
- **Mapping:** evaluate the exposure WCS (with SIP) on a 9 × 9 control grid of output pixels, interpolate the input pixel coordinates with a cubic bivariate spline, then resample the image bilinearly. Masked pixels and points outside the cutout become NaN. Flags are resampled nearest-neighbour.

## Consequences

- No artificial oversampling; each displayed pixel corresponds to one SPHEREx pixel, which keeps data volumes small.
- The control grid avoids running the iterative SIP inversion for every output pixel.
- Slow targets stay at the centre. Fast movers drift along their predicted track (shown as an overlay, ADR-0010): Barnard's Star moves about 1.6 pixels between its first and last full visits.
- Bilinear resampling smooths slightly and grows masked areas by about one pixel.
- We have not yet measured the spline approximation error; it is expected to be far below a pixel for fields up to 30′.

## Alternatives considered

- **The `reproject` package** (`reproject_interp`, `reproject_adaptive`): flux-conserving options, but slower per frame and another dependency.
- **Oversampled output (for example 3″ pixels):** smoother pictures with four times the data and no new information. Smooth display can be done in the browser.
- **Drizzle:** more accurate for undersampled data, but too heavy for an interactive tool.
