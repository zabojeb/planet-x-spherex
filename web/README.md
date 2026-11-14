# Web viewer

React + TypeScript single-page app (Vite, no UI framework). It asks the API to build a time sequence for a
sky position, downloads the aligned epoch co-adds as raw arrays and does all display work in the browser:
stretching, blinking, differences, stacks, false colour, overlays and charts.

## Run it

```bash
cd web
npm install
npm run dev          # http://localhost:5173, /api is proxied to the API
```

The dev server proxies `/api` to `API_URL`, read from the environment or from `../.env.deploy`
(see `../.env.example`), falling back to `http://localhost:8000`. To point at another backend:

```bash
API_URL=http://localhost:8000 npm run dev
```

`npm run build` type-checks and writes `dist/`. The FastAPI app serves `web/dist` at `/` when it exists,
so after `scripts/deploy.sh` the whole tool is one URL and the client uses relative `/api` paths.
`VITE_API_BASE` can prefix API calls if the app is hosted elsewhere.

## Structure

```
src/
  api.ts            the only module that talks to the API: fetch with retries, job polling,
                    binary array decoding (coadds/coverage/flags/exposures), catalogs
  state.ts          view state, defaults, URL parsing/serialisation and writeUrl() (the only URL writer)
  render.ts         pure pixel math, no React: percentiles, robust noise, Derived (S/N "All" image,
                    false-colour groups, stacks, differences, flag maps), colour maps, canvas painting
  display.ts        what the viewer shows for a mode (pure): limits over all epochs, reference epoch,
                    playable epochs (partial visits skipped unless asked)
  astro.ts          TAN projection onto the grid, proper-motion propagation, coordinate/date formatting
  photometry.ts     shared presentation of measurements (method, saturation, relative-only points)
  colors.ts         palette slots used by charts and overlays (validated dataviz values; never the UI accent)
  hooks.ts          element size, media query, viewport height, dismiss-on-outside-click, safe localStorage
  types.ts          API payload types (meta.json, jobs, catalogs)
  App.tsx           wiring and layout: target -> build -> derived images -> sections; playback, keys
  components/
    SkyView.tsx        full-bleed sky: drag, wheel/pinch zoom around the cursor, double-click, click to select a source
                       or drop a pin, fly-to, hairline overlays, RA/Dec grid with captions, SIMBAD name labels, hover
                       tips; HUD with the field-stepping pad, compass, scale and the coordinate readout; HUD loader
    PickCard.tsx       card for the selection (target, catalogue source or pin): ICRS/degrees/Galactic/ecliptic with
                       copy buttons, motion, distance, offset from the target, counterparts in the other catalogues,
                       measure / centre / follow, links to SIMBAD, Aladin Lite and WiseView
    GoTo.tsx           Go to panel: RA/Dec or Galactic l/b (sexagesimal or degrees, optional proper motion) with a live
                       preview, an all-sky Hammer map (Milky Way, ecliptic, deep fields, presets), recent places
    Objects.tsx        Objects in view: catalogue sources in the field, filter, sort (notable, nearest, brightest,
                       fastest), hover highlights on the sky, click flies there
    TopBar.tsx         floating top-left: serif wordmark, search pill, FOV; the share panel
    CommandSearch.tsx  ⌘K / Ctrl+K search with the challenge targets as quick picks
    Transport.tsx      floating dock (prev / play / next, epoch scrubber, date) and playback options
    Rail.tsx           right tool rail: Go to, Objects, Detector, Display, Layers, Details, Share
    Controls.tsx       mode control, detector panel, Display and Layers panels
    Drawer.tsx         closed-by-default drawer: Spectrum · Light curve · Data (lazy chunks)
    SidePanel.tsx      the Data tab: Overview · Epoch · Flags · Data
    Progress.tsx       top progress bar and the landing index over the sky
    ui/primitives.tsx  popover, segmented control, tabs, switch, micro label
    chart/             ChartFrame (reusable SVG chart), SpectrumChart, LightCurveChart, PeriodogramChart
```

Nothing about the instrument is hard-coded: detector ids, order and wavelength ranges come from
`meta.bands`, epochs from `meta.epochs`, flag names from `meta.flag_bits`. The false-colour channels are
the detectors split into three wavelength groups (longest = red). Targets may carry `default_band` and
`default_mode` hints from the API; they apply unless the URL sets band or mode.

## Loading and caching

Everything goes through `src/api.ts`; each newer endpoint falls back to the older API when it is missing.

- Every request sends `X-Client-Id` (one random id per tab), so the server counts interest per tab.
- One AbortController per load: changing target or FOV cancels polls and downloads; an unfinished job is
  withdrawn with `DELETE /api/sequences/{id}` (also on `pagehide`, via `fetch(..., {keepalive: true})`).
- Polling `GET /api/sequences/{id}` is the heartbeat: every 1 s while visible, 10 s while hidden. `202`
  shows the stage (incl. "publish"), `410` re-POSTs to restart, `500` is an error. When it turns `200` the
  big body is cancelled and the small `/lite` metadata is fetched instead (full meta if there is no `/lite`).
