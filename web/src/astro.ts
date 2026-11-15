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

/** Gnomonic (TAN) projection around (ra0, dec0): standard coordinates xi (east), eta (north) in degrees. */
export function worldToTan(ra0: number, dec0: number, ra: number, dec: number): [number, number] {
  const a0 = ra0 * D2R;
  const d0 = dec0 * D2R;
  const a = ra * D2R;
  const d = dec * D2R;
  const cosc = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(a - a0);
  const xi = (Math.cos(d) * Math.sin(a - a0)) / cosc;
  const eta = (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(a - a0)) / cosc;
  return [xi / D2R, eta / D2R];
}

/** Inverse of worldToTan: the sky position xi degrees east and eta degrees north of (ra0, dec0) on the tangent plane. */
export function tanToWorld(ra0: number, dec0: number, xiDeg: number, etaDeg: number): [number, number] {
  const xi = xiDeg * D2R;
  const eta = etaDeg * D2R;
  const a0 = ra0 * D2R;
  const d0 = dec0 * D2R;
  const rho = Math.hypot(xi, eta);
  if (rho === 0) return [ra0, dec0];
  const cang = Math.atan(rho);
  const dec = Math.asin(Math.cos(cang) * Math.sin(d0) + (eta * Math.sin(cang) * Math.cos(d0)) / rho);
  const ra = a0 + Math.atan2(xi * Math.sin(cang), rho * Math.cos(d0) * Math.cos(cang) - eta * Math.sin(d0) * Math.sin(cang));
  return [normRa(ra / D2R), dec / D2R];
}

/** Gnomonic (TAN) projection onto the sequence grid: 0-based pixel coordinates, north up, east left. */
export function worldToPixel(grid: Grid, ra: number, dec: number): [number, number] {
  const [xi, eta] = worldToTan(grid.ra, grid.dec, ra, dec);
  const s = grid.pixscale_arcsec / 3600;
  const c = (grid.n - 1) / 2;
  return [c - xi / s, c + eta / s];
}

export function pixelToWorld(grid: Grid, x: number, y: number): [number, number] {
  const s = grid.pixscale_arcsec / 3600;
  const c = (grid.n - 1) / 2;
  return tanToWorld(grid.ra, grid.dec, -(x - c) * s, (y - c) * s);
}

export const normRa = (ra: number) => ((ra % 360) + 360) % 360;

// ---------------------------------------------------------------- frames, separations

/** ICRS -> Galactic rotation (Hipparcos, ESA 1997 vol. 1 §1.5.3); its transpose is the inverse. */
const GAL = [
  [-0.0548755604162154, -0.873437090234885, -0.4838350155487132],
  [0.4941094278755837, -0.4448296299600112, 0.7469822444972189],
  [-0.8676661490190047, -0.1980763734312015, 0.4559837761750669],
];
const OBLIQUITY = 23.4392911 * D2R; // J2000 mean obliquity

const unit = (lon: number, lat: number): [number, number, number] => [
  Math.cos(lat * D2R) * Math.cos(lon * D2R),
  Math.cos(lat * D2R) * Math.sin(lon * D2R),
  Math.sin(lat * D2R),
];
const sph = (v: [number, number, number]): [number, number] => [normRa(Math.atan2(v[1], v[0]) / D2R), Math.asin(Math.max(-1, Math.min(1, v[2]))) / D2R];

export function toGalactic(ra: number, dec: number): [number, number] {
  const v = unit(ra, dec);
  return sph([0, 1, 2].map((i) => GAL[i][0] * v[0] + GAL[i][1] * v[1] + GAL[i][2] * v[2]) as [number, number, number]);
}

export function fromGalactic(l: number, b: number): [number, number] {
  const v = unit(l, b);
  return sph([0, 1, 2].map((i) => GAL[0][i] * v[0] + GAL[1][i] * v[1] + GAL[2][i] * v[2]) as [number, number, number]);
}

export function toEcliptic(ra: number, dec: number): [number, number] {
  const [x, y, z] = unit(ra, dec);
  const c = Math.cos(OBLIQUITY);
  const s = Math.sin(OBLIQUITY);
  return sph([x, c * y + s * z, -s * y + c * z]);
}

export function fromEcliptic(lon: number, lat: number): [number, number] {
  const [x, y, z] = unit(lon, lat);
  const c = Math.cos(OBLIQUITY);
  const s = Math.sin(OBLIQUITY);
  return sph([x, c * y - s * z, s * y + c * z]);
}

