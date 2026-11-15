/**
 * Full-bleed interactive sky (the Google Earth model): drag to pan, wheel or pinch to zoom around the
 * cursor, double-click to zoom in. Click a source or the target to select it, or empty sky to drop a pin;
 * the selection is owned by the app, which renders its card (placed here next to the object).
 * Overlays are SVG in data units on the same transformed layer as the canvas; labels and the coordinate
 * grid's captions live in screen space so text stays crisp at any zoom. Blinking repaints only the canvas.
 *
 * Around the field lies the sky map (skymap.ts, Aladin Lite), dimmed while the field fills the view. Two
 * modes share one camera: in "field" mode this component pans and zooms and the map follows; zoomed out
 * past FIELD_OUT the map takes over ("map" mode: the whole sky, drag and pinch by Aladin) and the field
 * rides on it as a small image; zoomed back in on the field, or clicked, the field takes the view back.
 */
import { Globe, LocateFixed, RotateCcw, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Progress } from "../api";
import { formatAngle, formatDec, formatRA, gridLabel, gridStep, normRa, pixelToWorld, positionAngle, propagate, separation, totalPm, worldToPixel } from "../astro";
import { CATALOG_IDS, CATALOG_STYLE, FLAG_OVERLAY } from "../colors";
import type { Display } from "../display";
import { useElementWidth, useViewportHeight } from "../hooks";
import { paintDisplay, paintFlags } from "../render";
import { createSkyMap, MAP_PIXEL_ARCSEC, preloadSkyMap, type MapCamera, type SkyMap } from "../skymap";
import { LoadChip } from "./Progress";
import type { Overlay, ViewState } from "../state";
import type { CatalogSource, Grid } from "../types";

export type Selection = { kind: "target" } | { kind: "source"; cat: string; src: CatalogSource } | { kind: "point"; ra: number; dec: number };
export type StepDir = "N" | "S" | "E" | "W";

export const sourceKey = (cat: string, src: CatalogSource) => `${cat}:${src.id}`;

/** Sky position of a selection at the shown date. */
export function selectionWorld(sel: Selection, year: number, targetWorld: [number, number] | null): [number, number] | null {
  if (sel.kind === "target") return targetWorld;
  if (sel.kind === "source") return propagate(sel.src, year);
  return [sel.ra, sel.dec];
}

/** The displayed pixel value at grid pixel (x, y), as text; null outside the image or for false colour. */
export function valueAt(d: Display | null, n: number, x: number, y: number): string | null {
  if (!d || !n) return null;
  const ix = Math.round(x);
  const iy = Math.round(y);
  if (ix < 0 || iy < 0 || ix >= n || iy >= n) return null;
  const i = iy * n + ix;
  if (d.kind === "scalar" || d.kind === "diverging") return Number.isFinite(d.img[i]) ? `${d.img[i].toPrecision(3)} ${d.unit}` : "no data";
  return null;
}

interface Props {
  grid: Grid | null;
  display: Display | null;
  view: ViewState;
  overlays: Set<Overlay>;
  flags: Uint32Array | null;
  flagMask: number;
  catalogs: Record<string, CatalogSource[]>;
  year: number;
  span: [number, number] | null;
  targetPx: [number, number] | null;
  /** What is being loaded, shown in the middle while there is nothing of it on screen yet (a search pick or a
   *  Random draw tells you what you got). */
  loading?: { name: string; note?: string | null } | null;
  /** The image on screen is of another place than the one loading: shown faded until the new one arrives. */
  dim?: boolean;
  /** What the image on screen is while the build goes on (e.g. a preview), shown in the loading chip. */
  chipNote?: string | null;
  targetWorld: [number, number] | null;
  track: [number, number][];
  trackIndex: number;
  dateLabel: string;
  progress: Progress | null;
  lockKey: string;
  error: string | null;
  onRetry: () => void;
  /** When a new search failed: go back to the last target that loaded. */
  back?: { label: string; go: () => void } | null;
  onZoom: (zoom: number) => void;
  onInteract: () => void;
  selection: Selection | null;
  onSelect: (s: Selection | null) => void;
  /** Card for the selection, placed next to it. */
  card?: ReactNode;
  /** Source highlighted from elsewhere (the objects list), as sourceKey(). */
  highlight?: string | null;
  /** Fly to this sky position when `key` changes (zooming in if the view is wide). */
  focus?: { ra: number; dec: number; key: number } | null;
  /** Move the whole field (a new build) half a field in a direction. */
  onStep?: (dir: StepDir) => void;
  shiftX?: number; // horizontal offset of the view centre (room for the landing index)
  /** Screen space taken by floating chrome (phones): the image is fitted and centred between these. */
  frame?: { top: number; bottom: number };
  /** Analysis marks (field candidates, known solar-system objects); a click calls onMark with the key. */
  marks?: SkyMark[];
  onMark?: (key: string) => void;
  /** Measuring tools: a distance/PA ruler (click two points) and a pixel magnifier under the cursor. */
  ruler?: boolean;
  magnifier?: boolean;
  invert?: boolean;
  /** Name of the field, written next to it on the zoomed-out map. */
  label?: string | null;
  /** Draw the sky map around the field (default on). Never for co-moving grids, which follow an object. */
  map?: boolean;
}

/** A mark drawn over the sky: grid pixel position, a shape per kind, optional label and motion arrow. */
export interface SkyMark {
  key: string;
  kind: "mover" | "tracklet" | "variable" | "red" | "anomaly" | "known" | "stack";
  x: number;
  y: number;
  label?: string;
  /** End of a motion arrow (grid px), e.g. where a known object is an hour later. */
  to?: [number, number];
  /** Further points of a track (tracklet detections), drawn as a thin line through the mark. */
  path?: [number, number][];
  active?: boolean;
  title?: string;
}

export const HIGH_PM = 100; // mas/yr
const MAX_MARKERS = 160;
const MAX_LABELS = 28;

/** Zoomed out below FIELD_OUT × the fitted scale, the map takes the view; zoomed back in on the field past
 *  FIELD_IN × (a gap, so the hand-over does not flicker), the field takes it back. */
const FIELD_OUT = 0.22;
const FIELD_IN = 0.3;
/** How dark the map stays around a field that fills the view (0 = not dimmed); it brightens on zooming out. */
const MAP_DIM = 0.62;
/** Width of the view, in degrees, for "zoom out to the whole sky": nearly a hemisphere. */
const SKY_FOV = 150;
const MAX_SCALE = 48; // screen pixels per image pixel

export type Marker = { cat: string; src: CatalogSource; x: number; y: number; from?: [number, number]; to?: [number, number]; pm: number };

/** Catalogue sources of the shown overlays that fall on the grid, at the shown date (grid pixels). Fast movers
 *  also get where they were at the start and end of the sequence (`span`, Julian years). Shared by the 3D views. */
