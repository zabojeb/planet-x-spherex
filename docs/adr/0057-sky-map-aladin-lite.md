# ADR-0057: Put the field on an Aladin Lite map of the SPHEREx all-sky HiPS, with one camera shared by both

- **Status:** Accepted
- **Date:** 2026-11-15

## Context

The viewer showed one field (a few arcminutes to a degree) on an empty background. Zooming out stopped at 0.6 × the
fitted size, so a user could not see where the field lies on the sky or move from it to another part of the sky.
We want the sky map to be the main surface. The loaded field should sit on it. Zooming out should reach the whole
sky, and zooming back in should return to the field without downloading it again.

IRSA publishes the SPHEREx DR1 all-sky HiPS cube (`data/hips/SPHEREx/spherex_allsky_cube`, CORS open). It has 102
frames, one per detector channel (frame = 17 × (detector − 1) + channel − 1; frames 50 and 51 are blank). It is
HEALPix order 5 with 512 px PNG tiles, 12.88″ pixels and an asinh stretch, in which empty sky is level 35 of 255.

## Decision

- The map is Aladin Lite v3 (`aladin-lite` on npm: WebGL2 + WASM, LGPL-3.0). It loads as its own chunk once the
  viewer has mounted. [`skymap.ts`](../../web/src/skymap.ts) is the only module that imports it. It turns off all of
  Aladin's own controls and keeps the CDS logo visible.
- It shows the HiPS frame of the middle channel of the detector on screen (D1 channel 9 for All/RGB). The frame is
  set by frequency at the middle of the frame's bin, because Aladin picks a cube frame linearly in frequency. The
  cuts are 36–255. A CSS brightness/contrast pair maps Aladin's rgb(26, 26, 26) (outside the globe, and tiles not
  yet loaded) to black. A blur proportional to the HiPS pixel's size on screen hides the HEALPix diamonds.
- [`SkyView`](../../web/src/components/SkyView.tsx) has two modes on one camera:
  - **field** (default): SkyView pans and zooms as before and pushes the camera (SIN, centre, width) to the map
    every frame. The map lies under the field, dimmed by a black layer at 0.62. The dimming fades as the field
    shrinks.
  - **map**: below 0.22 × the fitted scale, Aladin takes drag and pinch. The field's canvas is placed on the map with
    a CSS `matrix()` from the projected grid corners, with an amber outline and the target's name. Above 0.3 × the
    fitted scale, with the field in view, SkyView takes the view back. The gap stops the hand-over from flickering.
  - The wheel always goes through SkyView (Aladin's `wheelTriggered`), so one gesture can cross between the modes.
    The globe button flies to a 150° view. The centre button, or a click on the field, flies back to the fitted
    field. Flights follow a great circle, rising when the target is off screen.
- Fields in the target's co-moving frame have no fixed sky position, so they get no map.

## Consequences

- The field keeps all its tools. The map adds context and a way out to the whole sky. Field planes stay in the
  client LRU, so returning to the field costs nothing. Map tiles are cached by the browser and by Aladin.
- The bundle gains a 2.4 MB chunk (842 kB gzipped), so `vite build` warns about its size. It is fetched after first
  paint and never blocks the field.
- We depend on IRSA serving the HiPS. If it is down, the map is black and the field works as before.
- Aladin's camera and ours must agree: SIN projection, fov as the view width, north up. `skymap.ts` checks
  `world2pix` for finite values, because the WASM returns typed arrays and points on the far side are not drawn.
- WebGL in headless or background tabs is throttled. Verification needs a visible tab or headless Chrome with
  SwiftShader.

## Alternatives considered

- **Our own HiPS renderer (canvas or three.js).** It would be the same work as Aladin's tile scheduler,
  projections and cube handling, with weeks of edge cases. Rejected for now. `skymap.ts` keeps the swap local.
- **Static all-sky image (Hammer map, as in Go to).** Too coarse past a few degrees, and it cannot meet the field at
  arcminute scale.
- **Aladin Lite owning the field too, as an overlay image layer.** Our field rendering (stretches, modes, blink,
  overlays, selection) would have to move into Aladin's layers. Rejected: the field stays ours and only rides on the
  map.
- **Other HiPS (DSS2, 2MASS, unWISE).** Valid, but the SPHEREx HiPS matches the data in the field and needs no
  colour explanation. A layer switch can add others later.