/** Angular distance in degrees (haversine, accurate at small separations). */
export function separation(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const d1 = dec1 * D2R;
  const d2 = dec2 * D2R;
  const h = Math.sin((d2 - d1) / 2) ** 2 + Math.cos(d1) * Math.cos(d2) * Math.sin(((ra2 - ra1) * D2R) / 2) ** 2;
  return (2 * Math.asin(Math.min(1, Math.sqrt(h)))) / D2R;
}

/** Position angle of point 2 seen from point 1, degrees east of north (0–360). */
export function positionAngle(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const d1 = dec1 * D2R;
  const d2 = dec2 * D2R;
  const da = (ra2 - ra1) * D2R;
  const pa = Math.atan2(Math.sin(da), Math.cos(d1) * Math.tan(d2) - Math.sin(d1) * Math.cos(da)) / D2R;
  return (pa + 360) % 360;
}

const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
export const compassPoint = (pa: number) => COMPASS[Math.round(pa / 45) % 8];

/** "12.3″", "4.56′", "1.20°" */
export function formatAngle(deg: number): string {
  const a = Math.abs(deg);
  if (a < 1 / 60) return `${(a * 3600).toFixed(a * 3600 < 10 ? 2 : 1)}″`;
  if (a < 1) return `${(a * 60).toFixed(2)}′`;
  return `${a.toFixed(2)}°`;
}

export const formatDeg = (deg: number, decimals = 5, signed = false) => `${signed && deg >= 0 ? "+" : ""}${deg.toFixed(decimals)}°`;

/** Distance from a parallax in mas: "1.83 pc · 5.96 ly", null when the parallax is missing or not positive. */
export function formatDistance(plxMas: number | null | undefined): string | null {
  if (plxMas == null || !(plxMas > 0)) return null;
  const pc = 1000 / plxMas;
  const ly = pc * 3.26156;
  const f = (v: number) => (v >= 100 ? v.toFixed(0) : v.toPrecision(3));
  return `${f(pc)} pc · ${f(ly)} ly`;
}

// ---------------------------------------------------------------- parsing user coordinates

export type CoordFrame = "icrs" | "galactic";
export type ParsedCoords = { ok: true; ra: number; dec: number; frame: CoordFrame; sexagesimal: boolean } | { ok: false; error: string };

const NUM = /[+-]?(?:\d+(?:\.\d*)?|\.\d+)/g;

/**
 * Coordinates typed by a person, or null when the text is not coordinates at all (e.g. an object name).
 * ICRS: "269.452 4.693", "17:57:48.5 +04:41:36", "17h57m48.5s +4d41m36s", "17 57 48.5 +04 41 36",
 * "+4°41′36″", "J175748.50+044136.2". Galactic: "l=31.0 b=14.1", "gal 31.0 14.1", or frame = "galactic".
 */
