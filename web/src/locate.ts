/**
 * The source under a click, found in the image the browser already has (no request): the click is a hint,
 * the source is the blob whose light dominates there.
 *
 * On the noise-normalised image (S/N), lightly smoothed, we climb from the clicked pixel to the local maximum.
 * If that peak is within SEARCH px of the click and above DETECT S/N, it is the source "under the cursor";
 * otherwise the brightest peak within WIDE px; otherwise there is no source here. Its centre is the iterated
 * flux-weighted centroid above the local background (annulus), so the analysis measures the source itself
 * rather than wherever the pin landed. The server may refine this further.
 */

export const SEARCH_PX = 3;
export const WIDE_PX = 5;
export const DETECT_SNR = 5;

export interface Located {
  found: true;
  /** Centroid, grid pixels. */
  x: number;
  y: number;
  /** Peak S/N on the image used. */
  snr: number;
  /** Distance from the click, grid pixels. */
  offsetPx: number;
  how: "under" | "nearby";
}

export interface NotFound {
  found: false;
  /** S/N at the clicked pixel. */
  snr: number | null;
}

function robust(img: ArrayLike<number>): { med: number; sigma: number } {
  const step = Math.max(1, Math.floor(img.length / 40000));
  const v: number[] = [];
  for (let i = 0; i < img.length; i += step) if (Number.isFinite(img[i])) v.push(img[i]);
  if (v.length < 20) return { med: 0, sigma: NaN };
  v.sort((a, b) => a - b);
  const med = v[v.length >> 1];
  const dev = v.map((x) => Math.abs(x - med)).sort((a, b) => a - b);
  return { med, sigma: 1.4826 * dev[dev.length >> 1] };
}

/** S/N value at (x, y), NaN outside or where there is no data. */
const at = (s: Float32Array, n: number, x: number, y: number) => (x < 0 || y < 0 || x >= n || y >= n ? NaN : s[y * n + x]);

export function snrImage(img: ArrayLike<number>, n: number, normalised = false): Float32Array {
  const out = new Float32Array(n * n);
  const { med, sigma } = normalised ? { med: 0, sigma: 1 } : robust(img);
  for (let i = 0; i < n * n; i++) out[i] = sigma > 0 ? (img[i] - med) / sigma : NaN;
  return out;
}

function smooth3(s: Float32Array, n: number): Float32Array {
  const out = new Float32Array(n * n).fill(-Infinity);
  const w = [1, 2, 1];
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++) {
      let sum = 0;
      let wt = 0;
      for (let dy = -1; dy <= 1; dy++)
        for (let dx = -1; dx <= 1; dx++) {
          const v = at(s, n, x + dx, y + dy);
          if (!Number.isFinite(v)) continue;
          const k = w[dx + 1] * w[dy + 1];
          sum += k * v;
          wt += k;
        }
      if (wt >= 6) out[y * n + x] = sum / wt;
    }
  return out;
}

function climb(s: Float32Array, n: number, x0: number, y0: number, maxDist: number): [number, number] | null {
  let x = Math.round(x0);
  let y = Math.round(y0);
  if (x < 0 || y < 0 || x >= n || y >= n) return null;
  for (let step = 0; step < 4 * (maxDist + 2); step++) {
    let bx = x;
    let by = y;
    let bv = s[y * n + x];
    for (let dy = -1; dy <= 1; dy++)
      for (let dx = -1; dx <= 1; dx++) {
        const v = at(s, n, x + dx, y + dy);
        if (v > bv) {
          bv = v;
          bx = x + dx;
          by = y + dy;
        }
      }
    if (bx === x && by === y) return [x, y];
    x = bx;
    y = by;
    if (Math.hypot(x - x0, y - y0) > maxDist + 1) return null;
  }
  return [x, y];
}

function isPeak(s: Float32Array, n: number, x: number, y: number): boolean {
  const v = s[y * n + x];
  if (!Number.isFinite(v)) return false;
  for (let dy = -1; dy <= 1; dy++)
    for (let dx = -1; dx <= 1; dx++) if ((dx || dy) && at(s, n, x + dx, y + dy) > v) return false;
  return true;
}

