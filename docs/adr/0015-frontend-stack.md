# ADR-0015: Frontend stack and URL-addressable views

- **Status:** Accepted (implementation in progress)
- **Date:** 2026-11-14

## Context

The tool is public-facing and should be embeddable in other sites; the challenge suggests putting coordinates in the URL. The browser receives raw typed arrays and does all display operations itself (ADR-0005). The demo is judged visually, and astronomical images read best on a dark background.

## Decision

- **Stack:** Vite + React + TypeScript single-page app in `web/`, no UI framework, plain CSS with custom properties. Small libraries (for example `d3-scale`) are allowed.
- **Rendering:** images on `<canvas>` from typed arrays; overlays (target track, catalogue markers, compass, scale bar, flags) in SVG aligned to the canvas.
- **Charts:** hand-written SVG following our data-visualisation method: thin marks, markers at least 8 px, a single y-axis, hover tooltips, a legend whenever there are two or more series, ordinal colour ramps instead of rainbows, colours checked with the palette validator.
- **Theme:** dark only, using the method's dark palette and surfaces.
- **State in the URL:** target id or coordinates with proper motion, field, mode, detector, epoch, speed, stretch and overlays are kept in the query string with `history.replaceState`, so a link reproduces a view. `?embed=1` shows only the viewer and its controls, for iframes.
- **Serving:** `web/dist` is served by the API at `/`; in development the Vite server proxies `/api` to the dev API.

## Consequences

- No build-time coupling to the backend; the only contract is the HTTP API.
- Every view can be shared or embedded with a link.
- Hand-written charts need deliberate work on accessibility (labels, keyboard focus, table views).
- Large fields mean more pixels to stretch in JavaScript; fine for the 59 × 59 default, to be measured at 30′.

## Alternatives considered

- **Next.js:** server-side rendering is not needed for a client-side viewer.
- **Aladin Lite as the main viewer:** excellent sky context with HiPS surveys and catalogues, but not built for typed-array blinking and subtraction. It remains a candidate for a context panel.
- **Plotly or another chart library:** heavy, and harder to bring in line with the visualisation rules.
