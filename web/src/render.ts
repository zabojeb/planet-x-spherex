import type { BandId, SequenceData } from "./types";

export type Stretch = "asinh" | "linear" | "log";
export type Cmap = "gray" | "ice";
export type BandSel = BandId | "all";

/** No-data pixels: a slate tone that cannot be mistaken for dark sky. */
export const NAN_RGB: [number, number, number] = [38, 44, 54];

function hex(h: string): [number, number, number] {
  const v = parseInt(h.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function buildLut(stops: [number, string][]): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(256 * 3);
  const cols = stops.map(([p, c]) => [p, ...hex(c)] as [number, number, number, number]);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let k = 0;
    while (k < cols.length - 2 && t > cols[k + 1][0]) k++;
    const [p0, r0, g0, b0] = cols[k];
    const [p1, r1, g1, b1] = cols[k + 1];
    const u = p1 === p0 ? 0 : Math.min(1, Math.max(0, (t - p0) / (p1 - p0)));
    lut[i * 3] = r0 + (r1 - r0) * u;
    lut[i * 3 + 1] = g0 + (g1 - g0) * u;
    lut[i * 3 + 2] = b0 + (b1 - b0) * u;
  }
  return lut;
}

export const LUTS: Record<Cmap, Uint8ClampedArray> = {
  gray: buildLut([
    [0, "#000000"],
    [1, "#ffffff"],
  ]),
  // One hue (blue ramp of the palette), dark to light: a perceptual alternative to gray.
  ice: buildLut([
    [0, "#000000"],
    [0.3, "#104281"],
    [0.55, "#2a78d6"],
    [0.8, "#86b6ef"],
    [1, "#f4f6fb"],
  ]),
};

/** Diverging blue <-> red with a neutral gray midpoint (zero difference). */
export const DIVERGING = buildLut([
  [0, "#cde2fb"],
  [0.22, "#3987e5"],
  [0.5, "#383835"],
  [0.78, "#e66767"],
  [1, "#fbd5d5"],
]);

export const CMAP_LABEL: Record<Cmap, string> = { gray: "Gray", ice: "Blue" };

export function applyStretch(t: number, kind: Stretch): number {
  if (kind === "asinh") return Math.asinh(10 * t) / Math.asinh(10);
  if (kind === "log") return Math.log10(1 + 1000 * t) / Math.log10(1001);
  return t;
}

/** Percentiles of the finite values of several arrays (uniformly subsampled for speed). */
export function percentiles(arrays: ArrayLike<number>[], ps: number[], maxSamples = 250000): number[] {
  let total = 0;
  for (const a of arrays) total += a.length;
  const stride = Math.max(1, Math.floor(total / maxSamples));
  const buf = new Float32Array(Math.ceil(total / stride) + 1);
  let k = 0;
  let i = 0;
  for (const a of arrays) {
    for (let j = 0; j < a.length; j++, i++) {
      if (i % stride) continue;
      const v = a[j];
      if (Number.isFinite(v)) buf[k++] = v;
    }
  }
  if (!k) return ps.map(() => NaN);
  const s = buf.subarray(0, k).sort();
  return ps.map((p) => {
    const pos = Math.min(k - 1, Math.max(0, (p / 100) * (k - 1)));
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return s[lo] + (s[hi] - s[lo]) * (pos - lo);
  });
}

export function robustSigma(a: Float32Array): number {
  const [med] = percentiles([a], [50]);
  const dev = new Float32Array(a.length);
  let k = 0;
  for (let i = 0; i < a.length; i++) {
    const v = a[i];
    if (Number.isFinite(v)) dev[k++] = Math.abs(v - med);
  }
  if (!k) return NaN;
  const s = dev.subarray(0, k).sort();
  return 1.4826 * s[Math.floor(k / 2)] || NaN;
}

/** Images derived from one sequence, computed lazily and cached. Band order comes from meta.bands. */
export class Derived {
  readonly n: number;
  readonly size: number;
  readonly nEpochs: number;
  readonly bands: BandId[];
  /** False-colour channels [R, G, B]: the detectors split into three groups by wavelength, longest = red. */
  readonly rgbGroups: [BandId[], BandId[], BandId[]];
  private cache = new Map<string, unknown>();

  constructor(readonly seq: SequenceData) {
    this.n = seq.meta.grid.n;
    this.size = this.n * this.n;
    this.nEpochs = seq.meta.epochs.length;
    this.bands = seq.meta.bands.map((b) => b.id);
    const byWl = [...seq.meta.bands].sort((a, b) => a.wl_min - b.wl_min).map((b) => b.id);
    const third = Math.ceil(byWl.length / 3);
    this.rgbGroups = [byWl.slice(2 * third), byWl.slice(third, 2 * third), byWl.slice(0, third)];
  }

  private memo<T>(key: string, make: () => T): T {
    if (!this.cache.has(key)) this.cache.set(key, make());
    return this.cache.get(key) as T;
  }