- `429` + `Retry-After` shows a "many users right now" countdown and retries.
- Images are fetched one `[n, n]` plane at a time (`{coadds|coverage|flags}.bin?epoch=&band=`), only for
  what is on screen: the current epoch/detector(s), the reference in Difference, coverage only for Stack,
  flags only with the flag layer on. Neighbouring epochs are fetched next; while playing, every playable
  epoch. When more than half of a cube is missing and wanted (e.g. playing All/RGB), the whole cube comes in
  one request. Decoded planes live in a 320 MB LRU keyed by sequence + array + epoch + band. The previous
  frame stays on screen while the next one downloads; stretch limits use the epochs already in memory.
- The exposure and photometry tables arrive as columns (`/columns/{table}`) only when needed: photometry
  when the drawer opens, exposures in Exposures mode or the Data tab.
- Catalogs (Gaia, CatWISE, 2MASS, SIMBAD) load only when their layer is on (browser-cached via ETag/max-age); chart code loads only when
  the drawer opens. The only prefetch is a preset's lite metadata on hover in the landing index.
- Analysis: the Period tab calls `GET .../periodogram` (or `POST /api/periodogram` for a measured source);
  "Measure spectrum & light curve" on a catalogue source calls `POST .../photometry` and shows that source
  in the drawer; Share → Download links the `export/*` files.

## Design

Google-Earth model on black: the sky fills the viewport and every control floats over it as small glass
panels (search pill, dock, tool rail, drawer); hairline reticle around the image, minimal mono HUD. Type is
self-hosted: Geist (UI), Geist Mono (data and HUD) and Instrument Serif (display), from `@fontsource`.
One amber accent (`#ffc845`) marks active and focus states only; all data colours come from the dataviz
palette in `colors.ts` (contamination flags use the status red so they cannot be confused with the
accent). Blinking is instant; a new target plays a short lock-on animation.

## URL parameters

Every view is reproducible from its URL; the app keeps the address bar in sync.

| Parameter | Meaning |
|---|---|
| `target` | preset id from `/api/targets` (`barnard`, `eps-indi-b`, `wise-0855`, `rw-aur`, `r-dra`) |
| `ra`, `dec` | position in degrees (ICRS), instead of `target` |
| `pmra`, `pmdec`, `ref_epoch` | proper motion in mas/yr (pmra includes cos dec) and the Julian year of `ra`/`dec` |
| `name` | label for a custom position |
| `fov` | field of view in arcmin (1–30) |
| `mode` | `blink` (default), `diff`, `stack`, `rgb`, `exp` |
| `band` | `D1`…`D6` or `all` (noise-weighted combination) |
| `epoch` | epoch index shown |
| `ref` | reference epoch for `diff` |
| `stack` | comma-separated epoch indices for `stack` |
| `speed` | milliseconds per frame |
| `ends=1` | blink only the first and last epoch |
| `partial=1` | include partial visits in playback |
| `stretch` | `asinh` (default), `linear`, `log` |
| `lo`, `hi` | black and white points in percent (computed over all epochs) |
| `cmap` | `gray` or `ice` |
| `zoom` | integer zoom, `0` = fit |
| `smooth=1` | smooth instead of crisp pixels |
| `ov` | overlays: `target,track,gaia,catwise,2mass,simbad,flags,grid,compass` |
| `flags` | flag bit numbers drawn by the flags overlay |
| `ylog=0` | linear flux axis in the spectrum |
| `embed=1` | viewer and its controls only, for iframes |

Example: `/?target=barnard&mode=diff&band=D2&epoch=4` shows Barnard's Star in D2 minus the first visit:
the star leaves a red (now) and blue (then) pair, offset to the north.

## Keyboard

Space play/pause · ← → previous/next epoch · 1–6 detector · 0 all detectors · b d s r e modes · g coordinate grid ·
Shift + ← ↑ → ↓ new field half a field to the east, north, west, south · Esc closes the card, then the drawer.

## Exploring

- **Where am I.** The readout in the bottom-left corner follows the cursor (or the pin, selection or target): α δ in
  sexagesimal and degrees, Galactic l b, the offset from the target and the pixel value. `g` draws an RA/Dec grid.
- **Go somewhere.** Type a name or coordinates in the search, or open Go to (rail, top button) for RA/Dec or Galactic
  input with a live preview, optional proper motion, an all-sky map to click on and recent places. The pad above the
  compass (or Shift + arrows) moves the field half its width; the centre button fits the field back in view.
- **What is this.** Click empty sky to drop a pin, or click a marker. The card gives the position in four frames,
  motion and distance, the same object in the other catalogues, and actions: measure its spectrum and light curve,
  build a field centred on it, or one that follows its proper motion. SIMBAD, Aladin Lite and WiseView open at the
  same position.
- **What is here.** Objects (rail) lists every catalogue source in the field at the shown date; turn on SIMBAD for
  names and object types. Hover a row to see it on the sky, click to fly there.
