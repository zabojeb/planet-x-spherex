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
    TopBar.tsx         serif wordmark, command search, field of view, status readout, share menu
    CommandSearch.tsx  palette search (⌘K / Ctrl+K) with the challenge targets as quick picks
    Progress.tsx       thin top progress bar, numbered section headers, landing hero with target index
    Stage.tsx          image stage: canvas, SVG overlays, reticle with lock-on, mono HUD corners, HUD loader
    Controls.tsx       toolbar (mode, detector), header actions, Display and Layers panels
    Transport.tsx      previous / play / next, epoch scrubber on a time axis, exposures strip, speed
    SidePanel.tsx      04 Data tabs: Overview · Epoch · Flags · Data
    ui/primitives.tsx  popover, segmented control, tabs, switch, micro label
    ui/BottomSheet.tsx mobile controls sheet
    chart/
      ChartFrame.tsx      reusable SVG chart: axes, grid, error bars, hollow/diamond marks, hover tooltip
      SpectrumChart.tsx   flux vs wavelength, coloured by epoch, detector bands, molecular features
      LightCurveChart.tsx flux relative to the median spectrum vs date, epoch medians
      ChartsCard.tsx      the numbered 02 Spectrum and 03 Light curve sections
```

Nothing about the instrument is hard-coded: detector ids, order and wavelength ranges come from
`meta.bands`, epochs from `meta.epochs`, flag names from `meta.flag_bits`. The false-colour channels are
the detectors split into three wavelength groups (longest = red). Targets may carry `default_band` and
`default_mode` hints from the API; they apply unless the URL sets band or mode.

## Design

Editorial HUD on black: warm off-white text, hairline frames and numbered sections (01 Viewer,
02 Spectrum, 03 Light curve, 04 Data), reticle corners and monospaced readouts around the image. Type is
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
| `ov` | overlays: `target,track,gaia,catwise,2mass,flags,compass` |
| `flags` | flag bit numbers drawn by the flags overlay |
| `ylog=0` | linear flux axis in the spectrum |
| `embed=1` | viewer and its controls only, for iframes |

Example: `/?target=barnard&mode=diff&band=D2&epoch=4` shows Barnard's Star in D2 minus the first visit:
the star leaves a red (now) and blue (then) pair, offset to the north.

## Keyboard

Space play/pause · ← → previous/next epoch · 1–6 detector · 0 all detectors · b d s r e modes.
