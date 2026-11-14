/**
 * Search suggestions, computed in the browser on every keystroke with no network:
 * - the local object index (GET /api/objects, ~4.5k objects from SIMBAD) and the challenge presets, ranked by
 *   exact / prefix / word-prefix / substring matches on names and aliases, then by how well studied they are;
 * - object types ("brown dwarf", "mira", "globular") as facets with a "random one of these" action;
 * - coordinates parsed here (decimal or sexagesimal), with the known objects near them;
 * - a typo-tolerant pass when nothing matches well.
 * Nothing is loaded for a suggestion until the user picks it. Names unknown to the index are looked up with
 * the API's resolver (CDS Sesame) only after a pause in typing, and only to show what the name is.
 *
 * normalize() and the key rules mirror backend/spherex_core/targets/objects.py.
 */
import { formatDec, formatRA } from "./astro";
import type { Target } from "./types";

const D2R = Math.PI / 180;

// ------------------------------------------------------------------ normalisation
const GREEK: Record<string, string> = {
  α: "alpha", β: "beta", γ: "gamma", δ: "delta", ε: "epsilon", ζ: "zeta", η: "eta", θ: "theta", ι: "iota",
  κ: "kappa", λ: "lambda", μ: "mu", ν: "nu", ξ: "xi", ο: "omicron", π: "pi", ρ: "rho", σ: "sigma", ς: "sigma",
  τ: "tau", υ: "upsilon", φ: "phi", χ: "chi", ψ: "psi", ω: "omega",
};
const SYNONYMS: Record<string, string> = { gliese: "gj", gl: "gj" };
const SURVEY_J = /^(wisea|wisepc|wisep|wise|cwise|2massw|2massi|2mass|sdss|denisp|denis|ulas|pso)j(?=\d)/;