/** Iterated flux-weighted centroid of the excess over an annulus background, within 1.5 px of the peak. */
function centroid(s: Float32Array, n: number, px: number, py: number): [number, number] {
  let cx = px;
  let cy = py;
  for (let it = 0; it < 5; it++) {
    const ann: number[] = [];
    for (let dy = -7; dy <= 7; dy++)
      for (let dx = -7; dx <= 7; dx++) {
        const r = Math.hypot(dx + Math.round(cx) - cx, dy + Math.round(cy) - cy);
        const v = at(s, n, Math.round(cx) + dx, Math.round(cy) + dy);
        if (r >= 4 && r < 7 && Number.isFinite(v)) ann.push(v);
      }
    ann.sort((a, b) => a - b);
    const bkg = ann.length ? ann[ann.length >> 1] : 0;
    let sw = 0;
    let sx = 0;
    let sy = 0;
    for (let dy = -3; dy <= 3; dy++)
      for (let dx = -3; dx <= 3; dx++) {
        const x = Math.round(cx) + dx;
        const y = Math.round(cy) + dy;
        if (Math.hypot(x - cx, y - cy) > 2.5) continue;
        const v = at(s, n, x, y) - bkg;
        if (!(v > 0)) continue;
        sw += v;
        sx += v * x;
        sy += v * y;
      }
    if (sw <= 0) break;
    const nx = sx / sw;
    const ny = sy / sw;
    if (Math.hypot(nx - px, ny - py) > 1.5) break;
    const moved = Math.hypot(nx - cx, ny - cy);
    cx = nx;
    cy = ny;
    if (moved < 1e-3) break;
  }
  return [cx, cy];
}

/** The source under (or near) grid position (x0, y0). `normalised`: the image is already S/N. */
export function locateSource(img: ArrayLike<number>, n: number, x0: number, y0: number, normalised = false): Located | NotFound {
  const s = snrImage(img, n, normalised);
  const sm = smooth3(s, n);
  const clickSnr = at(s, n, Math.round(x0), Math.round(y0));
  let pick: [number, number] | null = null;
  let how: Located["how"] = "under";
  const top = climb(sm, n, x0, y0, SEARCH_PX);
  if (top) {
    // the climb ran on the smoothed map: snap to the strongest raw pixel next to where it stopped
    let best = top;
    for (let dy = -1; dy <= 1; dy++)
      for (let dx = -1; dx <= 1; dx++) if (at(s, n, top[0] + dx, top[1] + dy) > at(s, n, best[0], best[1])) best = [top[0] + dx, top[1] + dy];
    if (Math.hypot(best[0] - x0, best[1] - y0) <= SEARCH_PX + 0.5 && at(s, n, best[0], best[1]) >= DETECT_SNR) pick = best;
  }
  if (!pick) {
    let bv = DETECT_SNR;
    const r = Math.ceil(WIDE_PX);
    for (let dy = -r; dy <= r; dy++)
      for (let dx = -r; dx <= r; dx++) {
        const x = Math.round(x0) + dx;
        const y = Math.round(y0) + dy;
        if (Math.hypot(x - x0, y - y0) > WIDE_PX || x < 0 || y < 0 || x >= n || y >= n) continue;
        const v = s[y * n + x];
        if (v >= bv && isPeak(s, n, x, y)) {
          bv = v;
          pick = [x, y];
        }
      }
    how = "nearby";
  }
  if (!pick) return { found: false, snr: Number.isFinite(clickSnr) ? clickSnr : null };
  const [cx, cy] = centroid(s, n, pick[0], pick[1]);
  return { found: true, x: cx, y: cy, snr: s[pick[1] * n + pick[0]], offsetPx: Math.hypot(cx - x0, cy - y0), how };
}

/** A square stamp of one or three images around (cx, cy): values row-major, NaN outside. */
export interface Stamp {
  size: number;
  /** Grid pixel of the stamp's [0, 0]. */
  x0: number;
  y0: number;
  channels: Float32Array[];
}

export function cutStamp(images: ArrayLike<number>[], n: number, cx: number, cy: number, half = 10): Stamp {
  const size = 2 * half + 1;
  const x0 = Math.round(cx) - half;
  const y0 = Math.round(cy) - half;
  const channels = images.map((img) => {
    const out = new Float32Array(size * size).fill(NaN);
    for (let j = 0; j < size; j++)
      for (let i = 0; i < size; i++) {
        const x = x0 + i;
        const y = y0 + j;
        if (x >= 0 && y >= 0 && x < n && y < n) out[j * size + i] = img[y * n + x];
      }
    return out;
  });
  return { size, x0, y0, channels };
}
