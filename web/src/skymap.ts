/**
 * The sky map around the field: Aladin Lite (CDS, LGPL; its logo must stay visible) drawing the SPHEREx
 * all-sky HiPS, loaded on demand like three.js for the 3D views. The only module that touches aladin-lite.
 *
 * SkyView owns the camera while a field is on screen and pushes it here every frame; zoomed out, the map
 * owns drag and pinch. The wheel always goes to SkyView (`wheelTriggered` replaces Aladin's own zoom), so
 * one gesture can cross from the field to the map and back without changing hands mid-way.
 */

/** Camera of the map: view centre (ICRS degrees) and the width of the view in degrees. */
export interface MapCamera {
  ra: number;
  dec: number;
  fov: number;
}

export interface SkyMap {
  readonly el: HTMLDivElement;
  camera(): MapCamera;
  /** Move the view, skipping the call when nothing changed (it is made every frame). */
  setCamera(c: MapCamera): void;
  size(): [number, number];
  /** Screen position (map pixels) of a sky position, or null when it is not on the visible side. */
  world2pix(ra: number, dec: number): [number, number] | null;
  pix2world(x: number, y: number): [number, number] | null;
  /** Zoom by `factor` (> 1 zooms out) keeping the sky under (mx, my) where it is. */
  zoomAt(factor: number, mx: number, my: number): void;
  /** Drag the sky by (dx, dy) screen pixels. */
  panBy(dx: number, dy: number): void;
  /** Animate to a camera along a great circle, zooming out on the way when the target is off screen (the
   *  Google Earth hop); the duration follows the distance unless given. Returns a cancel function. */
  flyTo(to: MapCamera, ms?: number, done?: () => void): () => void;
  /** Stop Aladin's own zoom and move animations (when SkyView takes the camera back). */
  stop(): void;
  /** Show the HiPS cube frame closest to a detector ("D1"…"D6"; anything else: the default frame). */
  setBand(band: string): void;
  on(handlers: MapHandlers): void;
  destroy(): void;
}

export interface MapHandlers {
  wheel?: (e: WheelEvent) => void;
  click?: (ra: number, dec: number, dragging: boolean) => void;
  move?: (pos: [number, number] | null) => void;
}

export const MAP_MIN_FOV = 1 / 60; // 1′: deeper than this the 12.9″ HiPS is only blur
export const MAP_MAX_FOV = 180; // a hemisphere: the SIN projection's limit

/** SPHEREx DR1 all-sky HiPS cube from IRSA (CORS open): 102 frames of asinh-stretched PNG tiles, all epochs
 *  combined, frame = 17 × (detector − 1) + (channel − 1). Frames 50 and 51 (the dichroic channels) are blank,
 *  and Aladin opens a cube on its middle frame, so a frame is always chosen explicitly. */
const SPHEREX_HIPS = "https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube";
const DEFAULT_FRAME = 8; // D1 channel 9, ~0.91 µm: stars much like the 2MASS/DSS context everybody knows
/** The tiles put empty sky at 35 of 255 (and gaps at 0); cutting there makes the sky black like the viewer. */
const PNG_CUTS: [number, number] = [36, 255];
/** Size of a HiPS pixel (hips_pixel_scale 0.0035786°): zoomed in further, the map only shows its pixels. */
export const MAP_PIXEL_ARCSEC = 12.88;
const frameFor = (band: string) => {
  const m = /^D([1-6])$/.exec(band);
  if (!m) return DEFAULT_FRAME;
  return 17 * (Number(m[1]) - 1) + 8; // the middle channel of the detector
};

const D2R = Math.PI / 180;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let lib: Promise<any> | null = null;
function load() {
  lib ??= import("aladin-lite").then(async (m) => {
    const A = m.default;
    await A.init; // rejects without WebGL2: the viewer then works without a map
    return A;
  });
  return lib;
}

/** Start downloading the map code (~2.4 MB, WebAssembly inside) while the first field is still building. */
export function preloadSkyMap(): void {
  load().catch(() => undefined);
}

const toVec = (ra: number, dec: number): [number, number, number] => {
  const a = ra * D2R;
  const d = dec * D2R;
  return [Math.cos(d) * Math.cos(a), Math.cos(d) * Math.sin(a), Math.sin(d)];
};

/** Angle between two view centres, in degrees. */
function angle(a: { ra: number; dec: number }, b: { ra: number; dec: number }): number {
  const u = toVec(a.ra, a.dec);
  const v = toVec(b.ra, b.dec);
  return Math.acos(Math.min(1, Math.max(-1, u[0] * v[0] + u[1] * v[1] + u[2] * v[2]))) / D2R;
}

