/** Instant probe: a quick flux estimate at any sky position from the co-add planes already in the browser
 *  (no server round-trip). Per epoch for one detector (a light curve) and per detector for one epoch (a
 *  coarse spectrum), plus S/N, coverage and contamination flags at the pixel. Framework-free.
 *
 *  Method: circular aperture of PROBE_R native pixels on the background-subtracted co-add (MJy/sr), minus the
 *  median of an annulus, times the pixel solid angle → mJy. No PSF aperture correction: the co-adds are
 *  resampled, so a 3 px aperture holds most but not all of a point source's light. It is labelled
 *  "quick estimate"; the server's Analyze step does PSF photometry on the native exposures. */
import type { BandId, FlagBit, Meta, PlaneKey, PlaneSource } from "./types";

export const PROBE_R = 3; // aperture radius, pixels
const ANN_IN = 5;
const ANN_OUT = 8;
const ARCSEC2_TO_SR = (Math.PI / 180 / 3600) ** 2;

export interface ProbeMeasure {
  flux: number; // mJy
  err: number; // mJy, from the annulus scatter
  snr: number;
  /** Exposures that contributed at the central pixel. */
  coverage: number | null;
  /** OR of the contamination flags within 1.5 px (when the flags plane is loaded). */
  flags: number | null;
  npix: number;
}

export interface ProbePoint extends ProbeMeasure {
  epoch: number;
  band: BandId;
  mjd: number;
  date: string;
  wl: number; // µm, centre of the epoch's coverage in this detector
  wlLo: number;
  wlHi: number;
}

export interface ProbeResult {
  x: number;
  y: number;
  inside: boolean;
  /** The detector the light curve uses (the shown one, or the best-S/N one when "All" is shown). */
  lcBand: BandId | null;
  current: ProbePoint | null;
  lightCurve: ProbePoint[];
  spectrum: ProbePoint[];
  /** Planes this probe would use but that are not in memory yet. */
  missing: PlaneKey[];
}

function median(a: number[]): number {
  if (!a.length) return NaN;
  const s = [...a].sort((p, q) => p - q);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : 0.5 * (s[m - 1] + s[m]);
}

/** Aperture flux at pixel (x, y) of one [n, n] co-add plane. */
export function aperture(
  plane: Float32Array,
  n: number,
  x: number,
  y: number,
  pixscaleArcsec: number,
  coverage?: Uint16Array,
  flags?: Uint32Array,
): ProbeMeasure | null {
  const ix = Math.round(x);
  const iy = Math.round(y);
  if (ix < 0 || iy < 0 || ix >= n || iy >= n) return null;
  let sum = 0;
  let npix = 0;
  let fl = 0;
  const ann: number[] = [];
  const r0 = Math.ceil(ANN_OUT);
  for (let j = Math.max(0, iy - r0); j <= Math.min(n - 1, iy + r0); j++) {
    for (let i = Math.max(0, ix - r0); i <= Math.min(n - 1, ix + r0); i++) {
      const d = Math.hypot(i - x, j - y);
      const v = plane[j * n + i];
      if (d <= PROBE_R) {
        if (Number.isFinite(v)) {
          sum += v;
          npix++;
        }
        if (flags && d <= 1.5) fl |= flags[j * n + i];
      } else if (d >= ANN_IN && d <= ANN_OUT && Number.isFinite(v)) ann.push(v);
    }
  }
  if (npix < 4) return null;
  const bkg = ann.length >= 8 ? median(ann) : 0;
  const mad = ann.length >= 8 ? 1.4826 * median(ann.map((v) => Math.abs(v - bkg))) : NaN;
  const omega = pixscaleArcsec * pixscaleArcsec * ARCSEC2_TO_SR;
  const toMjy = omega * 1e9; // (MJy/sr) × sr → MJy → mJy
  const flux = (sum - bkg * npix) * toMjy;
  // background scatter over the aperture, plus a 3 % calibration floor (as the pipeline's photometry)
  const stat = Number.isFinite(mad) ? mad * Math.sqrt(npix * (1 + npix / Math.max(ann.length, 1))) * toMjy : NaN;
  const err = Number.isFinite(stat) ? Math.hypot(stat, 0.03 * Math.abs(sum - bkg * npix) * toMjy) : NaN;
  return {
    flux,
    err,
    snr: Number.isFinite(err) && err > 0 ? flux / err : NaN,
    coverage: coverage ? coverage[iy * n + ix] : null,
    flags: flags ? fl >>> 0 : null,
    npix,
  };
}

