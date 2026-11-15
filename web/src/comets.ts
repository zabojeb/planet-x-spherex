/** Comet discovery (ADR-0063): result types of the comet search and the known-comet watch, and the pure helpers
 *  the Comet hunt panel draws from (no runtime imports, so node --test checks them in comets.test.ts). The API
 *  calls live in api.ts. */

export type CometKind = "tracklet" | "slow" | "stack" | "object";
export type CometStatus = "known" | "unidentified" | "unverified";
type Stamp = (number | null)[][];

export interface CometCandidate {
  id: string;
  kind: CometKind;
  epoch: number;
  ra: number;
  dec: number;
  mjd: number;
  x: number;
  y: number;
  rate_arcsec_per_hr: number | null;
  pa_deg: number | null;
  n_exposures: number;
  bands?: string[];
  status: CometStatus;
  known?: { name: string; kind?: string } | null;
  comet_likeness: { score: number; log_odds: number; terms: Record<string, number>; why: string[] };
  coma: {
    verdict: string;
    coma_score: number | null;
    extent_sigma?: number;
    snr?: number | null;
    size?: { fwhm_arcsec: number | null; psf_fwhm_arcsec: number | null; intrinsic_fwhm_arcsec: number };
    tail?: { pa_deg: number; sigma: number; detected: boolean; aligned: boolean; antisolar_pa_deg?: number };
    stamp?: Stamp | null;
    psf_stamp?: Stamp | null;
  };
  spectrum: {
    verdict: string;
    gas_sigma: number | null;
    bands: { id: string; label: string; snr: number | null; detected: boolean }[];
    per_detector?: { band: string; wl: number; flux_mjy: number; err_mjy: number; n: number }[];
    points?: [number, number, number][];
  };
  orbit: { most_likely: string | null; comet_like: number; probabilities: Record<string, number>; constrained?: boolean } | null;
  path?: [number, number][];
  strip?: { mjd: number; band: string; image: Stamp | null }[];
  distance_au?: number;
  r_au?: number | null;
  delta_au?: number | null;
  dates?: [string, string];
  ecliptic_latitude?: number;
  elongation_deg?: number;
}

export interface CometSearch {
  id: string;
  kind: "comet_search";
  mode: "field" | "object";
  sequence: string;
  object?: string;
  counts?: { candidates: number; unidentified: number; comet_like: number; tracklet: number; slow: number; stack: number };
  summary?: { visits: number; max_score: number | null; extended_visits: number; gas_visits: number };
  candidates: { comets: CometCandidate[]; tracklets: unknown[] };
  caveats: string[];
}

export interface CometNow {
  name: string;
  designation: string;
  ra: number;
  dec: number;
  r_au: number;
  delta_au: number;
  elongation_deg: number;
  mag: number | null;
  pinned: boolean;
}

export interface WatchedComet {
  designation: string;
  name: string;
  stage?: string;
  sequence?: string;
  now?: CometNow;
  error?: string | null;
  comet_likeness?: number | null;
  latest?: { dates?: [string, string]; r_au?: number; afrho_cm?: number | null; co2_snr?: number | null } | null;
  activity?: { outbursts?: { epoch: number; ratio: number; delta_mag: number; sigma: number }[]; trend?: { k?: number } | null };
}

export interface CometWatch {
  updated: string | null;
  watched: WatchedComet[];
  now: CometNow[];
  refresh: { status: string; stage?: string; done?: number; total?: number; error?: string | null };
  pinned: string[];
  error?: string | null;
}

/** A score as a word for Explore mode, and the tone of its badge. */
export function scoreBand(score: number): { label: string; tone: "hot" | "warm" | "cold" } {
  if (score >= 70) return { label: "comet-like", tone: "hot" };
  if (score >= 30) return { label: "maybe", tone: "warm" };
  return { label: "asteroid-like", tone: "cold" };
}

/** One line of evidence for a row: coma, gas, orbit. */
export function evidenceLine(c: CometCandidate): string {
  const parts: string[] = [];
  const v = c.coma.verdict;
  if (v && v !== "no data") parts.push(v === "extended" || v === "possibly extended" ? `${v} ${(c.coma.extent_sigma ?? 0).toFixed(1)}σ` : v);
  if (c.coma.tail?.detected) parts.push(`tail PA ${c.coma.tail.pa_deg.toFixed(0)}°${c.coma.tail.aligned ? " ✓" : ""}`);
  const co2 = c.spectrum.bands.find((b) => b.id === "co2");
  if (co2?.snr != null && co2.snr >= 3) parts.push(`CO₂ ${co2.snr.toFixed(1)}σ`);
  const co = c.spectrum.bands.find((b) => b.id === "co");
  if (co?.snr != null && co.snr >= 3) parts.push(`CO ${co.snr.toFixed(1)}σ`);
  if (c.orbit?.most_likely) parts.push(`orbit ${c.orbit.most_likely}`);
  return parts.join(" · ");
}

export function motionLine(c: CometCandidate): string {
  if (c.rate_arcsec_per_hr == null) return c.kind;
  const kind = c.kind === "slow" ? "slow mover" : c.kind === "stack" ? "stacked" : c.kind === "object" ? `visit ${c.epoch + 1}` : "tracklet";
  return `${kind} · ${c.rate_arcsec_per_hr.toFixed(c.rate_arcsec_per_hr < 1 ? 2 : 1)}″/h PA ${(c.pa_deg ?? 0).toFixed(0)}°`;
}

/** Display range of a stamp (asinh-friendly): 2nd and 99.5th percentiles of its finite values. */
export function stampRange(img: Stamp): [number, number] {
  const v = img.flat().filter((x): x is number => x != null && Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return [0, 1];
  const q = (p: number) => v[Math.min(v.length - 1, Math.max(0, Math.round(p * (v.length - 1))))];
  const lo = q(0.02);
  const hi = q(0.995);
  return [lo, hi > lo ? hi : lo + 1e-9];
}

/** Grey levels 0..255 of a stamp, row-major, NaN as -1 (drawn transparent). */
export function stampPixels(img: Stamp): Int16Array {
  const [lo, hi] = stampRange(img);
  const s = Math.asinh((hi - lo) / ((hi - lo) * 0.1 || 1));
  const out = new Int16Array(img.length * (img[0]?.length ?? 0));
  let k = 0;
  for (const row of img)
    for (const x of row) {
      if (x == null || !Number.isFinite(x)) out[k++] = -1;
      else out[k++] = Math.round(255 * Math.min(1, Math.max(0, Math.asinh(Math.max(0, x - lo) / ((hi - lo) * 0.1 || 1)) / s)));
    }
  return out;
}

/** Polyline points of a spectrum in a w x h box (wavelength 0.7-5.1 um, flux scaled to the 2-98 % range). */
export function spectrumPath(points: [number, number, number][], w: number, h: number): string {
  if (!points.length) return "";
  const f = points.map((p) => p[1]).sort((a, b) => a - b);
  const lo = f[Math.floor(0.02 * (f.length - 1))];
  const hi = f[Math.ceil(0.98 * (f.length - 1))];
  const span = hi > lo ? hi - lo : 1;
  return points
    .map(([wl, fl]) => `${(((wl - 0.7) / 4.4) * w).toFixed(1)},${(h - 2 - ((Math.min(hi, Math.max(lo, fl)) - lo) / span) * (h - 4)).toFixed(1)}`)
    .join(" ");
}

export const wlToX = (wl: number, w: number) => ((wl - 0.7) / 4.4) * w;
