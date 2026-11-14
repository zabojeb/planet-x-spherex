import type { CatalogSource, Grid } from "./types";

const D2R = Math.PI / 180;
export const MJD_J2000 = 51544.5;

export const mjdToYear = (mjd: number) => 2000 + (mjd - MJD_J2000) / 365.25;
export const mjdToDate = (mjd: number) => new Date(Date.UTC(1858, 10, 17) + mjd * 86400000);

export function formatDate(mjd: number, withTime = false): string {
  const iso = mjdToDate(mjd).toISOString();
  return withTime ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : iso.slice(0, 10);
}

/** "11 Mar → 10 Apr 2026" (the year is repeated only when it changes). */
export function formatRange(a: number, b: number): string {
  const da = mjdToDate(a);
  const db = mjdToDate(b);
  const day = (d: Date) => d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const sameYear = da.getUTCFullYear() === db.getUTCFullYear();
  return `${day(da)}${sameYear ? "" : ` ${da.getUTCFullYear()}`} → ${day(db)} ${db.getUTCFullYear()}`;
}

export function formatMonth(mjd: number): string {
  const d = mjdToDate(mjd);
  return d.toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
}

/** Position of a catalog source at a given Julian year (linear proper motion, pm in mas/yr, pmra with cos dec). */
export function propagate(src: { ra: number; dec: number; epoch: number; pmra: number | null; pmdec: number | null }, year: number): [number, number] {
  if (src.pmra == null || src.pmdec == null) return [src.ra, src.dec];
  const dt = year - src.epoch;
  const dec = src.dec + (src.pmdec * dt) / 3.6e6;
  const ra = src.ra + (src.pmra * dt) / 3.6e6 / Math.cos(dec * D2R);
  return [ra, dec];
}

export const totalPm = (s: Pick<CatalogSource, "pmra" | "pmdec">) =>
  s.pmra == null || s.pmdec == null ? 0 : Math.hypot(s.pmra, s.pmdec);

/** Gnomonic (TAN) projection onto the sequence grid: 0-based pixel coordinates, north up, east left. */
export function worldToPixel(grid: Grid, ra: number, dec: number): [number, number] {
  const a0 = grid.ra * D2R;
  const d0 = grid.dec * D2R;
  const a = ra * D2R;
  const d = dec * D2R;
  const cosc = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(a - a0);
  const xi = (Math.cos(d) * Math.sin(a - a0)) / cosc;
  const eta = (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(a - a0)) / cosc;
  const s = grid.pixscale_arcsec / 3600;
  const c = (grid.n - 1) / 2;
  return [c - xi / D2R / s, c + eta / D2R / s];
}

export function pixelToWorld(grid: Grid, x: number, y: number): [number, number] {
  const s = (grid.pixscale_arcsec / 3600) * D2R;
  const c = (grid.n - 1) / 2;
  const xi = -(x - c) * s;
  const eta = (y - c) * s;
  const a0 = grid.ra * D2R;
  const d0 = grid.dec * D2R;
  const rho = Math.hypot(xi, eta);
  if (rho === 0) return [grid.ra, grid.dec];
  const cang = Math.atan(rho);
  const dec = Math.asin(Math.cos(cang) * Math.sin(d0) + (eta * Math.sin(cang) * Math.cos(d0)) / rho);
  const ra = a0 + Math.atan2(xi * Math.sin(cang), rho * Math.cos(d0) * Math.cos(cang) - eta * Math.sin(d0) * Math.sin(cang));
  return [((ra / D2R) % 360 + 360) % 360, dec / D2R];
}

function sexagesimal(value: number, decimals: number): string {
  const sign = value < 0 ? -1 : 1;
  let v = Math.abs(value);
  let d = Math.floor(v);
  v = (v - d) * 60;
  let m = Math.floor(v);
  let s = (v - m) * 60;
  const p = 10 ** decimals;
  s = Math.round(s * p) / p;
  if (s >= 60) {
    s -= 60;
    m += 1;
  }
  if (m >= 60) {
    m -= 60;
    d += 1;
  }
  const ss = s.toFixed(decimals).padStart(decimals ? 3 + decimals : 2, "0");
  return `${sign < 0 ? "-" : ""}${String(d).padStart(2, "0")}:${String(m).padStart(2, "0")}:${ss}`;
}

export const formatRA = (deg: number) => sexagesimal(deg / 15, 2);
export const formatDec = (deg: number) => {
  const s = sexagesimal(deg, 1);
  return s.startsWith("-") ? s : `+${s}`;
};

export function formatPm(pmra: number, pmdec: number): string {
  const tot = Math.hypot(pmra, pmdec);
  if (tot < 1) return "—";
  return tot >= 1000 ? `${(tot / 1000).toFixed(2)}″/yr` : `${tot.toFixed(0)} mas/yr`;
}

export function formatBytes(n: number | undefined): string {
  if (!n) return "0";
  if (n > 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n > 1e6) return `${(n / 1e6).toFixed(0)} MB`;
  return `${(n / 1e3).toFixed(0)} kB`;
}