/** The planes a probe at (epoch, band) wants: every detector of that epoch (spectrum), every epoch of the
 *  light-curve detector, and the flags plane of the shown epoch and detector. */
export function probeNeeds(meta: Meta, epoch: number, band: BandId | "all", lcBand: BandId | null): PlaneKey[] {
  const out: PlaneKey[] = [];
  const ep = meta.epochs[epoch];
  if (ep) for (const b of meta.bands) if (ep.bands[b.id]) out.push({ array: "coadds", e: epoch, b: b.id });
  const lb = band !== "all" ? band : lcBand;
  if (lb) {
    meta.epochs.forEach((e, i) => {
      if (e.bands[lb] && i !== epoch) out.push({ array: "coadds", e: i, b: lb });
    });
    if (ep?.bands[lb]) {
      out.push({ array: "flags", e: epoch, b: lb });
      out.push({ array: "coverage", e: epoch, b: lb });
    }
  }
  return out;
}

/**
 * Measure at a position (given per epoch, so a moving target or a high-proper-motion source is followed).
 * Uses only planes already in memory; reports the rest in `missing`.
 */
export function probe(
  meta: Meta,
  planes: PlaneSource,
  pixelAt: (epoch: number) => [number, number],
  epoch: number,
  band: BandId | "all",
): ProbeResult {
  const n = meta.grid.n;
  const pix = meta.grid.pixscale_arcsec;
  const [x, y] = pixelAt(epoch);
  const inside = x >= 0 && y >= 0 && x <= n - 1 && y <= n - 1;
  const missing: PlaneKey[] = [];
  const measure = (e: number, b: BandId, withExtras: boolean): ProbePoint | null => {
    const ep = meta.epochs[e];
    const eb = ep?.bands[b];
    if (!ep || !eb) return null;
    const plane = planes.plane("coadds", e, b);
    if (!plane) {
      missing.push({ array: "coadds", e, b });
      return null;
    }
    const [px, py] = pixelAt(e);
    const cov = withExtras ? planes.plane("coverage", e, b) : undefined;
    const fl = withExtras ? planes.plane("flags", e, b) : undefined;
    const m = aperture(plane, n, px, py, pix, cov, fl);
    if (!m) return null;
    return { ...m, epoch: e, band: b, mjd: eb.mjd_mean ?? ep.mjd_mean, date: ep.date_min.slice(0, 7), wl: (eb.wl_min + eb.wl_max) / 2, wlLo: eb.wl_min, wlHi: eb.wl_max };
  };

  const spectrum: ProbePoint[] = [];
  if (inside) for (const b of meta.bands) {
    const p = measure(epoch, b.id, false);
    if (p) spectrum.push(p);
  }
  let lcBand: BandId | null = band !== "all" ? band : null;
  if (!lcBand && spectrum.length) {
    const best = spectrum.filter((p) => Number.isFinite(p.snr)).sort((a, b) => b.snr - a.snr)[0];
    lcBand = (best ?? spectrum[0]).band;
  }
  const lightCurve: ProbePoint[] = [];
  let current: ProbePoint | null = null;
  if (inside && lcBand) {
    meta.epochs.forEach((_, e) => {
      const p = measure(e, lcBand!, e === epoch);
      if (p) lightCurve.push(p);
      if (p && e === epoch) current = p;
    });
  }
  return { x, y, inside, lcBand, current, lightCurve, spectrum, missing };
}

/** AB magnitude of a flux in mJy (null for non-positive flux). */
export function abMag(mjy: number): number | null {
  return mjy > 0 ? -2.5 * Math.log10((mjy * 1e-3) / 3631) : null;
}

export function flagNames(bits: number | null, defs: FlagBit[]): string[] {
  if (!bits) return [];
  return defs.filter((f) => (bits >>> f.bit) & 1).map((f) => f.name);
}

const SATURATION = ["OVERFLOW", "NONLINEAR"];

/** Shape of the per-detector spectrum beyond 2 µm. A normal star falls with wavelength (F_ν ∝ λ⁻², so
 *  D5/D3 ≈ 0.25). A cold brown dwarf peaks in D5 (3.8–4.4 µm) and drops on both sides, methane at 3.3 µm
 *  and CO at 4.6 µm (ε Indi B: D3 19, D4 30, D5 47, D6 21 mJy). Warm dust keeps rising or flat into D6. */