export function parseCoordinates(input: string, frame: CoordFrame = "icrs"): ParsedCoords | null {
  let t = input.trim().replace(/[\u2212\u2013\u2014]/g, "-").replace(/,/g, " ");
  if (!t) return null;
  const jname = t.match(/^J?(\d{2})(\d{2})(\d{2}(?:\.\d+)?)([+-])(\d{2})(\d{2})(\d{2}(?:\.\d+)?)$/i);
  if (jname) {
    const [, h, m, sec, sign, d, dm, ds] = jname;
    return fromParts([Number(h), Number(m), Number(sec)], sign, [Number(d), Number(dm), Number(ds)], true);
  }
  if (/^(gal(actic)?\b|l\s*[=:])/i.test(t)) {
    frame = "galactic";
    t = t.replace(/^gal(actic)?\b/i, " ");
  }
  t = t.replace(/^icrs\b|^j2000\b/i, " ").replace(/\b(ra|dec|l|b)\s*[=:]/gi, " ").replace(/[αδ]\s*[=:]?/g, " ");
  const hours = /\d\s*h/i.test(t) || /\d:\d/.test(t);
  // anything left that is not a number or a separator means this is a name, not coordinates
  if (/[a-z]/i.test(t.replace(/(\d)\s*[hdms](?![a-z])/gi, "$1 "))) return null;
  const toks = t.replace(/[hdms°′″'":]/gi, " ").match(NUM) ?? [];
  if (toks.length < 2) return null;
  if (frame === "galactic") {
    if (toks.length !== 2) return { ok: false, error: "Galactic coordinates are two numbers in degrees: l b" };
    const l = Number(toks[0]);
    const b = Number(toks[1]);
    if (!(l >= 0 && l < 360) || !(b >= -90 && b <= 90)) return { ok: false, error: "l must be 0–360° and b −90…+90°" };
    const [ra, dec] = fromGalactic(l, b);
    return { ok: true, ra, dec, frame, sexagesimal: false };
  }
  if (toks.length === 2 && !hours) {
    const ra = Number(toks[0]);
    const dec = Number(toks[1]);
    if (!(ra >= 0 && ra < 360)) return { ok: false, error: "RA in degrees must be 0–360" };
    if (!(dec >= -90 && dec <= 90)) return { ok: false, error: "Dec must be −90…+90°" };
    return { ok: true, ra, dec, frame, sexagesimal: false };
  }
  // split RA from Dec at the first explicit sign after the first token, else in the middle
  let k = toks.findIndex((x, i) => i > 0 && /^[+-]/.test(x));
  if (k < 0) {
    if (toks.length % 2) return { ok: false, error: "Could not tell where RA ends and Dec starts: add a sign to Dec (+ or −)" };
    k = toks.length / 2;
  }
  const raT = toks.slice(0, k);
  const decT = toks.slice(k);
  if (raT.length > 3 || decT.length > 3) return { ok: false, error: "Too many numbers: use h m s and ° ′ ″" };
  const sign = decT[0].startsWith("-") ? "-" : "+";
  return fromParts(raT.map(Number), sign, decT.map((x) => Math.abs(Number(x))), hours || raT.length > 1);
}

function fromParts(raP: number[], sign: string, decP: number[], raHours: boolean): ParsedCoords {
  const sexa = (p: number[]) => p.reduce((acc, v, i) => acc + v / 60 ** i, 0);
  if (raP.slice(1).some((v) => v >= 60) || decP.slice(1).some((v) => v >= 60)) return { ok: false, error: "Minutes and seconds must be below 60" };
  if (raP.slice(0, -1).some((v) => !Number.isInteger(v)) || decP.slice(0, -1).some((v) => !Number.isInteger(v)))
    return { ok: false, error: "Only the last part of a sexagesimal value may have decimals" };
  const ra = raHours ? sexa(raP) * 15 : sexa(raP);
  const dec = (sign === "-" ? -1 : 1) * sexa(decP);
  if (!(ra >= 0 && ra < 360)) return { ok: false, error: raHours ? "RA must be 0–24 h" : "RA must be 0–360°" };
  if (!(dec >= -90 && dec <= 90)) return { ok: false, error: "Dec must be −90…+90°" };
  return { ok: true, ra, dec, frame: "icrs", sexagesimal: raHours };
}

// ---------------------------------------------------------------- coordinate grid

/** Grid spacings: Dec in arcseconds of arc, RA in seconds of time. */
const DEC_STEPS = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 18000, 36000, 54000, 108000];
const RA_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 10800];

/** The finest spacing that keeps at most `maxLines` lines across `spanDeg`. RA steps are in seconds of time. */
export function gridStep(spanDeg: number, kind: "ra" | "dec", maxLines = 5): number {
  const steps = kind === "dec" ? DEC_STEPS : RA_STEPS;
  const toDeg = kind === "dec" ? 1 / 3600 : 1 / 240;
  return steps.find((st) => spanDeg / (st * toDeg) <= maxLines) ?? steps[steps.length - 1];
}

/** Grid label for a line: precision follows the spacing ("17h57m", "17h57m48s", "+04°41′", "+04°41′30″"). */
export function gridLabel(value: number, kind: "ra" | "dec", step: number): string {
  if (kind === "ra") {
    const totalSec = Math.round(((normRa(value) / 15) * 3600) / Math.min(step, 1)) * Math.min(step, 1);
    const h = Math.floor(totalSec / 3600) % 24;
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    const pad = (v: number) => String(v).padStart(2, "0");
    if (step >= 3600) return `${pad(h)}h`;
    if (step >= 60) return `${pad(h)}h${pad(m)}m`;
    return `${pad(h)}h${pad(m)}m${step < 1 ? s.toFixed(1).padStart(4, "0") : pad(Math.round(s))}s`;
  }
  const sign = value < 0 ? "−" : "+";
  const total = Math.round(Math.abs(value) * 3600);
  const d = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (v: number) => String(v).padStart(2, "0");
  if (step >= 3600) return `${sign}${pad(d)}°`;
  if (step >= 60) return `${sign}${pad(d)}°${pad(m)}′`;
  return `${sign}${pad(d)}°${pad(m)}′${pad(s)}″`;
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
