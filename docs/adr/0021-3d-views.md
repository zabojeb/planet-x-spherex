# ADR-0021: Offer three 3D views (relief, time cube, λ cube), drawn with three.js loaded on demand

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The flat viewer answers "what is where" but makes two questions slow to read. *Does anything move?* means comparing frames by eye, and a blink only shows two frames at a time. *At which wavelengths is a source bright?* means switching detectors one by one. The data for both is already in the browser: per-epoch, per-detector co-adds on one common grid (ADR-0005, ADR-0006). Fields are small: 59 × 59 pixels by default, 293 × 293 at 30′.

Each 3D picture answers a different question, so we offer three:

| View | What it is | Answers | Best example |
|---|---|---|---|
| Relief | The frame on screen as a height field, using the same stretch and colours as the flat sky | How bright and how compact is a source; in Difference, which way it moved (peak now, pit then) | Barnard's Star, Difference |
| Time cube | Every epoch as a layer at its date (in Exposures mode: every exposure of the visit at its time); the sky is transparent, sources glow | Moves: a still star is a vertical column, a mover leans along its predicted track | Barnard's Star; asteroids in Exposures mode |
| λ cube | Every detector of the epoch as a layer, short to long wavelength | Colour: a source lights up only where it is bright | WISE 0855 (only D5–D6) |

## Decision

- 3D is a view state (`view3d`: `off`, `relief`, `time`, `bands`), separate from the mode. The relief draws whatever the mode draws, Exposures included. The cubes stack that mode's frames. In Exposures mode with one detector, the time cube stacks that epoch's single exposures at their times, so an asteroid crossing the field during a visit becomes a straight diagonal. Stack has no per-epoch frame, so the time cube shows it as Blink. RGB and Exposures have no per-detector frame, so the λ cube shows them as Blink.
- Colours come from the same `computeDisplay` and painting as the flat view, with the same stretch and percentile limits, so 3D never disagrees with 2D.
- Height is signal-to-noise above the sky (median and MAD of each frame) on a log scale: flat within 2σ, 1 at 200σ, slightly more for saturated stars (`heightField` in `render.ts`). It does not depend on the stretch, so it stays put while blinking, and a difference image's noise stays flat. A first version used the stretched values, but a difference image then became a field of spikes, because its colour limits are about 3σ. Layer opacity follows the same height, so noise is transparent and a 10σ source opaque (`sliceAlpha`).
- Orientation is the sky's: north is the far side, east is left. The predicted track and the target crosshair are drawn on top. In the time cube they thread through the epochs at their dates.
- Plane loading follows the view (`planesNeeded3d`). The time cube needs every epoch of the selection, the λ cube every detector of the current epoch, nothing more.
- Rendering uses three.js (WebGL) with OrbitControls, in its own lazily imported chunk (`Sky3D.tsx`), so the 2D viewer does not download it. It renders on demand, only while something changes.
- The view and its height are in the URL (`v3d`, `h3d`) and toggle with `v` or the rail's 3D button.

## Consequences

- One click shows a mover or a colour in a single picture, which is good for the demo and for newcomers. The flat blink stays the primary tool.
- three.js adds a lazy chunk of 129 kB gzip (511 kB minified), paid only by people who open 3D.
- The cubes fetch more planes up front than a blink does (all epochs, or all detectors). At the default field this is under 1 MB.
- Heights are signal-to-noise on a log scale, not fluxes. The hover readout and the charts stay the place for numbers.
- WebGL is required. Without it the view shows a note, and the 2D viewer is unaffected.

## Alternatives considered

- **Hand-written WebGL:** no dependency, but orbit controls, picking, text labels and resource handling would be ours to write and debug during a hackathon.
- **Plotly or another chart library's 3D surface:** heavy, styled as a chart rather than as the sky, and cannot do a stack of transparent layers.
- **Volume ray-marching of the cubes:** prettier, but much more shader work. With only 4–30 epochs or 6 detectors, discrete layers read more clearly.
- **3D as a sixth mode:** it would hide Difference and RGB in 3D, which give the most telling reliefs.