export function catalogMarkers(grid: Grid, catalogs: Record<string, CatalogSource[]>, overlays: Set<Overlay>, year: number, span: [number, number] | null): Marker[] {
  const n = grid.n;
  const out: Marker[] = [];
  for (const cat of CATALOG_IDS) {
    if (!overlays.has(cat)) continue;
    let kept = 0;
    for (const src of catalogs[cat] ?? []) {
      const [ra, dec] = propagate(src, year);
      const [x, y] = worldToPixel(grid, ra, dec);
      if (x < -2 || y < -2 || x > n + 1 || y > n + 1) continue;
      const pm = totalPm(src);
      // SIMBAD rows are few and come most-studied first: keep them all; cap the dense catalogues
      if (cat !== "simbad" && pm <= HIGH_PM && kept >= MAX_MARKERS) continue;
      kept++;
      const m: Marker = { cat, src, x, y, pm };
      if (pm > HIGH_PM && span) {
        m.from = worldToPixel(grid, ...propagate(src, span[0]));
        m.to = worldToPixel(grid, ...propagate(src, span[1]));
      }
      out.push(m);
    }
  }
  return out;
}

/** Hover text for a source: its name, then its object type (SIMBAD) or catalogue, and a magnitude. */
export function markerTip(cat: string, src: CatalogSource): { name: string; sub: string } {
  const kind = cat === "simbad" ? (src.type as string | null) ?? "SIMBAD" : CATALOG_STYLE[cat].label;
  return { name: String(src.id), sub: `${kind}${src.mag != null ? ` · ${src.mag_band} ${src.mag.toFixed(1)}` : ""}` };
}

function hexRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

/** The shortest round length that is at least 44 px long, at `pxPerDeg` screen pixels per degree. */
function scaleBar(pxPerDeg: number): { px: number; label: string } {
  const steps = [0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1200]; // arcminutes
  const arcmin = steps.find((a) => (a / 60) * pxPerDeg >= 44) ?? steps[steps.length - 1];
  const label = arcmin < 1 ? `${arcmin * 60}″` : arcmin < 60 ? `${arcmin}′` : `${arcmin / 60}°`;
  return { px: (arcmin / 60) * pxPerDeg, label };
}

const ease = (t: number) => 1 - (1 - t) ** 3;
const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();

