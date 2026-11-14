# ADR-0005: Align and co-add on the server, display on the client

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Users need to blink between epochs, subtract and stack epochs, switch detectors, make false-colour composites and adjust contrast. These interactions should feel instant. Fields are small: the default 6′ field is 59 × 59 pixels at SPHEREx's native scale. Building the aligned frames is expensive (network and CPU) and must happen on a machine near the data.

## Decision

The server does the expensive, data-dependent work once per request: reading cutouts, masking, background subtraction, alignment to a common grid, epoch grouping, co-adds and forced photometry. It writes a result directory with raw little-endian arrays and metadata:

| File | Type | Shape | Content |
|---|---|---|---|
| `coadds.f32` | float32 | [epochs, 6, n, n] | background-subtracted co-adds in MJy/sr; NaN where there is no data |
| `coverage.u16` | uint16 | [epochs, 6, n, n] | number of frames per pixel |
| `flags.u32` | uint32 | [epochs, 6, n, n] | bitwise OR of the frames' flags |
| `exposures.f16` | float16 | [frames, n, n] | single aligned exposures (stored up to 80 million voxels) |
| `meta.json` | JSON | — | grid, epochs, frames, photometry, flag definitions, target track, statistics |

The browser fetches the arrays once and does every display operation on typed arrays: stretch, colormap, blink, difference, stack and RGB composites.

## Consequences

- Display changes need no round trip.
- Payloads are small at the default field: Barnard's Star (6 epochs × 6 detectors × 59²) is about 0.5 MB of co-adds.
- The same files serve the web client, the CLI quick-look images and future MCP tools.
- Size grows with the square of the field of view; a 30′ field is 293 × 293 pixels per image.
- Clients must parse binary arrays using the shapes in `meta.json`.

## Alternatives considered

- **Pre-rendered PNG or GIF frames:** fixed stretch, a request for every change, and no client-side subtraction.
- **FITS files to the browser:** larger payloads and FITS parsing in JavaScript for no benefit.