  private offset(e: number, b: BandId): number {
    return (e * this.bands.length + this.bands.indexOf(b)) * this.size;
  }

  hasBand(e: number, b: BandId): boolean {
    return !!this.seq.meta.epochs[e]?.bands[b];
  }

  hasAny(e: number): boolean {
    return this.bands.some((b) => this.hasBand(e, b));
  }

  has(e: number, sel: BandSel | "rgb"): boolean {
    return sel === "all" || sel === "rgb" ? this.hasAny(e) : this.hasBand(e, sel);
  }

  /** Epochs that have data for the selection, in time order. */
  playable(sel: BandSel | "rgb"): number[] {
    const out: number[] = [];
    for (let e = 0; e < this.nEpochs; e++) if (this.has(e, sel)) out.push(e);
    return out;
  }

  coadd(e: number, b: BandId): Float32Array {
    const off = this.offset(e, b);
    return this.seq.coadds.subarray(off, off + this.size);
  }

  coverage(e: number, b: BandId): Uint16Array {
    const off = this.offset(e, b);
    return this.seq.coverage.subarray(off, off + this.size);
  }

  sigma(e: number, b: BandId): number {
    return this.memo(`sig|${e}|${b}`, () => robustSigma(this.coadd(e, b)));
  }

  /** Noise-normalised combination of all detectors (a signal-to-noise image). */
  all(e: number): Float32Array {
    return this.memo(`all|${e}`, () => this.combine(e, this.bands.filter((b) => this.hasBand(e, b))));
  }

  image(e: number, sel: BandSel): Float32Array {
    return sel === "all" ? this.all(e) : this.coadd(e, sel);
  }

  /** False colour: each channel is the mean of its detector group (NaN-aware). */
  rgb(e: number): [Float32Array, Float32Array, Float32Array] {
    return this.memo(`rgb|${e}`, () => {
      const channel = (group: BandId[]) => {
        const sum = new Float32Array(this.size);
        const cnt = new Uint8Array(this.size);
        for (const b of group) {
          if (!this.hasBand(e, b)) continue;
          const a = this.coadd(e, b);
          for (let i = 0; i < this.size; i++) {
            const v = a[i];
            if (Number.isFinite(v)) {
              sum[i] += v;
              cnt[i]++;
            }
          }
        }
        const out = new Float32Array(this.size);
        for (let i = 0; i < this.size; i++) out[i] = cnt[i] ? sum[i] / cnt[i] : NaN;
        return out;
      };
      return this.rgbGroups.map(channel) as [Float32Array, Float32Array, Float32Array];
    });
  }

  /** Coverage-weighted mean of the chosen epochs. */
  stack(sel: BandSel, epochs: number[]): Float32Array {
    return this.memo(`stack|${sel}|${epochs.join(",")}`, () => {
      const sum = new Float64Array(this.size);
      const w = new Float64Array(this.size);
      for (const e of epochs) {
        if (!this.has(e, sel)) continue;
        const img = this.image(e, sel);
        const cov = sel === "all" ? null : this.coverage(e, sel);
        for (let i = 0; i < this.size; i++) {
          const v = img[i];
          if (!Number.isFinite(v)) continue;
          const wi = cov ? cov[i] || 1 : 1;
          sum[i] += v * wi;
          w[i] += wi;
        }
      }
      const out = new Float32Array(this.size);
      for (let i = 0; i < this.size; i++) out[i] = w[i] ? sum[i] / w[i] : NaN;
      return out;
    });
  }

  /** S/N combination restricted to the given detectors (so two epochs are compared like with like). */
  private combine(e: number, bands: BandId[]): Float32Array {
    const sum = new Float32Array(this.size);
    const cnt = new Uint8Array(this.size);
    for (const b of bands) {
      const s = this.sigma(e, b);
      if (!Number.isFinite(s) || s <= 0) continue;
      const a = this.coadd(e, b);
      for (let i = 0; i < this.size; i++) {
        const v = a[i];
        if (Number.isFinite(v)) {
          sum[i] += v / s;
          cnt[i]++;
        }
      }
    }
    const out = new Float32Array(this.size);
    for (let i = 0; i < this.size; i++) out[i] = cnt[i] ? sum[i] / Math.sqrt(cnt[i]) : NaN;
    return out;
  }

  /** Epoch minus reference. For "all", only detectors present in both epochs are combined. */
  diff(sel: BandSel, e: number, ref: number): Float32Array {
    return this.memo(`diff|${sel}|${e}|${ref}`, () => {
      let a: Float32Array;
      let r: Float32Array;
      if (sel === "all") {
        const common = this.bands.filter((b) => this.hasBand(e, b) && this.hasBand(ref, b));
        a = this.combine(e, common);
        r = this.combine(ref, common);
      } else {
        a = this.image(e, sel);
        r = this.image(ref, sel);
      }
      const out = new Float32Array(this.size);
      for (let i = 0; i < this.size; i++) out[i] = a[i] - r[i];
      return out;
    });
  }