export function SkyView(p: Props) {
  const [boxRef, W] = useElementWidth<HTMLDivElement>();
  const H = useViewportHeight();
  const imgRef = useRef<HTMLCanvasElement>(null);
  const flagRef = useRef<HTMLCanvasElement>(null);
  const n = p.grid?.n ?? 0;
  const frameTop = p.frame?.top ?? 0;
  const frameH = H - frameTop - (p.frame?.bottom ?? 0);
  const cy = frameTop + frameH / 2;
  const fit = n && W ? (p.frame ? Math.min(0.94 * W, frameH) : 0.8 * Math.min(W, H)) / n : 1;
  const [scale, setScale] = useState(0); // 0 = follow fit
  const [center, setCenter] = useState<[number, number] | null>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [hover, setHover] = useState<Marker | null>(null);
  const [ruler, setRuler] = useState<{ a: [number, number]; b: [number, number] | null } | null>(null);
  const loupeRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (!p.ruler) setRuler(null);
  }, [p.ruler, p.lockKey]);
  const s = scale || (p.view.zoom ? p.view.zoom : fit);
  const c = center ?? [n / 2, n / 2];
  const shiftX = p.shiftX ?? 0;
  const tx = W / 2 + shiftX - c[0] * s;
  const ty = cy - c[1] * s;
  const u = 1 / s;

  // ---------------------------------------------------------------- sky map (see the header comment)
  const mapHost = useRef<HTMLDivElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);
  const outlineRef = useRef<SVGPolygonElement>(null);
  const ringRef = useRef<SVGCircleElement>(null);
  const nameRef = useRef<SVGTextElement>(null);
  const [skyMap, setSkyMap] = useState<SkyMap | null>(null);
  const [mode, setMode] = useState<"field" | "map">("field");
  const modeRef = useRef(mode); // switched at once by the handlers, before React re-renders
  modeRef.current = mode;
  const [mapFov, setMapFov] = useState(0); // map mode: width of the view in degrees (scale bar)
  const mapFovRef = useRef(0);
  const [mapCursor, setMapCursor] = useState<[number, number] | null>(null);
  const wantMap = p.map !== false;
  const mapOn = !!skyMap && !!p.grid && p.grid.frame !== "comoving" && wantMap;
  const isMap = mapOn && mode === "map";
  const wantCam = useRef<MapCamera | null>(null); // field mode: the map camera matching the field view
  const flight = useRef<(() => void) | null>(null); // cancels the map animation in progress
  const mapPointers = useRef(new Set<number>()); // buttons or fingers down on the map itself
  const layerMatrix = useRef(""); // map mode: the image layer's transform, kept up to date every frame
  const outlinePts = useRef("");
  const [armed, setArmed] = useState(false); // the map is made once the first field is known

  /** Map camera showing what the field view (scale sv, layer origin at screen txv, tyv) shows. */
  function camFor(sv: number, txv: number, tyv: number): MapCamera | null {
    if (!p.grid || !sv) return null;
    const [mw, mh] = skyMap ? skyMap.size() : [W, H];
    const [ra, dec] = pixelToWorld(p.grid, (mw / 2 - txv) / sv - 0.5, n - (mh / 2 - tyv) / sv - 0.5);
    return { ra, dec, fov: (mw * p.grid.pixscale_arcsec) / 3600 / sv };
  }

  useEffect(() => {
    if (wantMap) preloadSkyMap();
  }, [wantMap]);
  useEffect(() => {
    if (p.grid && W) setArmed(true);
  }, [p.grid, W]);
  useEffect(() => {
    const host = mapHost.current;
    if (!wantMap || !armed || !host) return;
    let alive = true;
    let made: SkyMap | null = null;
    createSkyMap(host, wantCam.current ?? camFor(s, tx, ty) ?? { ra: 0, dec: 0, fov: 60 })
      .then((m) => {
        if (!alive) return m.destroy();
        made = m;
        setSkyMap(m);
      })
      .catch(() => undefined); // no WebGL2, or the code failed to load: the field works on its own
    return () => {
      alive = false;
      made?.destroy();
      setSkyMap(null);
    };
  }, [wantMap, armed]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => skyMap?.setBand(p.view.band), [skyMap, p.view.band]);

  // Field mode: the map follows the field's camera (here, before paint, and again every frame below so
  // nothing Aladin animates on its own can drift).
  useLayoutEffect(() => {
    if (!skyMap || !mapOn || mode !== "field") {
      wantCam.current = null;
      return;
    }
    wantCam.current = camFor(s, tx, ty);
    if (wantCam.current) skyMap.setCamera(wantCam.current);
  });

  function cancelFlight() {
    flight.current?.();
    flight.current = null;
  }

  /** Hand the view to the map, from a field view at scale sv with the layer origin at (txv, tyv). */
  function enterMap(sv: number, txv: number, tyv: number) {
    if (!skyMap) return;
    stopAnim();
    window.clearTimeout(zoomTimer.current); // the URL keeps the last field zoom
    const cam = camFor(sv, txv, tyv);
    if (cam) skyMap.setCamera(cam);
    layerMatrix.current = `matrix(${sv}, 0, 0, ${sv}, ${txv}, ${tyv})`;
    const e = n * sv;
    outlinePts.current = `${txv},${tyv} ${txv + e},${tyv} ${txv + e},${tyv + e} ${txv},${tyv + e}`;
    mapFovRef.current = cam?.fov ?? 0;
    setMapFov(mapFovRef.current);
    wantCam.current = null;
    modeRef.current = "map";
    setMode("map");
    setHover(null);
    setCursor(null);
  }

  /** Hand the view back to the field: the layer is drawn at scale sv with its origin at (ox, oy). */
  function enterField(sv: number, ox: number, oy: number) {
    skyMap?.stop();
    cancelFlight();
    modeRef.current = "field";
    if (layerRef.current) layerRef.current.style.visibility = "";
    const sc = Math.min(sv, MAX_SCALE);
    setScale(sc);
    setCenter([(W / 2 + shiftX - ox) / sv, (cy - oy) / sv]); // the layer point now at the view centre
    setMode("field");
    setMapCursor(null);
    window.clearTimeout(zoomTimer.current);
    zoomTimer.current = window.setTimeout(() => p.onZoom(Math.round(sc * 10) / 10), 300);
  }

  /** Fly from the map back into the field, ending with the field fitted in view. */
  function flyToField() {
    if (!skyMap || !p.grid) return;
    const to = camFor(fit, W / 2 + shiftX - (n / 2) * fit, cy - (n / 2) * fit);
    if (!to) return;
    cancelFlight();
    flight.current = skyMap.flyTo(to, undefined, () => {
      flight.current = null;
      modeRef.current = "field";
      if (layerRef.current) layerRef.current.style.visibility = "";
      setCenter(null);
      setScale(0);
      setMode("field");
      setMapCursor(null);
      p.onZoom(0);
    });
    p.onInteract();
  }

  /** Zoom out to (nearly) a hemisphere around the field. */
  function zoomToSky() {
    if (!skyMap || !p.grid) return;
    if (modeRef.current === "field") enterMap(s, tx, ty);
    cancelFlight();
    flight.current = skyMap.flyTo({ ra: p.grid.ra, dec: p.grid.dec, fov: SKY_FOV }, undefined, () => (flight.current = null));
    p.onInteract();
  }

  /** Map mode, every frame: put the image where the map shows its patch of sky, outline and name it, and
   *  give the view back to the field once it is zoomed in on it. */
  function placeOnMap() {
    if (!skyMap || !p.grid) return;
    const g = p.grid;
    const at = (lx: number, ly: number) => skyMap.world2pix(...pixelToWorld(g, lx - 0.5, n - ly - 0.5));
    const a = at(0, 0);
    const b = at(n, 0);
    const d = at(0, n);
    const e = at(n, n);
    const fov = skyMap.camera().fov;
    if (Math.abs(fov - mapFovRef.current) > 0.01 * fov) {
      mapFovRef.current = fov;
      setMapFov(fov);
    }
    const layer = layerRef.current;
    const hide = !a || !b || !d || !e; // behind the visible hemisphere
    if (layer) layer.style.visibility = hide ? "hidden" : "";
    for (const el of [outlineRef.current, ringRef.current, nameRef.current]) el?.setAttribute("visibility", hide ? "hidden" : "visible");
    if (!a || !b || !d || !e) return;
    const m = [(b[0] - a[0]) / n, (b[1] - a[1]) / n, (d[0] - a[0]) / n, (d[1] - a[1]) / n, a[0], a[1]];
    layerMatrix.current = `matrix(${m.join(", ")})`;
    if (layer) layer.style.transform = layerMatrix.current;
    outlinePts.current = [a, b, e, d].map((q) => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(" ");
    outlineRef.current?.setAttribute("points", outlinePts.current);
    const sm = Math.hypot(m[0], m[1]); // screen pixels per image pixel
    const mx = (a[0] + e[0]) / 2;
    const my = (a[1] + e[1]) / 2;
    const tiny = sm * n < 14;
    ringRef.current?.setAttribute("cx", mx.toFixed(1));
    ringRef.current?.setAttribute("cy", my.toFixed(1));
    if (tiny) ringRef.current?.setAttribute("visibility", "visible");
    else ringRef.current?.setAttribute("visibility", "hidden");
    nameRef.current?.setAttribute("x", mx.toFixed(1));
    nameRef.current?.setAttribute("y", (tiny ? my + 20 : Math.max(a[1], b[1], d[1], e[1]) + 16).toFixed(1));
    if (!flight.current && mapPointers.current.size === 0 && sm >= fit * FIELD_IN && mx > 0 && mx < W && my > 0 && my < H) enterField(sm, a[0], a[1]);
  }

  /** One wheel step at (mx, my), whichever element it reached: the field zooms, or the map does. */
  function wheelAt(dy: number, mx: number, my: number) {
    if (!n || modeRef.current !== mode) return; // a hand-over is still being rendered
    const f = Math.exp(-dy * 0.0016);
    if (isMap && skyMap) {
      cancelFlight();
      skyMap.zoomAt(1 / f, mx, my);
      p.onInteract();
      return;
    }
    zoomAt(s * f, mx, my);
  }

  // The frame loop and the map's callbacks run with the latest render's closures.
  const live = useRef({ frame: () => {}, wheel: (_e: WheelEvent) => {}, click: (_ra: number, _dec: number, _drag: boolean) => {}, move: (_pos: [number, number] | null) => {} });
  live.current.frame = () => {
    if (!skyMap || !mapOn) return;
    if (modeRef.current === "map") placeOnMap();
    else if (wantCam.current) skyMap.setCamera(wantCam.current);
  };
  live.current.wheel = (e) => {
    const [mx, my] = local(e);
    wheelAt(e.deltaY, mx, my);
  };
  live.current.click = (ra, dec, dragging) => {
    if (dragging || modeRef.current !== "map" || !p.grid || !skyMap) return;
    const half = (n * p.grid.pixscale_arcsec) / 3600 / Math.SQRT2; // half the field's diagonal
    const px = skyMap.size()[0] / skyMap.camera().fov; // screen pixels per degree
    if (separation(p.grid.ra, p.grid.dec, ra, dec) <= Math.max(half, 16 / px)) flyToField();
  };
  live.current.move = (pos) => setMapCursor(modeRef.current === "map" ? pos : null);
  useEffect(() => {
    if (!skyMap) return;
    skyMap.on({
      wheel: (e) => live.current.wheel(e),
      click: (ra, dec, drag) => live.current.click(ra, dec, drag),
      move: (pos) => live.current.move(pos),
    });
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      live.current.frame();
    };
    raf = requestAnimationFrame(tick);
    // a button released anywhere ends a gesture on the map (Aladin keeps some events to itself)
    const up = (e: PointerEvent) => mapPointers.current.delete(e.pointerId);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", up, true);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", up, true);
    };
  }, [skyMap]);

  // new sequence: recentre on the field, keep the URL zoom
  useLayoutEffect(() => {
    setCenter(null);
    setScale(0);
    setHover(null);
    cancelFlight();
    modeRef.current = "field";
    setMode("field");
    if (layerRef.current) layerRef.current.style.visibility = "";
  }, [p.lockKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // external zoom changes (Display panel, URL)
  useEffect(() => setScale(0), [p.view.zoom]);

  useEffect(() => {
    const cv = imgRef.current;
    const d = p.display;
    if (!cv || !n || !d || d.kind === "empty") return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const out = ctx.createImageData(n, n);
    paintDisplay(d, n, p.view.stretch, p.view.cmap, out);
    ctx.putImageData(out, 0, 0);
  }, [p.display, p.view.stretch, p.view.cmap, n]);

  const showFlags = p.overlays.has("flags") && !!p.flags && p.flagMask !== 0;
  useEffect(() => {
    const cv = flagRef.current;
    if (!cv || !n || !showFlags || !p.flags) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const out = ctx.createImageData(n, n);
    paintFlags(p.flags, n, p.flagMask, out, hexRgb(FLAG_OVERLAY));
    ctx.putImageData(out, 0, 0);
  }, [p.flags, p.flagMask, n, showFlags]);

  // magnifier: 11×11 image pixels around the cursor, drawn 12× without smoothing
  useEffect(() => {
    const cv = loupeRef.current;
    const img = imgRef.current;
    if (!p.magnifier || !cv || !img || !cursor || !n) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const half = 5.5;
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.drawImage(img, cursor.x + 0.5 - half, n - cursor.y - 0.5 - half, 2 * half, 2 * half, 0, 0, cv.width, cv.height);
  });

  // grid pixel (x right, y up) -> layer coordinates (y down), pixel centres
  const toL = (x: number, y: number): [number, number] => [x + 0.5, n - y - 0.5];
  const toScreen = (x: number, y: number): [number, number] => {
    const [lx, ly] = toL(x, y);
    return [tx + lx * s, ty + ly * s];
  };
  const fromScreen = (sx: number, sy: number): [number, number] => [(sx - tx) / s - 0.5, n - (sy - ty) / s - 0.5];

  const markers = useMemo(
    () => (p.grid ? catalogMarkers(p.grid, p.catalogs, p.overlays, p.year, p.span) : []),
    [p.grid, p.catalogs, p.overlays, p.year, p.span],
  );

  // ---------------------------------------------------------------- fly-to
  const anim = useRef<number>();
  const stopAnim = () => {
    if (anim.current) cancelAnimationFrame(anim.current);
  };
  useEffect(() => {
    const f = p.focus;
    if (!f || !p.grid || !W) return;
    if (modeRef.current === "map") {
      // an object picked from the list while the map is shown: back to the field first
      skyMap?.stop();
      cancelFlight();
      modeRef.current = "field";
      if (layerRef.current) layerRef.current.style.visibility = "";
      setMode("field");
    }
    const [x, y] = worldToPixel(p.grid, f.ra, f.dec);
    const [lx, ly] = toL(x, y);
    const s0 = s;
    const s1 = s0 < fit * 1.8 ? Math.min(fit * 2.5, 48) : s0;
    const c0 = c;
    const c1: [number, number] = [lx, ly];
    const t0 = performance.now();
    stopAnim();
    const tick = (t: number) => {
      const k = ease(Math.min(1, (t - t0) / 520));
      const sk = s0 + (s1 - s0) * k;
      setScale(sk);
      setCenter([c0[0] + (c1[0] - c0[0]) * k, c0[1] + (c1[1] - c0[1]) * k]);
      if (k < 1) anim.current = requestAnimationFrame(tick);
      else p.onZoom(Math.round(s1 * 10) / 10);
    };
    anim.current = requestAnimationFrame(tick);
    return stopAnim;
  }, [p.focus?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------- interaction
  const drag = useRef<{ x: number; y: number; c: [number, number]; moved: boolean } | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ d: number; s: number; last: number } | null>(null);
  const zoomTimer = useRef<number>();
  // without a map the field cannot be zoomed out much; with one, zooming out past FIELD_OUT shows the map
  const clampS = (v: number) => Math.min(Math.max(v, mapOn ? 1e-6 : fit * 0.6), MAX_SCALE);

  function zoomAt(newS: number, mx: number, my: number) {
    stopAnim();
    const ns = clampS(newS);
    const px = (mx - tx) / s; // layer coords under the cursor
    const py = (my - ty) / s;
    if (mapOn && ns < fit * FIELD_OUT) {
      enterMap(ns, mx - px * ns, my - py * ns);
      p.onInteract();
      return;
    }
    setScale(ns);
    setCenter([(W / 2 + shiftX - (mx - px * ns)) / ns, (cy - (my - py * ns)) / ns]);
    window.clearTimeout(zoomTimer.current);
    zoomTimer.current = window.setTimeout(() => p.onZoom(Math.abs(ns - fit) < 0.05 ? 0 : Math.round(ns * 10) / 10), 300);
    p.onInteract();
  }

  function resetView() {
    if (isMap) return flyToField();
    stopAnim();
    setCenter(null);
    setScale(0);
    p.onZoom(0);
  }

  function local(e: { clientX: number; clientY: number }): [number, number] {
    const b = boxRef.current!.getBoundingClientRect();
    return [e.clientX - b.left, e.clientY - b.top];
  }

  /** The marker (or the target) nearest to a screen point, within `radius` pixels. */
  function pickAt(mx: number, my: number, radius: number): Selection | null {
    let best: Selection | null = null;
    let bd = radius * radius;
    if (p.targetPx) {
      const [sx, sy] = toScreen(...p.targetPx);
      const d = (sx - mx) ** 2 + (sy - my) ** 2;
      if (d < 18 * 18) {
        best = { kind: "target" };
        bd = Math.min(d, bd);
      }
    }
    for (const m of markers) {
      const [sx, sy] = toScreen(m.x, m.y);
      const d = (sx - mx) ** 2 + (sy - my) ** 2;
      if (d < bd) {
        bd = d;
        best = { kind: "source", cat: m.cat, src: m.src };
      }
    }
    return best;
  }

  function onWheel(e: React.WheelEvent) {
    // wheel steps on the map itself arrive through the map's handler (it stops their propagation)
    const [mx, my] = local(e);
    wheelAt(e.deltaY, mx, my);
  }

  const onMapEl = (e: { target: EventTarget | null }) => !!mapHost.current && mapHost.current.contains(e.target as Node);

  function onPointerDown(e: React.PointerEvent) {
    if (!n) return;
    if (isMap) {
      // the map pans and pinches itself; a touch on it cancels a flight and holds back the hand-over
      if (onMapEl(e)) {
        mapPointers.current.add(e.pointerId);
        cancelFlight();
        p.onInteract();
      }
      return;
    }
    stopAnim();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const [x, y] = local(e);
    pointers.current.set(e.pointerId, { x, y });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      pinch.current = { d, s, last: d };
      drag.current = null;
    } else drag.current = { x, y, c, moved: false };
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!n) return;
    const [x, y] = local(e);
    if (isMap) {
      // A drag or pinch that began on the field and zoomed out onto the map stays captured here: it
      // moves the map from now on. Pointers that went down on the map are the map's own.
      const prev = pointers.current.get(e.pointerId);
      if (!prev || !skyMap) return;
      pointers.current.set(e.pointerId, { x, y });
      if (pinch.current && pointers.current.size === 2) {
        const [a, b] = [...pointers.current.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        skyMap.zoomAt(pinch.current.last / d, (a.x + b.x) / 2, (a.y + b.y) / 2);
        pinch.current.last = d;
      } else if (pointers.current.size === 1) skyMap.panBy(x - prev.x, y - prev.y);
      return;
    }
    if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, { x, y });
    if (pinch.current && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      pinch.current.last = d;
      zoomAt(pinch.current.s * (d / pinch.current.d), (a.x + b.x) / 2, (a.y + b.y) / 2);
      return;
    }
    const dr = drag.current;
    if (dr) {
      const dx = x - dr.x;
      const dy = y - dr.y;
      if (Math.hypot(dx, dy) > 4) dr.moved = true;
      if (dr.moved) {
        setCenter([dr.c[0] - dx / s, dr.c[1] - dy / s]);
        setHover(null);
        p.onInteract();
      }
    } else if (e.pointerType === "mouse") {
      const hit = pickAt(x, y, 10);
      setHover(hit?.kind === "source" ? markers.find((m) => m.src === hit.src) ?? null : null);
    }
    // cursor in grid coordinates
    const [gx, gy] = fromScreen(x, y);
    setCursor({ x: gx, y: gy });
  }

  function onPointerUp(e: React.PointerEvent) {
    pointers.current.delete(e.pointerId);
    mapPointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    const dr = drag.current;
    drag.current = null;
    if (isMap || !dr || dr.moved || !n || !p.grid) return; // on the map a click is the map's (see live.click)
    // click: select the nearest marker or the target; on empty sky drop a pin, or close an open card
    const [mx, my] = local(e);
    if (p.ruler) {
      const g = fromScreen(mx, my);
      setRuler((r) => (!r || r.b ? { a: g, b: null } : { a: r.a, b: g }));
      return;
    }
    if (p.marks?.length && p.onMark) {
      let best: SkyMark | null = null;
      let bd = (e.pointerType === "mouse" ? 12 : 22) ** 2;
      for (const m of p.marks) {
        const [sx, sy] = toScreen(m.x, m.y);
        const d = (sx - mx) ** 2 + (sy - my) ** 2;
        if (d < bd) {
          bd = d;
          best = m;
        }
      }
      if (best) return p.onMark(best.key);
    }
    const hit = pickAt(mx, my, e.pointerType === "mouse" ? 12 : 22);
    if (hit) return p.onSelect(hit);
    if (p.selection) return p.onSelect(null);
    const [gx, gy] = fromScreen(mx, my);
    if (gx < -0.5 || gy < -0.5 || gx > n - 0.5 || gy > n - 0.5) return;
    const [ra, dec] = pixelToWorld(p.grid, gx, gy);
    p.onSelect({ kind: "point", ra, dec });
  }

  function onDoubleClick(e: React.MouseEvent) {
    if (isMap) {
      // Aladin has just centred the view on the double-clicked point: zoom in on the centre
      if (skyMap && onMapEl(e)) {
        const [mw, mh] = skyMap.size();
        skyMap.zoomAt(0.5, mw / 2, mh / 2);
      }
      return;
    }
    const [mx, my] = local(e);
    zoomAt(s * 2, mx, my);
  }

  // ---------------------------------------------------------------- coordinate grid
  const gridLines = useMemo(() => {
    const g = p.grid;
    if (!g || !n || !W || !p.overlays.has("grid")) return null;
    // the visible part of the image, in grid pixels
    const x0 = Math.max(-0.5, fromScreen(0, 0)[0]);
    const x1 = Math.min(n - 0.5, fromScreen(W, 0)[0]);
    const y0 = Math.max(-0.5, fromScreen(0, H)[1]);
    const y1 = Math.min(n - 0.5, fromScreen(0, 0)[1]);
    if (x1 <= x0 || y1 <= y0) return null;
    const ras: number[] = [];
    const decs: number[] = [];
    for (let i = 0; i <= 8; i++) {
      for (const [x, y] of [[x0 + ((x1 - x0) * i) / 8, y0], [x0 + ((x1 - x0) * i) / 8, y1], [x0, y0 + ((y1 - y0) * i) / 8], [x1, y0 + ((y1 - y0) * i) / 8]]) {
        const [ra, dec] = pixelToWorld(g, x, y);
        ras.push(((ra - g.ra + 540) % 360) - 180);
        decs.push(dec);
      }
    }
    let raMin = Math.min(...ras);
    let raMax = Math.max(...ras);
    const decMin = Math.min(...decs);
    const decMax = Math.max(...decs);
    // a celestial pole in view: meridians fan out over all hours
    const pole = [90, -90].some((pd) => {
      const [px, py] = worldToPixel(g, 0, pd);
      return px >= x0 && px <= x1 && py >= y0 && py <= y1;
    });
    if (pole) [raMin, raMax] = [-180, 180];
    const cosd = Math.max(0.05, Math.cos((g.dec * Math.PI) / 180));
    const decStep = gridStep(decMax - decMin, "dec");
    const raStep = gridStep((raMax - raMin) * cosd, "ra"); // seconds of time
    const raStepDeg = raStep / 240;
    const decStepDeg = decStep / 3600;
    const lines: { kind: "ra" | "dec"; value: number; pts: [number, number][] }[] = [];
    const SAMPLES = 24;
    const pad = 0.1 * (decMax - decMin);
    for (let k = Math.ceil(decMin / decStepDeg); k * decStepDeg <= decMax; k++) {
      const dec = k * decStepDeg;
      const pts: [number, number][] = [];
      for (let i = 0; i <= SAMPLES; i++) pts.push(worldToPixel(g, g.ra + raMin - pad + ((raMax - raMin + 2 * pad) * i) / SAMPLES, dec));
      lines.push({ kind: "dec", value: dec, pts });
      if (lines.length > 40) break;
    }
    const raAbs0 = g.ra + raMin;
    for (let k = Math.ceil(raAbs0 / raStepDeg); k * raStepDeg <= g.ra + raMax; k++) {
      const ra = k * raStepDeg;
      const pts: [number, number][] = [];
      for (let i = 0; i <= SAMPLES; i++) pts.push(worldToPixel(g, ra, Math.max(-90, Math.min(90, decMin - pad + ((decMax - decMin + 2 * pad) * i) / SAMPLES))));
      lines.push({ kind: "ra", value: normRa(ra), pts });
      if (lines.length > 80) break;
    }
    return { lines, raStep, decStep };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.grid, p.overlays, n, W, H, tx, ty, s]);

  // grid captions: where each line leaves the visible image through the left (Dec) or bottom (RA) edge
  const gridCaptions = useMemo(() => {
    if (!gridLines || !n) return [];
    const left = Math.max(tx, 12);
    const bottom = Math.min(ty + n * s, H - 132);
    const top = Math.max(ty, 12);
    const right = Math.min(tx + n * s, W - 80);
    const out: { x: number; y: number; text: string; anchor: "start" | "end" }[] = [];
    for (const line of gridLines.lines) {
      const sp = line.pts.map(([x, y]) => toScreen(x, y));
      for (let i = 1; i < sp.length; i++) {
        const [ax, ay] = sp[i - 1];
        const [bx, by] = sp[i];
        if (line.kind === "dec" && (ax - left) * (bx - left) <= 0 && ax !== bx) {
          const y = ay + ((left - ax) / (bx - ax)) * (by - ay);
          if (y > top + 10 && y < bottom - 4) out.push({ x: left + 5, y: y - 4, text: gridLabel(line.value, "dec", gridLines.decStep), anchor: "start" });
          break;
        }
        if (line.kind === "ra" && (ay - bottom) * (by - bottom) <= 0 && ay !== by) {
          const x = ax + ((bottom - ay) / (by - ay)) * (bx - ax);
          if (x > left + 70 && x < right - 4) out.push({ x: x - 4, y: bottom - 6, text: gridLabel(line.value, "ra", gridLines.raStep), anchor: "end" });
          break;
        }
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridLines, tx, ty, s, W, H, n]);

  // SIMBAD names next to their markers: most-studied first, never overlapping
  const labels = useMemo(() => {
    if (!p.overlays.has("simbad") || !n) return [];
    const boxes: [number, number, number, number][] = [];
    const out: { x: number; y: number; text: string; key: string }[] = [];
    const named = markers.filter((m) => m.cat === "simbad").sort((a, b) => Number(b.src.refs ?? 0) - Number(a.src.refs ?? 0));
    for (const m of named) {
      if (out.length >= MAX_LABELS) break;
      const [sx, sy] = toScreen(m.x, m.y);
      if (sx < 0 || sy < 0 || sx > W || sy > H) continue;
      const text = String(m.src.id);
      const w = Math.min(text.length, 28) * 6.4 + 6;
      const box: [number, number, number, number] = [sx + 9, sy - 15, sx + 9 + w, sy - 1];
      if (boxes.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
      boxes.push(box);
      out.push({ x: sx + 10, y: sy - 5, text: text.length > 28 ? `${text.slice(0, 27)}…` : text, key: sourceKey(m.cat, m.src) });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markers, p.overlays, tx, ty, s, W, H, n]);

  // ---------------------------------------------------------------- render
  const d = p.display;
  const hasImage = n > 0 && d && d.kind !== "empty";
  // on a wide map the compass and the scale bar would hold only near the centre: dropped past 30°
  const bar = !p.grid ? null : isMap ? (mapFov && mapFov <= 30 ? scaleBar(W / mapFov) : null) : scaleBar((s * 3600) / p.grid.pixscale_arcsec);
  const cw = !isMap && cursor && p.grid && cursor.x >= -0.5 && cursor.y >= -0.5 && cursor.x < n - 0.5 && cursor.y < n - 0.5 ? cursor : null;
  const world = isMap ? mapCursor : cw && p.grid ? pixelToWorld(p.grid, cw.x, cw.y) : null;
  const value = cw ? valueAt(d, n, cw.x, cw.y) : null;
  // the map around the field: dark while the field fills the view, brightening as it is zoomed out
  const dim = !mapOn || isMap ? 0 : MAP_DIM * Math.min(1, Math.max(0, Math.log(s / (fit * FIELD_OUT)) / Math.log(0.8 / FIELD_OUT)));
  // Zoomed in past the map's resolution its HEALPix pixels show as diamonds: soften them in proportion.
  const mapPx = !p.grid ? 0 : isMap ? (mapFov ? (MAP_PIXEL_ARCSEC * W) / (mapFov * 3600) : 0) : (MAP_PIXEL_ARCSEC * s) / p.grid.pixscale_arcsec;
  const mapBlur = Math.min(12, Math.max(0, 0.4 * (mapPx - 2.5)));
  const L = 14 * u; // reticle arm length in layer units
  const selWorld = p.selection ? selectionWorld(p.selection, p.year, p.targetWorld) : null;
  const selPx: [number, number] | null =
    p.selection?.kind === "target" ? p.targetPx : selWorld && p.grid ? worldToPixel(p.grid, ...selWorld) : null;
  const selPos = selPx ? toScreen(...selPx) : null;
  const hl = p.highlight ? markers.find((m) => sourceKey(m.cat, m.src) === p.highlight) : null;
  const readout = world ? { label: "Cursor", pos: world } : selWorld ? { label: p.selection?.kind === "point" ? "Pin" : "Selected", pos: selWorld } : p.targetWorld ? { label: "Target", pos: p.targetWorld } : null;

  const cardLeft = selPos ? Math.max(12, Math.min(selPos[0] + 18, W - 300)) : 0;
  const cardTop = selPos ? Math.max(70, Math.min(selPos[1] - 24, H - 420)) : 0;

  return (
    <div
      className="sky"
      ref={boxRef}
      onWheel={onWheel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onPointerLeave={() => {
        setCursor(null);
        setHover(null);
        setMapCursor(null);
      }}
      onDoubleClick={onDoubleClick}
    >
      {wantMap && <div ref={mapHost} className={`sky-map${mapOn ? "" : " off"}${isMap ? " live" : ""}`} style={{ "--map-blur": `${mapBlur.toFixed(1)}px` } as React.CSSProperties} aria-hidden />}
      {mapOn && <div className="sky-dim" style={{ opacity: dim }} aria-hidden />}
      {n > 0 && (
        <div
          ref={layerRef}
          className={`sky-layer${p.dim ? " dim" : ""}${isMap ? " on-map" : ""}`}
          // on the map the frame loop moves the layer; React only restates where it last put it
          style={{ width: n, height: n, transform: isMap ? layerMatrix.current : `translate(${tx}px, ${ty}px) scale(${s})` }}
        >
          <canvas ref={imgRef} width={n} height={n} className={p.view.smooth ? "smooth" : "crisp"} style={{ visibility: hasImage ? "visible" : "hidden", filter: p.invert ? "invert(1)" : undefined }} aria-label="SPHEREx image" />
          {showFlags && <canvas ref={flagRef} width={n} height={n} className="crisp flags-layer" />}
        </div>
      )}
      {isMap && W > 0 && (
        <svg className="overlay map-overlay" width={W} height={H} aria-hidden>
          <polygon ref={outlineRef} className="field-outline" points={outlinePts.current} />
          <circle ref={ringRef} className="field-ring" r={7} visibility="hidden" />
          {p.label && <text ref={nameRef} className="field-name" textAnchor="middle">{p.label}</text>}
        </svg>
      )}
      {n > 0 && W > 0 && !isMap && (
        // Overlays live in screen space with an SVG transform so strokes stay hairline at any zoom.
        <svg className={`overlay${p.dim ? " dim" : ""}`} width={W} height={H}>
          <defs>
            <clipPath id="sky-clip">
              <rect x={0} y={0} width={n} height={n} />
            </clipPath>
          </defs>
          <g transform={`translate(${tx} ${ty}) scale(${s})`}>
            {gridLines && (
              <g className="coord-grid" clipPath="url(#sky-clip)">
                {gridLines.lines.map((l, i) => (
                  <polyline key={i} points={l.pts.map(([x, y]) => toL(x, y).join(",")).join(" ")} vectorEffect="non-scaling-stroke" />
                ))}
              </g>
            )}
            <g className="reticle" key={`r${p.lockKey}`}>
              {[[0, 0, 1, 1], [n, 0, -1, 1], [0, n, 1, -1], [n, n, -1, -1]].map(([x, y, sx, sy], i) => (
                <path key={i} d={`M${x + sx * L},${y} L${x},${y} L${x},${y + sy * L}`} vectorEffect="non-scaling-stroke" />
              ))}
            </g>
            {p.overlays.has("track") && p.track.length > 1 && (
              <g className="track">
                <polyline points={p.track.map(([x, y]) => toL(x, y).join(",")).join(" ")} vectorEffect="non-scaling-stroke" />
                {p.track.map(([x, y], i) => {
                  const [lx, ly] = toL(x, y);
                  return <circle key={i} cx={lx} cy={ly} r={(i === p.trackIndex ? 2.5 : 1.5) * u} className={i === p.trackIndex ? "on" : ""} />;
                })}
              </g>
            )}
            {markers.map((m, i) => {
              const st = CATALOG_STYLE[m.cat];
              const [lx, ly] = toL(m.x, m.y);
              const r = (m.pm > HIGH_PM ? 7 : 5) * u;
              const common = { fill: "none", stroke: st.color, strokeWidth: m.pm > HIGH_PM ? 2 : 1.2, vectorEffect: "non-scaling-stroke" as const };
              const shape =
                st.shape === "circle" ? <circle cx={lx} cy={ly} r={r} {...common} />
                  : st.shape === "square" ? <rect x={lx - r * 0.85} y={ly - r * 0.85} width={r * 1.7} height={r * 1.7} {...common} />
                    : st.shape === "plus" ? <path d={`M${lx - r * 1.5},${ly} H${lx - r * 0.5} M${lx + r * 0.5},${ly} H${lx + r * 1.5} M${lx},${ly - r * 1.5} V${ly - r * 0.5} M${lx},${ly + r * 0.5} V${ly + r * 1.5}`} {...common} strokeWidth={1.6} />
                      : <path d={`M${lx},${ly - r} L${lx + r},${ly} L${lx},${ly + r} L${lx - r},${ly} Z`} {...common} />;
              let vector = null;
              if (m.from && m.to && m.cat !== "simbad") {
                const [fx, fy] = toL(...m.from);
                const [tX, tY] = toL(...m.to);
                const ang = Math.atan2(tY - fy, tX - fx);
                const hx = lx + Math.cos(ang) * (r + 16 * u);
                const hy = ly + Math.sin(ang) * (r + 16 * u);
                vector = (
                  <g className="pm-vector" stroke={st.color}>
                    <line x1={fx} y1={fy} x2={tX} y2={tY} vectorEffect="non-scaling-stroke" strokeWidth={2} />
                    <line x1={lx + Math.cos(ang) * r} y1={ly + Math.sin(ang) * r} x2={hx} y2={hy} vectorEffect="non-scaling-stroke" strokeWidth={1.5} />
                    <text x={lx + r + 3 * u} y={ly - r - 2 * u} fontSize={11 * u} strokeWidth={2.5 * u} className="pm-label">
                      {(m.pm / 1000).toFixed(m.pm >= 1000 ? 1 : 2)}″/yr
                    </text>
                  </g>
                );
              }
              return <g key={`${m.cat}${i}`}>{vector}{shape}</g>;
            })}
            {p.overlays.has("target") && p.targetPx && (() => {
              const [lx, ly] = toL(...p.targetPx);
              const a = 7 * u;
              const b = 16 * u;
              const lines = [[lx - b, ly, lx - a, ly], [lx + a, ly, lx + b, ly], [lx, ly - b, lx, ly - a], [lx, ly + a, lx, ly + b]];
              return (
                <g className={`target-mark${p.selection?.kind === "target" ? " sel" : ""}`} key={`t${p.lockKey}`} style={{ transformOrigin: `${lx}px ${ly}px` }}>
                  {lines.map((l, i) => <line key={`h${i}`} x1={l[0]} y1={l[1]} x2={l[2]} y2={l[3]} className="halo" vectorEffect="non-scaling-stroke" />)}
                  {lines.map((l, i) => <line key={`l${i}`} x1={l[0]} y1={l[1]} x2={l[2]} y2={l[3]} vectorEffect="non-scaling-stroke" />)}
                </g>
              );
            })()}
          </g>
          {/* screen-space marks: highlight, selection, pin, labels, grid captions */}
          {hl && (() => {
            const [sx, sy] = toScreen(hl.x, hl.y);
            return <circle className="mark-highlight" cx={sx} cy={sy} r={13} />;
          })()}
          {p.selection?.kind === "source" && selPos && <circle className="mark-selected" cx={selPos[0]} cy={selPos[1]} r={11} />}
          {p.selection?.kind === "point" && selPos && (
            <g className="pin" transform={`translate(${selPos[0]} ${selPos[1]})`} key={`${p.selection.ra},${p.selection.dec}`}>
              <circle r={7} className="halo" />
              <circle r={7} />
              <line x1={-14} x2={-9} y1={0} y2={0} />
              <line x1={9} x2={14} y1={0} y2={0} />
              <line y1={-14} y2={-9} x1={0} x2={0} />
              <line y1={9} y2={14} x1={0} x2={0} />
            </g>
          )}
          {labels.map((l) => (
            <text key={l.key} x={l.x} y={l.y} className="obj-label">{l.text}</text>
          ))}
          {gridCaptions.map((g, i) => (
            <text key={i} x={g.x} y={g.y} textAnchor={g.anchor} className="grid-label">{g.text}</text>
          ))}
          {(p.marks ?? []).map((m) => {
            const [sx, sy] = toScreen(m.x, m.y);
            if (sx < -20 || sy < -20 || sx > W + 20 || sy > H + 20) return null;
            const r = m.active ? 9 : 7;
            const shape =
              m.kind === "mover" ? <path d={`M0,${-r} L${r * 0.87},${r * 0.5} L${-r * 0.87},${r * 0.5} Z`} />
                : m.kind === "red" ? <rect x={-r * 0.8} y={-r * 0.8} width={r * 1.6} height={r * 1.6} />
                  : m.kind === "variable" ? <><circle r={r} /><circle r={r * 0.45} /></>
                    : m.kind === "anomaly" ? <path d={`M0,${-r} L${r * 0.3},${-r * 0.3} L${r},0 L${r * 0.3},${r * 0.3} L0,${r} L${-r * 0.3},${r * 0.3} L${-r},0 L${-r * 0.3},${-r * 0.3} Z`} />
                      : m.kind === "tracklet" ? <><circle r={r * 0.5} /><circle r={r} strokeDasharray="2 2" /></>
                        : m.kind === "stack" ? <path d={`M${-r},0 H${-r * 0.4} M${r * 0.4},0 H${r} M0,${-r} V${-r * 0.4} M0,${r * 0.4} V${r}`} />
                          : <circle r={r} />;
            const to = m.to ? toScreen(...m.to) : null;
            return (
              <g key={m.key} className={`sky-mark ${m.kind}${m.active ? " active" : ""}`}>
                {m.path && m.path.length > 1 && <polyline className="sky-mark-path" points={m.path.map((q) => toScreen(...q).join(",")).join(" ")} />}
                {to && (() => {
                  const ang = Math.atan2(to[1] - sy, to[0] - sx);
                  const len = Math.max(18, Math.min(60, Math.hypot(to[0] - sx, to[1] - sy)));
                  const ex = sx + Math.cos(ang) * len;
                  const ey = sy + Math.sin(ang) * len;
                  return (
                    <g className="sky-mark-arrow">
                      <line x1={sx + Math.cos(ang) * r} y1={sy + Math.sin(ang) * r} x2={ex} y2={ey} />
                      <path d={`M${ex},${ey} l${-6 * Math.cos(ang - 0.45)},${-6 * Math.sin(ang - 0.45)} M${ex},${ey} l${-6 * Math.cos(ang + 0.45)},${-6 * Math.sin(ang + 0.45)}`} />
                    </g>
                  );
                })()}
                <g transform={`translate(${sx} ${sy})`}>
                  {m.title && <title>{m.title}</title>}
                  {shape}
                </g>
                {m.label && <text className="sky-mark-label" x={sx + r + 4} y={sy - r}>{m.label}</text>}
              </g>
            );
          })}
          {ruler && p.grid && (() => {
            const [ax, ay] = toScreen(...ruler.a);
            const b = ruler.b ?? (cursor ? [cursor.x, cursor.y] as [number, number] : null);
            if (!b) return <circle className="ruler-end" cx={ax} cy={ay} r={4} />;
            const [bx, by] = toScreen(...b);
            const wa = pixelToWorld(p.grid, ...ruler.a);
            const wb = pixelToWorld(p.grid, ...b);
            const sep = separation(wa[0], wa[1], wb[0], wb[1]);
            const pa = (positionAngle(wa[0], wa[1], wb[0], wb[1]) + 360) % 360;
            const text = `${formatAngle(sep)} · PA ${pa.toFixed(1)}°`;
            return (
              <g className="ruler">
                <line x1={ax} y1={ay} x2={bx} y2={by} className="halo" />
                <line x1={ax} y1={ay} x2={bx} y2={by} />
                <circle className="ruler-end" cx={ax} cy={ay} r={4} />
                <circle className="ruler-end" cx={bx} cy={by} r={4} />
                <text className="ruler-label" x={(ax + bx) / 2 + 8} y={(ay + by) / 2 - 8}>{text}</text>
              </g>
            );
          })()}
          {hover && !p.selection && (() => {
            const [sx, sy] = toScreen(hover.x, hover.y);
            const tip = markerTip(hover.cat, hover.src);
            return (
              <g className="hover-tip" transform={`translate(${Math.min(sx + 12, W - 220)} ${Math.max(sy + 22, 20)})`}>
                <text className="hover-name">{tip.name}</text>
                <text className="hover-sub" y={13}>{tip.sub}</text>
              </g>
            );
          })()}
        </svg>
      )}

      {p.magnifier && !isMap && cursor && n > 0 && cursor.x >= -0.5 && cursor.y >= -0.5 && cursor.x < n - 0.5 && cursor.y < n - 0.5 && (() => {
        const [sx, sy] = toScreen(cursor.x, cursor.y);
        return (
          <div className="loupe" style={{ left: Math.min(sx + 24, W - 150), top: Math.max(sy - 150, 8) }} aria-hidden>
            <canvas ref={loupeRef} width={132} height={132} style={{ filter: p.invert ? "invert(1)" : undefined }} />
            <span className="loupe-cross" />
          </div>
        );
      })()}

      {/* HUD: compass + scale, epoch line, one coordinate line. Field stepping and full coordinates live in Go to. */}
      {n > 0 && (
        <div className="hud-min">
          <div className="hud-row">
          <button className="nav-btn hud-fit" onClick={resetView} onPointerDown={stop} onPointerUp={stop} onDoubleClick={stop} title={isMap ? "Back to the field" : "Fit the field in view"} aria-label={isMap ? "Back to the field" : "Fit the field in view"}>
            <LocateFixed size={13} />
          </button>
          {mapOn && (
            <button className={`nav-btn hud-fit${isMap && mapFov >= SKY_FOV * 0.9 ? " on" : ""}`} onClick={zoomToSky} onPointerDown={stop} onPointerUp={stop} onDoubleClick={stop} title="Zoom out to the whole sky" aria-label="Zoom out to the whole sky">
              <Globe size={13} />
            </button>
          )}
          {p.overlays.has("compass") && bar && (
            <svg width={bar.px + 50} height={30} className="hud-compass" aria-hidden>
              <g transform="translate(18 24)" className="compass">
                <line x1={0} y1={0} x2={0} y2={-14} />
                <line x1={0} y1={0} x2={-14} y2={0} />
                <text x={0} y={-17} textAnchor="middle">N</text>
                <text x={-19} y={3} textAnchor="middle">E</text>
              </g>
              <g transform="translate(36 22)" className="scalebar">
                <line x1={0} y1={0} x2={bar.px} y2={0} />
                <text x={bar.px / 2} y={-5} textAnchor="middle">{bar.label}</text>
              </g>
            </svg>
          )}
          </div>
          <div className="hud-line" aria-hidden>
            <span>{p.dateLabel}</span>
          </div>
          {readout && readout.label !== "Target" && (
            <div className="readout" aria-live="off">
              <span><i>α</i>{formatRA(readout.pos[0])}</span>
              <span><i>δ</i>{formatDec(readout.pos[1])}</span>
              {value && readout.label === "Cursor" && <span className="muted">{value}</span>}
            </div>
          )}
        </div>
      )}

      {!isMap && p.selection && selPos && p.card && (
        <div className="pick glass" style={{ left: cardLeft, top: cardTop }} onPointerDown={stop} onPointerUp={stop} onPointerMove={stop} onDoubleClick={stop} onWheel={stop}>
          {p.card}
        </div>
      )}

      {p.progress && p.loading && (
        <div className="hud-loader" aria-hidden>
          <div className="hud-loader-target">
            <div className="hud-loader-name">{p.loading.name}</div>
            {p.loading.note && <div className="hud-loader-note">{p.loading.note}</div>}
          </div>
        </div>
      )}
      {p.progress && <LoadChip progress={p.progress} note={p.chipNote} />}
      {d?.kind === "empty" && !p.progress && n > 0 && <div className="sky-note glass">{d.message}</div>}
      {p.error && !p.progress && (
        <div className="sky-note glass error" role="alert" onPointerDown={stop} onPointerUp={stop}>
          {p.back && (
            <button className="btn ghost icon sky-note-close" onClick={p.back.go} title={`Close and go back to ${p.back.label}`} aria-label={`Close and go back to ${p.back.label}`}>
              <X size={14} />
            </button>
          )}
          <span>{p.error}</span>
          <button className="btn" onClick={p.onRetry}>
            <RotateCcw size={14} /> Try again
          </button>
        </div>
      )}
    </div>
  );
}