function longShape(pts: ProbePoint[]): "peak4um" | "rising" | null {
  const at = (b: string) => pts.find((p) => p.band === b && p.snr >= 5);
  const d3 = at("D3");
  const d4 = pts.find((p) => p.band === "D4");
  const d5 = at("D5");
  const d6 = pts.find((p) => p.band === "D6");
  if (!d3 || !d5 || !d6 || d5.flux <= 1.8 * Math.max(d3.flux, 0)) return null;
  if (d6.flux < 0.75 * d5.flux && (!d4 || d5.flux > 1.2 * d4.flux)) return "peak4um";
  return "rising";
}

export interface Hint {
  kind: "motion" | "colour" | "variable" | "saturated" | "faint";
  text: string;
}

/**
 * Plain-language observations for the Explore mode. Each is stated only when the numbers support it.
 * `pmMasYr` is the catalogue proper motion when known; `year` the shown date.
 */
export function plainHints(r: ProbeResult, defs: FlagBit[], pmMasYr: number | null, year: number): Hint[] {
  const out: Hint[] = [];
  if (pmMasYr && pmMasYr >= 100) {
    const perYr = pmMasYr / 1000;
    const since = perYr * (year - 2000);
    out.push({
      kind: "motion",
      text: `Moves ${perYr >= 1 ? perYr.toFixed(1) : perYr.toFixed(2)}″ a year across the sky — about ${since >= 60 ? `${(since / 60).toFixed(1)}′` : `${Math.round(since)}″`} since 2000.`,
    });
  }
  // The flags plane is an OR over every exposure, so a saturation bit alone over-warns: require a filled
  // core (every exposure masked there) or a flux above the measured saturation onset (~0.1 Jy short, ~1 Jy long).
  const c = r.current;
  const names = flagNames(c?.flags ?? null, defs);
  const sat =
    names.includes("FILLED") ||
    (!!c && names.some((f) => SATURATION.includes(f)) && c.flux > (c.wl < 2.5 ? 100 : 800));
  if (sat) out.push({ kind: "saturated", text: "Too bright for SPHEREx here (the detector saturates), so this quick number is a lower limit. Analyze measures it from the unsaturated edges." });

  const good = r.spectrum.filter((p) => Number.isFinite(p.snr));
  const short = good.filter((p) => p.wl < 2.0);
  const long = good.filter((p) => p.wl > 3.8);
  if (short.length && long.length) {
    const fs = Math.max(...short.map((p) => p.flux));
    const fl = Math.max(...long.map((p) => p.flux));
    const lsnr = Math.max(...long.map((p) => p.snr));
    const ssnr = Math.max(...short.map((p) => p.snr));
    if (lsnr >= 5 && (ssnr < 3 || fl > 4 * Math.max(fs, 1e-6))) {
      out.push({ kind: "colour", text: "Bright at 4–5 µm but faint below 2 µm — the signature of something cold, like a brown dwarf." });
    } else if (longShape(good) === "peak4um") {
      out.push({ kind: "colour", text: "Peaks near 4 µm and fades on both sides (methane at 3.3 µm, CO at 4.6 µm): the pattern of a cold brown dwarf." });
    } else if (longShape(good) === "rising") {
      out.push({ kind: "colour", text: "Brighter at 4–5 µm than at 2 µm: warm dust around it, or a very cool object." });
    } else if (ssnr >= 5 && fs > 5 * Math.max(fl, 1e-6)) {
      out.push({ kind: "colour", text: "Much brighter at short wavelengths than at 4–5 µm — a normal, hotter star." });
    }
  }
  const lc = r.lightCurve.filter((p) => Number.isFinite(p.snr) && p.snr >= 5 && p.flux > 0);
  if (lc.length >= 2 && !sat) {
    const hi = lc.reduce((a, b) => (b.flux > a.flux ? b : a));
    const lo = lc.reduce((a, b) => (b.flux < a.flux ? b : a));
    const ratio = hi.flux / lo.flux;
    const sig = (hi.flux - lo.flux) / Math.hypot(hi.err, lo.err);
    if (ratio >= 1.3 && sig >= 5) {
      out.push({ kind: "variable", text: `Changed brightness: ${ratio.toFixed(1)}× brighter in ${hi.date} than in ${lo.date}.` });
    }
  }
  if (r.current && Number.isFinite(r.current.snr) && r.current.snr < 3) {
    out.push({ kind: "faint", text: "Nothing clearly here at this wavelength: maybe empty sky, or too faint." });
  }
  return out;
}