  limits(key: string, arrays: () => ArrayLike<number>[], lo: number, hi: number): [number, number] {
    return this.memo(`lim|${key}|${lo}|${hi}`, () => {
      const [a, b] = percentiles(arrays(), [lo, hi]);
      return [a, b > a ? b : a + 1e-6] as [number, number];
    });
  }

  /** Bitwise OR of the flag maps of the selected detectors in one epoch. */
  flagsOr(e: number, sel: BandSel | "rgb"): Uint32Array {
    return this.memo(`flags|${e}|${sel}`, () => {
      const out = new Uint32Array(this.size);
      const bands = sel === "all" || sel === "rgb" ? this.bands : [sel];
      for (const b of bands) {
        if (!this.hasBand(e, b)) continue;
        const off = this.offset(e, b);
        const f = this.seq.flags.subarray(off, off + this.size);
        for (let i = 0; i < this.size; i++) out[i] |= f[i];
      }
      return out;
    });
  }
}

// ------------------------------------------------------------------ painting (rows flipped: north up)

export function paintScalar(img: ArrayLike<number>, n: number, lo: number, hi: number, stretch: Stretch,
  lut: Uint8ClampedArray, out: ImageData): void {
  const d = out.data;
  const span = hi - lo || 1;
  for (let r = 0; r < n; r++) {
    const y = n - 1 - r;
    for (let x = 0; x < n; x++) {
      const v = img[y * n + x];
      const o = (r * n + x) * 4;
      if (!Number.isFinite(v)) {
        d[o] = NAN_RGB[0];
        d[o + 1] = NAN_RGB[1];
        d[o + 2] = NAN_RGB[2];
      } else {
        const t = applyStretch(Math.min(1, Math.max(0, (v - lo) / span)), stretch);
        const li = Math.round(t * 255) * 3;
        d[o] = lut[li];
        d[o + 1] = lut[li + 1];
        d[o + 2] = lut[li + 2];
      }
      d[o + 3] = 255;
    }
  }
}

export function paintDiverging(img: ArrayLike<number>, n: number, lim: number, stretch: Stretch, out: ImageData): void {
  const d = out.data;
  const soft = stretch === "linear" ? 0 : 4;
  const norm = soft ? Math.asinh(soft) : 1;
  for (let r = 0; r < n; r++) {
    const y = n - 1 - r;
    for (let x = 0; x < n; x++) {
      const v = img[y * n + x];
      const o = (r * n + x) * 4;
      if (!Number.isFinite(v)) {
        d[o] = NAN_RGB[0];
        d[o + 1] = NAN_RGB[1];
        d[o + 2] = NAN_RGB[2];
      } else {
        let t = Math.min(1, Math.max(-1, v / (lim || 1)));
        if (soft) t = Math.asinh(soft * t) / norm;
        const li = Math.round(((t + 1) / 2) * 255) * 3;
        d[o] = DIVERGING[li];
        d[o + 1] = DIVERGING[li + 1];
        d[o + 2] = DIVERGING[li + 2];
      }
      d[o + 3] = 255;
    }
  }
}

export function paintRGB(ch: [Float32Array, Float32Array, Float32Array], n: number, lims: [number, number][],
  stretch: Stretch, out: ImageData): void {
  const d = out.data;
  for (let r = 0; r < n; r++) {
    const y = n - 1 - r;
    for (let x = 0; x < n; x++) {
      const i = y * n + x;
      const o = (r * n + x) * 4;
      let any = false;
      for (let c = 0; c < 3; c++) {
        const v = ch[c][i];
        if (Number.isFinite(v)) {
          any = true;
          const [lo, hi] = lims[c];
          d[o + c] = Math.round(applyStretch(Math.min(1, Math.max(0, (v - lo) / (hi - lo || 1))), stretch) * 255);
        } else d[o + c] = 0;
      }
      if (!any) {
        d[o] = NAN_RGB[0];
        d[o + 1] = NAN_RGB[1];
        d[o + 2] = NAN_RGB[2];
      }
      d[o + 3] = 255;
    }
  }
}

export function paintFlags(flags: Uint32Array, n: number, mask: number, out: ImageData, rgb: [number, number, number]): number {
  const d = out.data;
  let count = 0;
  for (let r = 0; r < n; r++) {
    const y = n - 1 - r;
    for (let x = 0; x < n; x++) {
      const o = (r * n + x) * 4;
      if (mask && flags[y * n + x] & mask) {
        d[o] = rgb[0];
        d[o + 1] = rgb[1];
        d[o + 2] = rgb[2];
        d[o + 3] = 150;
        count++;
      } else d[o + 3] = 0;
    }
  }
  return count;
}
