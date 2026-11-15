# ADR-0061: Serve the sky map from our own HiPS cache, give the map its own tools, and let users pick any point

- **Status:** Accepted (extends 0058)
- **Date:** 2026-11-15

## Context

ADR-0058 put the field on an Aladin Lite map of the SPHEREx DR1 all-sky HiPS cube, loaded straight from IRSA. Three
problems showed up in use:

- **Speed and reliability.** Each tile was a cross-continent request to IRSA. From a laptop in Europe a single 512-px
  tile took 0.95–1.4 s (properties 0.8 s, `Allsky` 3.6 s). Under the burst Aladin sends when the view changes, IRSA
  failed many requests outright: 48 tiles fetched in parallel from the browser took 11.5–12.2 s and **18–33 of 48
  failed** (three runs); one cold field load showed no map tiles at all until the view moved.
- **The sky around a field looked fake.** Next to a field that fills the view, one HiPS pixel (12.9″, order 14) spans
  about 25 screen pixels. The map used a fixed black point (PNG cut 36), so background noise became large grey
  HEALPix diamonds; a blur of up to 12 px and a 0.62 dim turned them into dark grey clouds that read as a placeholder
  texture, not as sky (screenshot `docs/images/sky-map/field-surround-before.jpg`).
- **The zoomed-out map had the field's tools.** Transport, display mode and epoch did nothing there, and there was no
  way to choose a place on the map itself, see what SPHEREx has there, or open it.

## Decision

1. **HiPS proxy cache.** `GET /hips/{survey}/{path}` (only `spherex-dr1` today; only HiPS file names are accepted)
   serves the file from `<cache>/hips/<survey>/…`, fetching it from IRSA once (in-flight requests deduplicated, a
   mirror base via `HIPS_MIRROR_BASE` tried after IRSA, endpoints registered in `upstreams` with their breakers).
   Tiles and `Allsky` are served `Cache-Control: public, max-age=31536000, immutable` with an ETag (304 on
   revalidation); `properties` and `Moc.fits` get one hour and are refreshed after that, with the stale copy served
   when IRSA is down. A file IRSA does not have is remembered for a day (`.404` marker). The viewer uses the proxy
   when its `properties` answer within 2.5 s and IRSA directly otherwise; `VITE_HIPS_BASE` can point it at a CDN.
2. **Prewarm.** `scripts/prewarm_hips.py` downloads the six frames the viewer shows (the middle channel of D1–D6:
   frames 8, 25, 42, 59, 76, 93) at every order 0–5 plus `Allsky`, low orders first. On the dev server this is
   98,288 files, **7.0 GB**, fetched in 862 s with no failures and no missing tiles; anything else (other frames)
   fills lazily.
3. **Map mode chrome.** When the view hands over to the map (ADR-0058's threshold), the dock fades out and the field
   rail is replaced by a map rail: Go to (the same panel, but it flies the map and opens a card instead of loading at
   once), the SPHEREx channel (D1–D6), layers (exposure footprints from the local file catalogue, deep fields, RA/Dec
   grid, galactic plane, ecliptic, featured and recent places, Gaia DR3 and SIMBAD as CDS progressive catalogues),
   pick mode, back to the field, whole sky. Zooming back into the field restores dock and rail. Map layers are drawn
   only in map mode.
4. **Pick anywhere.** In map mode a click on the sky (outside the loaded field) drops a pin and a card that follows
   it: coordinates (sexagesimal; degrees and galactic in Research), and SPHEREx coverage there from
   `GET /api/coverage` (exposures, visits, date range, per detector, deep field) — milliseconds from the mirror.
   "Open SPHEREx here" builds the field at that position with the current field of view while the map flies
   towards it; "Analyze" does the same and runs the point analysis when the full field has arrived. A click on the
   loaded field flies into it and, inside the image, pins the point there, so its instant probe and Analyze are one
   click away. Places (presets at their current position, recent places, deep fields) are clickable markers.
5. **Neighbouring sky.** The map's black point follows the zoom: PNG cut 36 at 2° and wider, rising to 88 at 0.25°
   and closer, so background noise is black and stars stay; the blur is 0.32 × (HiPS pixel on screen − 2), at most
   8 px, which removes the diamond edges without smearing stars; the dim around a field-filling view is 0.3 instead
   of 0.62 (`docs/images/sky-map/field-surround-after.jpg`).
6. **Full screen** moves to its own button in the top-right corner (the F key and hold-Esc behaviour are unchanged);
   on phones, where the search bar spans the top, it stays in the rail.

## Consequences

- The map no longer depends on IRSA's load or our users' distance from Pasadena: tiles come from the server next to
  the API and are cached by browsers for a year. In the same browser test, 48 tiles through the proxy took 7.1–9.1 s
  with 0 failures (through the Vite dev proxy and HTTP/1.1 — six connections — to the dev server; production serves
  them same-origin from the API). A single tile from the laptop: 0.55 s via the proxy vs 0.95–1.1 s from IRSA.
- 7 GB of disk on each API host that serves the map, filled once by the prewarm (or lazily). The cache sits under the
  shared cache directory, so a redeploy keeps it.
- uvicorn serves tiles over HTTP/1.1; a CDN in front of `/hips` (immutable URLs make that trivial) is the next step if
  many users arrive at once.
- Gaia DR3 and SIMBAD layers are loaded by Aladin from CDS directly; they never reach our API and fail independently.
- The map's appearance near a field is a stretch choice: faint diffuse emission close to a field is cut to black.

## Alternatives considered

- **Keep loading from IRSA** — the measurements above; no control over failures.
- **Re-encode the HiPS (WebP/JPEG) or a smaller tile size** — would change pixel values and break Aladin's cube
  frame selection; the PNG tiles are 25–115 KB already.
- **Prewarm all 102 frames** — ~120 GB for channels the viewer never shows; lazy fill covers the rest.
- **Smooth the HiPS in WebGL** — Aladin samples tiles nearest-neighbour and exposes no filter option; the CSS blur
  proportional to the pixel size gives the same result.
- **Replace the field rail with a menu inside the card** — the map needs persistent tools (layers, pick mode, way
  back); a rail in the same place as the field's keeps the layout stable.
