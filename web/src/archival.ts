/** Long baseline: pure helpers to blink 2MASS (≈2000), AllWISE (2010) and NEOWISE (2014–2024) images with the
 *  SPHEREx epochs. Archival frames come on a concentric grid at the same pixel scale (possibly wider), so a
 *  SPHEREx image is embedded in it with a fixed pixel offset. No network here (see api.ts). */
import { totalPm } from "./astro";
import type { ArchivalFrame, Grid, Target } from "./types";

export const TWOMASS_BANDS = ["J", "H", "Ks"] as const;
export const WISE_BANDS = ["W1", "W2"] as const;
export type ArchBands = [string, string]; // [2MASS band, WISE band]
export const DEFAULT_ARCH_BANDS: ArchBands = ["Ks", "W1"];

/** Field needed so the target stays in view back to 1999 (null = the sequence's own grid is enough). */
export function archivalFov(target: Pick<Target, "pmra" | "pmdec"> | null, gridFov: number): number | null {
  if (!target) return null;
  const pm = totalPm({ pmra: target.pmra, pmdec: target.pmdec }); // mas/yr
  const shift = (pm / 1000) * (2026.5 - 1999) / 60; // arcmin travelled since 2MASS
  if (shift < 0.4) return null;
  const fov = Math.min(60, Math.max(gridFov, 2 * (shift + 1.2)));
  return fov > gridFov + 0.05 ? Math.round(fov * 10) / 10 : null;
}

export const isTwoMass = (f: ArchivalFrame) => f.survey_id === "2mass";

/** The frames shown for a band choice, in time order. */
export function selectFrames(frames: ArchivalFrame[], bands: ArchBands): ArchivalFrame[] {
  return frames.filter((f) => (isTwoMass(f) ? f.band === bands[0] : f.band === bands[1])).sort((a, b) => a.mjd - b.mjd);
}

/** Frames of one survey and band share a stretch (their units and resolutions are alike). */
export const stretchGroup = (f: ArchivalFrame) => `${f.survey_id}|${f.band}`;

/** Pixel offset of an n-grid inside a concentric N-grid with the same pixel scale. */
export function gridOffset(outer: Grid, inner: Grid): number {
  return Math.round((outer.n - inner.n) / 2);
}

/** Copy an [n, n] image into the middle of an [N, N] one (-Infinity = outside, painted as the stage). */
export function embed(img: ArrayLike<number>, n: number, N: number, off: number): Float32Array {
  const out = new Float32Array(N * N).fill(-Infinity);
  for (let y = 0; y < n; y++) {
    const oy = y + off;
    if (oy < 0 || oy >= N) continue;
    for (let x = 0; x < n; x++) {
      const ox = x + off;
      if (ox >= 0 && ox < N) out[oy * N + ox] = img[y * n + x];
    }
  }
  return out;
}

export function parseArchBands(s: string | null): ArchBands | undefined {
  if (!s) return undefined;
  const [a, b] = s.split(",");
  return (TWOMASS_BANDS as readonly string[]).includes(a) && (WISE_BANDS as readonly string[]).includes(b) ? [a, b] : undefined;
}

/** One-line label for a frame: "2MASS Ks · 2000-05-28". */
export const frameLabel = (f: ArchivalFrame) => `${f.survey} ${f.band} · ${f.date.slice(0, 10)}`;