/** Lower case, accents dropped, Greek letters spelled out, apostrophes removed, other punctuation -> space. */
export function normalize(text: string): string {
  let s = text.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");
  s = s.replace(/[α-ω]/g, (ch) => GREEK[ch] ?? ch).replace(/['’`]/g, "");
  return s
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean)
    .map((w) => SYNONYMS[w] ?? w)
    .join(" ");
}

function compactKeys(norm: string): string[] {
  const c = norm.replace(/ /g, "");
  const alt = c.replace(SURVEY_J, "$1");
  return alt === c ? [c] : [c, alt];
}

// ------------------------------------------------------------------ index
export interface SkyObject {
  name: string;
  aliases: string[];
  otype: string;
  /** Human-readable object type (SIMBAD's long name). */
  type: string;
  cat: string;
  ra: number;
  dec: number;
  pmra: number;
  pmdec: number;
  plx: number | null;
  vmag: number | null;
  kmag: number | null;
  sp: string | null;
  size: number | null;
  fov: number | null;
  refs: number;
  preset: string | null;
  /** For challenge presets: the API target with its view hints. */
  target?: Target;
  keys: Key[];
  pop: number;
}

interface Key {
  text: string;
  tokens: string[];
  compact: string[];
  letters: boolean;
}

export interface ObjectIndex {
  objects: SkyObject[];
  categories: Record<string, string>;
  types: Record<string, string>;
}

function makeKey(text: string): Key {
  const norm = normalize(text);
  return { text, tokens: norm.split(" "), compact: compactKeys(norm), letters: /^[a-z]{3}/.test(norm) };
}

/** Ranking bonus from how well studied an object is (SIMBAD references), with a nudge for Messier objects
 *  (what people mean by "orion" is M 42, not the Orion A cloud). Presets get a fixed bonus above any of these. */
const popularity = (refs: number, messier: boolean) => Math.min(12, 2.6 * Math.log10(refs + 1)) + (messier ? 3 : 0);
const PRESET_BONUS = 15;
const MESSIER = /^M \d+$/;

/** Parse the compact JSON served by /api/objects (fields + rows). */
export function parseIndex(doc: { fields: string[]; rows: unknown[][]; categories?: Record<string, string>; types?: Record<string, string> }): ObjectIndex {
  const f = Object.fromEntries(doc.fields.map((name, i) => [name, i]));
  const types = doc.types ?? {};
  const objects = doc.rows.map((r): SkyObject => {
    const name = r[f.name] as string;
    const aliases = (r[f.aliases] as string[]) ?? [];
    const refs = (r[f.refs] as number) ?? 0;
    const otype = r[f.otype] as string;
    return {
      name,
      aliases,
      otype,
      type: types[otype] ?? otype,
      cat: r[f.cat] as string,
      ra: r[f.ra] as number,
      dec: r[f.dec] as number,
      pmra: (r[f.pmra] as number) ?? 0,
      pmdec: (r[f.pmdec] as number) ?? 0,
      plx: r[f.plx] as number | null,
      vmag: r[f.vmag] as number | null,
      kmag: r[f.kmag] as number | null,
      sp: r[f.sp] as string | null,
      size: r[f.size] as number | null,
      fov: r[f.fov] as number | null,
      refs,
      preset: (r[f.preset] as string | null) ?? null,
      keys: [name, ...aliases].map(makeKey),
      pop: popularity(refs, MESSIER.test(name) || aliases.some((a) => MESSIER.test(a))),
    };
  });
  return { objects, categories: doc.categories ?? {}, types };
}

/** Objects to search: the index with its preset rows replaced by the presets themselves (their names,
 *  descriptions and view hints), and presets the index does not know added. */
export function searchSpace(index: ObjectIndex | null, presets: Target[]): SkyObject[] {
  const byPreset = new Map<string, SkyObject>();
  for (const o of index?.objects ?? []) if (o.preset) byPreset.set(o.preset, o);
  const fromPresets = presets.map((t): SkyObject => {
    const row = t.id ? byPreset.get(t.id) : undefined;
    const name = t.name ?? t.id ?? "Target";
    const aliases = [...(row && row.name !== name ? [row.name] : []), ...(row?.aliases ?? []), ...(t.id ? [t.id] : [])];
    return {
      name,
      aliases,
      otype: row?.otype ?? "",
      type: "Challenge target",
      cat: "challenge",
      ra: t.ra,
      dec: t.dec,
      pmra: t.pmra,
      pmdec: t.pmdec,
      plx: t.parallax ?? null,
      vmag: row?.vmag ?? null,
      kmag: row?.kmag ?? null,
      sp: row?.sp ?? null,
      size: null,
      fov: t.fov_arcmin ?? null,
      refs: row?.refs ?? 0,
      preset: t.id ?? null,
      target: t,
      keys: [name, ...aliases].map(makeKey),
      pop: PRESET_BONUS,
    };
  });
  return [...fromPresets, ...(index?.objects ?? []).filter((o) => !o.preset)];
}

// ------------------------------------------------------------------ coordinates
const SEXA_SEP = /[:hmsd°′″'"]/i;

function sexToNumber(parts: number[]): number {
  return parts[0] + (parts[1] ?? 0) / 60 + (parts[2] ?? 0) / 3600;
}

/** 'RA Dec' in decimal degrees or sexagesimal ('17:57:48.5 +04:41:36', '17h57m48.5s 4d41m36s',
 *  '17 57 48.5 +04 41 36'). RA in sexagesimal is hours. Returns null unless the whole text is a position. */
export function parseCoords(text: string): { ra: number; dec: number } | null {
  const t = text.trim().replace(/[−–—]/g, "-").replace(/,/g, " ").replace(/\s+/g, " ");
  if (!t || !/^[+\d]/.test(t)) return null;
  const dec2 = /^\+?(\d+(?:\.\d*)?)°?\s+([+-]?\d+(?:\.\d*)?)°?$/.exec(t);
  if (dec2) {
    const ra = Number(dec2[1]);
    const dec = Number(dec2[2]);
    return ra >= 0 && ra < 360 && Math.abs(dec) <= 90 ? { ra, dec } : null;
  }
  if (/[^0-9.:+\-\shmsd°′″'"]/i.test(t)) return null;
  // Split into RA and Dec at an explicit sign, else at the middle of the numbers.
  let raPart: string;
  let decPart: string;
  const signAt = t.slice(1).search(/[+-]/);
  if (signAt >= 0) {
    raPart = t.slice(0, signAt + 1);
    decPart = t.slice(signAt + 1);
  } else {
    const tokens = t.split(" ");
    if (tokens.length === 2) [raPart, decPart] = tokens;
    else if (tokens.length === 4 || tokens.length === 6) {
      raPart = tokens.slice(0, tokens.length / 2).join(" ");
      decPart = tokens.slice(tokens.length / 2).join(" ");
    } else return null;
  }
  const nums = (s: string) => (s.match(/\d+(?:\.\d*)?/g) ?? []).map(Number);
  const r = nums(raPart);
  const d = nums(decPart);
  if (!r.length || !d.length || r.length > 3 || d.length > 3) return null;
  if (!(SEXA_SEP.test(raPart) || r.length > 1 || d.length > 1)) return null;
  if (r.slice(1).some((x) => x >= 60) || d.slice(1).some((x) => x >= 60) || r[0] >= 24) return null;
  const ra = sexToNumber(r) * 15;
  const dec = (decPart.trim().startsWith("-") ? -1 : 1) * sexToNumber(d);
  return Math.abs(dec) <= 90 ? { ra, dec } : null;
}

/** Text that is on its way to being a position (digits, separators, a sign) but not one yet. */
export const looksLikeCoords = (text: string) => /^[+\d][\d\s.:+\-−,hmsd°′″'"]*$/i.test(text.trim());

/** Angular distance in degrees. */
export function separation(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const s = Math.sin(((dec2 - dec1) * D2R) / 2) ** 2 + Math.cos(dec1 * D2R) * Math.cos(dec2 * D2R) * Math.sin(((ra2 - ra1) * D2R) / 2) ** 2;
  return (2 * Math.asin(Math.min(1, Math.sqrt(s)))) / D2R;
}

/** Galactic latitude (deg) of an ICRS position: tells a crowded Milky Way field from a high-latitude one. */
export function galacticLatitude(ra: number, dec: number): number {
  const raNgp = 192.85948 * D2R;
  const decNgp = 27.12825 * D2R;
  const sinb = Math.sin(dec * D2R) * Math.sin(decNgp) + Math.cos(dec * D2R) * Math.cos(decNgp) * Math.cos(ra * D2R - raNgp);
  return Math.asin(sinb) / D2R;
}

export function nearby(space: SkyObject[], ra: number, dec: number, radiusDeg = 0.5, limit = 4): { obj: SkyObject; dist: number }[] {
  const out: { obj: SkyObject; dist: number }[] = [];
  for (const obj of space) {
    if (Math.abs(obj.dec - dec) > radiusDeg) continue;
    const dist = separation(ra, dec, obj.ra, obj.dec);
    if (dist <= radiusDeg) out.push({ obj, dist });
  }
  return out.sort((a, b) => a.dist - b.dist).slice(0, limit);
}

// ------------------------------------------------------------------ facets (object types)
export interface Facet {
  id: string;
  label: string;
  /** "a brown dwarf", for "Random a brown dwarf" -> "Random brown dwarf". */
  one: string;
  terms: string[];
  test: (o: SkyObject) => boolean;
}

const ofType = (...t: string[]) => (o: SkyObject) => t.includes(o.otype);
const ofCat = (c: string) => (o: SkyObject) => o.cat === c;

export const FACETS: Facet[] = [
  { id: "brown-dwarf", label: "Brown dwarfs", one: "brown dwarf", terms: ["brown dwarf", "brown dwarfs", "substellar", "l dwarf", "t dwarf", "y dwarf"], test: ofCat("brown-dwarf") },
  { id: "high-pm", label: "Fast-moving stars", one: "fast-moving star", terms: ["fast moving", "fast stars", "high proper motion", "proper motion", "moving stars", "movers"], test: ofCat("high-pm") },
  { id: "nearby", label: "Stars within 10 pc", one: "nearby star", terms: ["nearby stars", "nearest stars", "closest stars", "neighbours", "neighbors"], test: (o) => (o.plx ?? 0) > 100 },
  { id: "mira", label: "Mira variables", one: "Mira variable", terms: ["mira variables", "miras", "mira type"], test: ofType("Mi*") },
  { id: "cepheid", label: "Cepheids", one: "Cepheid", terms: ["cepheids", "cepheid variables"], test: ofType("cC*", "Ce*", "WV*") },
  { id: "rr-lyrae", label: "RR Lyrae stars", one: "RR Lyrae star", terms: ["rr lyrae"], test: ofType("RR*") },
  { id: "variable", label: "Variable stars", one: "variable star", terms: ["variable stars", "variables", "pulsating stars"], test: ofCat("variable") },
  { id: "young", label: "Young stars", one: "young star", terms: ["young stars", "t tauri stars", "yso", "young stellar objects", "protostars", "herbig ae be"], test: ofCat("young") },
  { id: "bright", label: "Bright stars", one: "bright star", terms: ["bright stars", "naked eye stars"], test: ofCat("bright") },
  { id: "agn", label: "Active galaxies and quasars", one: "active galaxy", terms: ["quasars", "qso", "active galaxies", "agn", "seyfert galaxies", "blazars"], test: ofType("AGN", "QSO", "Sy1", "Sy2", "SyG", "BLL", "Bla", "LIN", "rG") },
  { id: "galaxy", label: "Galaxies", one: "galaxy", terms: ["galaxy", "galaxies"], test: ofCat("galaxy") },
  { id: "globular", label: "Globular clusters", one: "globular cluster", terms: ["globular clusters", "globulars"], test: ofType("GlC") },
  { id: "open-cluster", label: "Open clusters", one: "open cluster", terms: ["open clusters"], test: ofType("OpC") },
  { id: "cluster", label: "Star clusters", one: "star cluster", terms: ["star clusters", "clusters"], test: ofCat("cluster") },
  { id: "pn", label: "Planetary nebulae", one: "planetary nebula", terms: ["planetary nebulae", "planetary nebulas"], test: ofType("PN") },
  { id: "snr", label: "Supernova remnants", one: "supernova remnant", terms: ["supernova remnants", "snr"], test: ofType("SNR", "SR?") },
  { id: "sfr", label: "Star-forming regions", one: "star-forming region", terms: ["star forming regions", "hii regions", "molecular clouds", "dark clouds"], test: ofType("HII", "SFR", "MoC", "DNe", "Cld") },
  { id: "nebula", label: "Nebulae", one: "nebula", terms: ["nebula", "nebulae", "nebulas"], test: ofCat("nebula") },
  { id: "pulsar", label: "Pulsars", one: "pulsar", terms: ["pulsars"], test: ofType("Psr") },
  { id: "white-dwarf", label: "White dwarfs", one: "white dwarf", terms: ["white dwarfs"], test: ofType("WD*") },
];

/** The facet a query names: a term starting with the query (3+ letters), or the query starting with a term. */
export function matchFacet(query: string): Facet | null {
  const q = normalize(query);
  if (q.length < 3 || /\d/.test(q)) return null;
  for (const f of FACETS) if (f.terms.some((t) => t === q || q.startsWith(t + " "))) return f;
  for (const f of FACETS) if (f.terms.some((t) => t.startsWith(q))) return f;
  return null;
}

// ------------------------------------------------------------------ ranking
export interface Match {
  obj: SkyObject;
  score: number;
  /** The alias that matched, when it is not the name (shown as "also …"). */
  via: string | null;
  /** The matched text (name or alias), for inline completion. */
  text: string;
  fuzzy?: boolean;
}

interface Query {
  tokens: string[];
  compact: string;
}

function keyScore(q: Query, k: Key): number {
  let best = 0;
  for (const c of k.compact) {
    if (c === q.compact) return 100;
    if (c.startsWith(q.compact)) best = Math.max(best, 88 - Math.min(12, (c.length - q.compact.length) / 2));
  }
  if (best) return best;
  if (q.tokens.every((t) => k.tokens.some((w) => w.startsWith(t)))) return k.tokens[0].startsWith(q.tokens[0]) ? 66 : 60;
  if (q.compact.length >= 3 && k.compact.some((c) => c.includes(q.compact))) return 40;
  return 0;
}

/** Damerau–Levenshtein distance between a and the best prefix of b (b[0..a.length±1]), capped at max+1. */
function prefixDistance(a: string, b: string, max: number): number {
  const n = a.length;
  const m = Math.min(b.length, n + max);
  let prev2: number[] = [];
  let prev = Array.from({ length: m + 1 }, (_, j) => j);
  let best = Infinity;
  for (let i = 1; i <= n; i++) {
    const cur = [i];
    let rowMin = i;
    for (let j = 1; j <= m; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, prev2[j - 2] + 1);
      cur.push(v);
      rowMin = Math.min(rowMin, v);
    }
    if (rowMin > max) return max + 1;
    prev2 = prev;
    prev = cur;
  }
  for (let j = Math.max(0, n - max); j <= m; j++) best = Math.min(best, prev[j]);
  return best;
}

/** Ranked objects for a query. Empty for an empty query. */
export function rankObjects(space: SkyObject[], query: string, limit = 8): Match[] {
  const norm = normalize(query);
  if (!norm) return [];
  const q: Query = { tokens: norm.split(" "), compact: norm.replace(/ /g, "") };
  const out: Match[] = [];
  for (const obj of space) {
    let best = 0;
    let at = 0;
    for (let i = 0; i < obj.keys.length; i++) {
      const s = keyScore(q, obj.keys[i]) + (i === 0 ? 4 : 0); // the name a little ahead of its aliases
      if (s > best) {
        best = s;
        at = i;
      }
    }
    if (best > 4) out.push({ obj, score: best + obj.pop, via: at === 0 ? null : obj.keys[at].text, text: obj.keys[at].text });
  }
  out.sort((a, b) => b.score - a.score || a.obj.name.length - b.obj.name.length);
  // Nothing close: try names within one or two typos (only names that start with letters).
  if ((!out.length || out[0].score - out[0].obj.pop < 60) && q.compact.length >= 4 && /^[a-z]/.test(q.compact)) {
    const max = q.compact.length >= 7 ? 2 : 1;
    // Typos are forgiven in letters only: HD 12345 is not a misspelling of HD 32450.
    const digits = q.compact.replace(/\D/g, "");
    const seen = new Set(out.map((m) => m.obj));
    const fuzzy: Match[] = [];
    for (const obj of space) {
      if (seen.has(obj)) continue;
      let best = max + 1;
      let at = 0;
      for (let i = 0; i < Math.min(obj.keys.length, 4); i++) {
        const k = obj.keys[i];
        // Cheap filter: a typo rarely hits both of the first two letters.
        if (!k.letters || (k.compact[0][0] !== q.compact[0] && k.compact[0][1] !== q.compact[1])) continue;
        if (digits && !k.compact[0].replace(/\D/g, "").startsWith(digits)) continue;
        const d = prefixDistance(q.compact, k.compact[0], max);
        if (d < best) {
          best = d;
          at = i;
        }
      }
      if (best <= max) fuzzy.push({ obj, score: 50 - 12 * best + obj.pop, via: at === 0 ? null : obj.keys[at].text, text: obj.keys[at].text, fuzzy: true });
    }
    out.push(...fuzzy);
    out.sort((a, b) => b.score - a.score);
  }
  return out.slice(0, limit);
}

export function facetObjects(space: SkyObject[], facet: Facet, limit: number): SkyObject[] {
  return space
    .filter(facet.test)
    .sort((a, b) => b.refs - a.refs)
    .slice(0, limit);
}

// ------------------------------------------------------------------ targets, descriptions, random
export function describe(o: SkyObject): string {
  if (o.target?.description) return o.target.description;
  const parts = [o.type];
  if (o.sp) parts.push(o.sp);
  if (o.plx && o.plx > 1) parts.push(o.plx >= 10 ? `${(1000 / o.plx).toFixed(1)} pc` : `${Math.round(1000 / o.plx)} pc`);
  const pm = Math.hypot(o.pmra, o.pmdec);
  if (pm >= 100) parts.push(`${(pm / 1000).toFixed(2)}″/yr`);
  if (o.size) parts.push(`${o.size}′`);
  return parts.join(" · ");
}

/** What picking a suggestion opens: a preset (with its view hints) or a position with motion. */
export type Pick = { kind: "preset"; id: string } | { kind: "target"; target: Target };

export function pickFor(o: SkyObject): Pick {
  if (o.preset) return { kind: "preset", id: o.preset };
  return {
    kind: "target",
    target: {
      name: o.name,
      ra: o.ra,
      dec: o.dec,
      pmra: o.pmra,
      pmdec: o.pmdec,
      ref_epoch: 2000,
      parallax: o.plx,
      source: "SIMBAD (local index)",
      description: describe(o),
      fov_arcmin: o.fov,
    },
  };
}

export function coordsName(ra: number, dec: number): string {
  return `${formatRA(ra).replace(/\.\d+$/, "")} ${formatDec(dec).replace(/\.\d+$/, "")}`;
}

export function pickCoords(ra: number, dec: number, description = "Position entered by hand"): Pick {
  return { kind: "target", target: { name: coordsName(ra, dec), ra, dec, pmra: 0, pmdec: 0, ref_epoch: 2000, source: "user", description } };
}

/** Uniform on the sphere: every patch of sky is equally likely (SPHEREx covers all of it). */
export function randomField(rand = Math.random): Pick {
  const ra = rand() * 360;
  const dec = Math.asin(2 * rand() - 1) / D2R;
  const b = galacticLatitude(ra, dec);
  const where = Math.abs(b) < 10 ? "in the Milky Way plane, crowded with stars" : Math.abs(b) > 45 ? "far from the Milky Way plane, where galaxies show" : "at mid galactic latitude";
  return pickCoords(ra, dec, `Random patch of sky ${where} (b = ${b >= 0 ? "+" : "−"}${Math.abs(b).toFixed(0)}°)`);
}

// Categories the Random button draws from, and how often: movers and variables are what SPHEREx's
// time series shows best, so they come up more often than bright stars.
const RANDOM_WEIGHTS: Record<string, number> = {
  "brown-dwarf": 3, "high-pm": 3, variable: 2.5, young: 2, nearby: 1.5, galaxy: 2, nebula: 2, cluster: 1.5,
  bright: 1, star: 0.5, challenge: 1,
};

/** A random object: a weighted category first, then an object in it (better-studied ones a little more likely). */
export function randomObject(space: SkyObject[], facet: Facet | null = null, avoid?: string, rand = Math.random): SkyObject | null {
  let pool = facet ? space.filter(facet.test) : space;
  if (!facet) {
    const cats = [...new Set(pool.map((o) => o.cat))].filter((c) => RANDOM_WEIGHTS[c]);
    if (!cats.length) return null;
    const total = cats.reduce((s, c) => s + RANDOM_WEIGHTS[c], 0);
    let x = rand() * total;
    const cat = cats.find((c) => (x -= RANDOM_WEIGHTS[c]) <= 0) ?? cats[cats.length - 1];
    pool = pool.filter((o) => o.cat === cat);
  }
  pool = pool.filter((o) => o.name !== avoid);
  if (!pool.length) return null;
  const weights = pool.map((o) => Math.sqrt(o.refs + 1));
  let x = rand() * weights.reduce((s, w) => s + w, 0);
  return pool.find((_, i) => (x -= weights[i]) <= 0) ?? pool[pool.length - 1];
}

// ------------------------------------------------------------------ recent picks
const RECENT_KEY = "spherex-recent-v1";

export interface Recent {
  label: string;
  sub: string;
  pick: Pick;
}

export function loadRecents(): Recent[] {
  try {
    const v = JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(v) ? v.slice(0, 6) : [];
  } catch {
    return [];
  }
}

export function saveRecent(r: Recent): Recent[] {
  const key = (x: Recent) => (x.pick.kind === "preset" ? `p:${x.pick.id}` : `t:${x.pick.target.ra.toFixed(4)},${x.pick.target.dec.toFixed(4)}`);
  const next = [r, ...loadRecents().filter((x) => key(x) !== key(r))].slice(0, 6);
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* private mode: recents just are not kept */
  }
  return next;
}

// ------------------------------------------------------------------ solar-system objects
/** Comet and asteroid designations that cannot be stars: 3I, 1P/Halley, C/2025 N1, 2024 YR4, (433) Eros. */
const SMALL_BODY = [
  /^\d+\s?[IP](\/.*)?$/i, // interstellar / periodic comet numbers: 3I, 1P, 2I/Borisov
  /^[CPDXI]\/\s?\d{4}\s?[A-Z]{1,2}\d*/i, // comet designations: C/2025 N1
  /^\d{4}\s?[A-Z]{2}\d{0,3}$/, // provisional asteroid designations: 2024 YR4
  /^\(\d+\)\s*\S*/, // (433) Eros
  /^\d{1,6}\s+[A-Z][a-z]{2,}$/, // 433 Eros, 16 Psyche
];
const SOLAR_TERMS = ["solar system", "asteroid", "asteroids", "comet", "comets", "minor planet", "minor planets", "interstellar"];

/** "strong": the text is a small-body designation; "term": it asks for the solar system in general. */
export function solarSystemQuery(text: string): "strong" | "term" | null {
  const q = text.trim();
  if (!q) return null;
  if (SMALL_BODY.some((re) => re.test(q))) return "strong";
  const n = normalize(q);
  if (n.length >= 4 && SOLAR_TERMS.some((t) => t.startsWith(n) || n === t)) return "term";
  return null;
}

/** Name for the moving-object search: "(433) Eros" -> "Eros"-style names are passed as typed, minus prefixes. */
export function smallBodyName(text: string): string {
  return text.trim().replace(/^(asteroid|comet)\s+/i, "").replace(/^\((\d+)\)\s*/, "$1 ");
}

export const SOLAR_EXAMPLES = [
  { name: "3I", label: "3I/ATLAS", sub: "Interstellar comet, 2025" },
  { name: "Eros", label: "433 Eros", sub: "Near-Earth asteroid" },
  { name: "Psyche", label: "16 Psyche", sub: "Metal-rich main-belt asteroid" },
];
