/**
 * RA/Dec grid of the sky map, drawn by SkyView over Aladin (whose own grid labels sit along the middle of each
 * line, in the middle of the view). Lines are sampled through the map's projection; captions go where the
 * lines leave the free part of the screen: Dec on the left edge, RA on the bottom edge, like the field's grid.
 * Zoomed out to the whole sky, where lines end on the limb before reaching an edge, a caption sits at the
 * line's leftmost (Dec) or lowest (RA) visible point instead.
 */
import { gridLabel, gridStep } from "./astro";

type Project = (ra: number, dec: number) => [number, number] | null;
type Unproject = (x: number, y: number) => [number, number] | null;

/** The part of the screen captions may use (clear of the top bar, the rail and the HUD). */
export interface CaptionBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface GridCaption {
  x: number;
  y: number;
  text: string;
  anchor: "start" | "end";
}

export interface MapGrid {
  path: string; // SVG path data of every line
  captions: GridCaption[];
}

type Line = { kind: "ra" | "dec"; value: number; segs: [number, number][][] };

const CHAR_W = 6.3; // grid-label: 10 px monospace
const CAPTION_H = 12;

/** Grid lines and captions for a map view of width W × H showing a camera of `fov` degrees around (ra, dec). */
export function mapGrid(cam: { ra: number; dec: number; fov: number }, W: number, H: number, toPix: Project, toSky: Unproject, box: CaptionBox): MapGrid {
  // ---- the visible extent, from a 9 × 9 net of screen points
  const ras: number[] = [];
  const decs: number[] = [];
  let limb = false; // some of the screen is off the sphere
  for (let i = 0; i <= 8; i++)
    for (let j = 0; j <= 8; j++) {
      const w = toSky((W * i) / 8, (H * j) / 8);
      if (!w) {
        limb = true;
        continue;
      }
      ras.push(((w[0] - cam.ra + 540) % 360) - 180);
      decs.push(w[1]);
    }
  if (!decs.length) return { path: "", captions: [] };
  let raMin = Math.min(...ras);
  let raMax = Math.max(...ras);
  let decMin = Math.min(...decs);
  let decMax = Math.max(...decs);
  const onScreen = (q: [number, number] | null) => !!q && q[0] >= 0 && q[0] <= W && q[1] >= 0 && q[1] <= H;
  const northPole = toPix(0, 90);
  const southPole = toPix(0, -90);
  if (onScreen(northPole)) decMax = 90;
  if (onScreen(southPole)) decMin = -90;
  if (limb || onScreen(northPole) || onScreen(southPole) || cam.fov > 90) [raMin, raMax] = [-180, 180];
  if (limb) [decMin, decMax] = [Math.max(-90, cam.dec - 90), Math.min(90, cam.dec + 90)];

  const decStep = gridStep(decMax - decMin, "dec", 6); // arcsec
  const raStep = gridStep((raMax - raMin) * Math.max(0.15, Math.cos((cam.dec * Math.PI) / 180)), "ra", 6); // s of time
  const decStepDeg = decStep / 3600;
  const raStepDeg = raStep / 240;
  const samples = cam.fov < 20 ? 24 : cam.fov < 60 ? 48 : 90;

  /** Project a run of sky points, split where it leaves the visible side or jumps. */
  const trace = (pt: (t: number) => [number, number]): [number, number][][] => {
    const segs: [number, number][][] = [];
    let cur: [number, number][] = [];
    for (let i = 0; i <= samples; i++) {
      const q = toPix(...pt(i / samples));
      const prev = cur[cur.length - 1];
      if (!q || (prev && Math.hypot(q[0] - prev[0], q[1] - prev[1]) > Math.max(W, H))) {
        if (cur.length > 1) segs.push(cur);
        cur = q ? [q] : [];
        continue;
      }
      cur.push(q);
    }
    if (cur.length > 1) segs.push(cur);
    return segs;
  };

  const lines: Line[] = [];
  for (let k = Math.ceil(decMin / decStepDeg); k * decStepDeg <= decMax && lines.length < 40; k++) {
    const dec = k * decStepDeg;
    if (Math.abs(dec) >= 90) continue;
    lines.push({ kind: "dec", value: dec, segs: trace((t) => [cam.ra + raMin + (raMax - raMin) * t, dec]) });
  }
  const d0 = Math.max(-89.99, decMin);
  const d1 = Math.min(89.99, decMax);
  for (let k = Math.ceil((cam.ra + raMin) / raStepDeg); k * raStepDeg <= cam.ra + raMax && lines.length < 90; k++) {
    const ra = k * raStepDeg;
    if (raMax - raMin >= 360 && k * raStepDeg >= cam.ra + raMin + 360 - 1e-9) break; // the full circle: once
    lines.push({ kind: "ra", value: ((ra % 360) + 360) % 360, segs: trace((t) => [ra, d0 + (d1 - d0) * t]) });
  }

  const path = lines
    .flatMap((l) => l.segs)
    .map((sg) => `M${sg.map((q) => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join("L")}`)
    .join("");

  // ---- captions: Dec first (left edge), then RA (bottom edge); none may overlap another
  const placed: [number, number, number, number][] = [];
  const captions: GridCaption[] = [];
  const add = (x: number, y: number, text: string, anchor: "start" | "end") => {
    const w = text.length * CHAR_W + 4;
    const x0 = anchor === "start" ? x - 2 : x - w + 2;
    const r: [number, number, number, number] = [x0, y - CAPTION_H + 2, x0 + w, y + 3];
    if (r[0] < box.left - 2 || r[2] > box.right + 2 || r[1] < box.top - 2 || r[3] > box.bottom + 6) return;
    if (placed.some((b) => r[0] < b[2] && r[2] > b[0] && r[1] < b[3] && r[3] > b[1])) return;
    placed.push(r);
    captions.push({ x, y, text, anchor });
  };
  const inside = (q: [number, number]) => q[0] >= box.left && q[0] <= box.right && q[1] >= box.top && q[1] <= box.bottom;
  const near = (q: [number, number], p: [number, number] | null) => !!p && Math.hypot(q[0] - p[0], q[1] - p[1]) < 24;

  for (const l of lines) {
    if (l.kind !== "dec") continue;
    let at: [number, number] | null = null;
    // where it crosses the left edge of the free area ...
    for (const sg of l.segs) {
      for (let i = 1; i < sg.length && !at; i++) {
        const [a, b] = [sg[i - 1], sg[i]];
        if ((a[0] - box.left) * (b[0] - box.left) > 0 || a[0] === b[0]) continue;
        const y = a[1] + ((box.left - a[0]) / (b[0] - a[0])) * (b[1] - a[1]);
        if (y >= box.top + 8 && y <= box.bottom) at = [box.left, y];
      }
    }
    // ... or else its leftmost point in it (the limb, or a circle round a pole)
    if (!at) for (const sg of l.segs) for (const q of sg) if (inside(q) && (!at || q[0] < at[0])) at = q;
    if (at) add(at[0] + 5, at[1] - 4, gridLabel(l.value, "dec", decStep), "start");
  }
  for (const l of lines) {
    if (l.kind !== "ra") continue;
    let at: [number, number] | null = null;
    let edge = false;
    for (const sg of l.segs) {
      for (let i = 1; i < sg.length && !at; i++) {
        const [a, b] = [sg[i - 1], sg[i]];
        if ((a[1] - box.bottom) * (b[1] - box.bottom) > 0 || a[1] === b[1]) continue;
        const x = a[0] + ((box.bottom - a[1]) / (b[1] - a[1])) * (b[0] - a[0]);
        if (x >= box.left && x <= box.right) {
          at = [x, box.bottom];
          edge = true;
        }
      }
    }
    if (!at) for (const sg of l.segs) for (const q of sg) if (inside(q) && (!at || q[1] > at[1])) at = q;
    if (!at || (!edge && (near(at, southPole) || near(at, northPole)))) continue; // every meridian meets at a pole
    const text = gridLabel(l.value, "ra", raStep);
    if (edge) add(at[0] - 4, at[1] - 6, text, "end");
    else add(at[0] + 4, at[1] + 12, text, "start");
  }
  return { path, captions };
}