/** Point a fraction t of the way along the great circle from (ra0, dec0) to (ra1, dec1). */
function slerp(ra0: number, dec0: number, ra1: number, dec1: number, t: number): [number, number] {
  const a = toVec(ra0, dec0);
  const b = toVec(ra1, dec1);
  const dot = Math.min(1, Math.max(-1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]));
  const w = Math.acos(dot);
  if (w < 1e-9) return [ra1, dec1];
  const ka = Math.sin((1 - t) * w) / Math.sin(w);
  const kb = Math.sin(t * w) / Math.sin(w);
  const v = [ka * a[0] + kb * b[0], ka * a[1] + kb * b[1], ka * a[2] + kb * b[2]];
  const ra = Math.atan2(v[1], v[0]) / D2R;
  return [(ra + 360) % 360, Math.asin(Math.max(-1, Math.min(1, v[2]))) / D2R];
}

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

export async function createSkyMap(host: HTMLElement, cam: MapCamera): Promise<SkyMap> {
  const A = await load();
  const el = document.createElement("div");
  el.className = "aladin-host";
  el.style.cssText = "position: absolute; inset: 0;"; // Aladin sizes itself from this div at creation
  host.appendChild(el);
  const aladin = A.aladin(el, {
    survey: SPHEREX_HIPS,
    target: `${cam.ra} ${cam.dec}`,
    fov: cam.fov,
    cooFrame: "ICRS",
    projection: "SIN",
    backgroundColor: "rgb(0, 0, 0)",
    showReticle: false,
    showZoomControl: false,
    showLayersControl: false,
    showFullscreenControl: false,
    showFrame: false,
    showFov: false,
    showCooLocation: false,
    showProjectionControl: false,
    showStatusBar: false,
    showContextMenu: false,
    showCooGridControl: false,
    showSettingsControl: false,
    showSimbadPointerControl: false,
    showShareControl: false,
    showColorPickerControl: false,
    log: false, // no usage reports to CDS
    samp: false,
  });
  aladin.setFoVRange(MAP_MIN_FOV, MAP_MAX_FOV);

  let handlers: MapHandlers = {};
  aladin.on("wheelTriggered", (e: WheelEvent) => handlers.wheel?.(e));
  aladin.on("click", (e: { ra: number; dec: number; isDragging: boolean }) => handlers.click?.(e.ra, e.dec, !!e.isDragging));
  aladin.on("mouseMove", (e: { ra: number | null; dec: number | null }) => handlers.move?.(e.ra == null || e.dec == null ? null : [e.ra, e.dec]));

  // the wasm core answers with typed arrays, undefined off the visible side
  const finite = (v: unknown): v is [number, number] =>
    v != null && typeof v === "object" && (v as ArrayLike<number>).length >= 2 && Number.isFinite((v as ArrayLike<number>)[0]) && Number.isFinite((v as ArrayLike<number>)[1]);
  const world2pix = (ra: number, dec: number): [number, number] | null => {
    try {
      const v = aladin.world2pix(ra, dec);
      return finite(v) ? [v[0], v[1]] : null;
    } catch {
      return null;
    }
  };
  const pix2world = (x: number, y: number): [number, number] | null => {
    try {
      const v = aladin.pix2world(x, y);
      return finite(v) ? [v[0], v[1]] : null;
    } catch {
      return null;
    }
  };
  const camera = (): MapCamera => {
    const [ra, dec] = aladin.getRaDec();
    return { ra, dec, fov: aladin.getFov()[0] };
  };
  const size = (): [number, number] => {
    const [w, h] = aladin.getSize();
    return [w || el.clientWidth || 1, h || el.clientHeight || 1];
  };
  const setCamera = (c: MapCamera) => {
    const now = camera();
    const fov = Math.min(MAP_MAX_FOV, Math.max(MAP_MIN_FOV, c.fov));
    // a thousandth of a screen pixel is "the same" (the call comes every frame)
    const tol = (now.fov / size()[0]) * 1e-3;
    const dra = (((c.ra - now.ra + 540) % 360) - 180) * Math.cos(c.dec * D2R);
    if (Math.abs(dra) > tol || Math.abs(c.dec - now.dec) > tol) aladin.gotoRaDec(c.ra, c.dec);
    if (Math.abs(fov - now.fov) > now.fov * 1e-6) aladin.setFoV(fov);
  };
  // The base layer is added once its properties have loaded; until then a frame cannot be set.
  let frame = DEFAULT_FRAME;
  let shown = -1;
  let frameTimer = 0;
  const applyFrame = (tries = 0) => {
    window.clearTimeout(frameTimer);
    const layer = aladin.getBaseImageLayer?.();
    if (layer?.added && typeof layer.setFrequency === "function" && layer.emMin > 0 && layer.emMax > 0 && layer.cubeDepth > 0) {
      // Aladin picks the cube frame linearly in frequency (its setSliceNumber goes linearly in wavelength
      // instead, so the two do not round-trip): aim at the middle of frame `frame`'s frequency bin.
      const C = 299792458;
      const lo = C / layer.emMax;
      const hi = C / layer.emMin;
      if (shown < 0) layer.setCuts?.(...PNG_CUTS);
      if (shown !== frame) layer.setFrequency({ value: lo + ((frame + 0.5) / layer.cubeDepth) * (hi - lo), unit: "Hz" });
      shown = frame;
    } else if (tries < 100) frameTimer = window.setTimeout(() => applyFrame(tries + 1), 100);
  };
  applyFrame();

  let flight: number | null = null;
  const cancelFlight = () => {
    if (flight != null) cancelAnimationFrame(flight);
    flight = null;
  };

  return {
    el,
    camera,
    setCamera,
    size,
    world2pix,
    pix2world,
    zoomAt(factor, mx, my) {
      cancelFlight();
      const before = pix2world(mx, my);
      const fov = Math.min(MAP_MAX_FOV, Math.max(MAP_MIN_FOV, camera().fov * factor));
      aladin.setFoV(fov);
      if (!before) return;
      const after = world2pix(...before);
      if (!after) return;
      const [w, h] = size();
      const c = pix2world(w / 2 + after[0] - mx, h / 2 + after[1] - my);
      if (c) aladin.gotoRaDec(c[0], c[1]);
    },
    panBy(dx, dy) {
      cancelFlight();
      const [w, h] = size();
      const c = pix2world(w / 2 - dx, h / 2 - dy);
      if (c) aladin.gotoRaDec(c[0], c[1]);
    },
    flyTo(to, ms, done) {
      cancelFlight();
      aladin.stopAnimation?.();
      aladin.view?.zoom?.stopAnimation?.();
      const from = camera();
      const t0 = performance.now();
      const l0 = Math.log(from.fov);
      const l1 = Math.log(Math.min(MAP_MAX_FOV, Math.max(MAP_MIN_FOV, to.fov)));
      // A target farther than the views at either end: rise to a view that holds both, then come down
      // (a quadratic Bézier in log fov that peaks at lp halfway).
      const lp = Math.log(Math.min(MAP_MAX_FOV, Math.max(MAP_MIN_FOV, 1.6 * angle(from, to))));
      const hop = lp > Math.max(l0, l1) + 0.1;
      const ctrl = hop ? 2 * lp - (l0 + l1) / 2 : (l0 + l1) / 2; // a middle control point is a straight line
      const climb = hop ? 2 * lp - l0 - l1 : Math.abs(l1 - l0); // e-folds of zoom on the way
      const dur = ms ?? Math.min(2400, 450 + 170 * climb);
      const tick = (t: number) => {
        const k = easeInOut(Math.min(1, (t - t0) / dur));
        const [ra, dec] = slerp(from.ra, from.dec, to.ra, to.dec, k);
        aladin.gotoRaDec(ra, dec);
        aladin.setFoV(Math.exp((1 - k) ** 2 * l0 + 2 * k * (1 - k) * ctrl + k ** 2 * l1));
        if (k < 1) flight = requestAnimationFrame(tick);
        else {
          flight = null;
          done?.();
        }
      };
      flight = requestAnimationFrame(tick);
      return cancelFlight;
    },
    stop() {
      cancelFlight();
      aladin.stopAnimation?.();
      aladin.view?.zoom?.stopAnimation?.();
    },
    setBand(band) {
      frame = frameFor(band);
      applyFrame();
    },
    on(h) {
      handlers = h;
    },
    destroy() {
      cancelFlight();
      window.clearTimeout(frameTimer);
      handlers = {};
      const gl = el.querySelector("canvas")?.getContext("webgl2");
      gl?.getExtension("WEBGL_lose_context")?.loseContext();
      el.remove();
    },
  };
}
