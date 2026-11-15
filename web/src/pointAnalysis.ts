/**
 * Point analysis for the analysis window: run it, and turn the result into what the window shows.
 *
 * runPointAnalysis asks the server (POST /api/sequences/{id}/analyze, a polled job, ADR-0053) and turns its
 * result into the slots the window reads (fromServer). A server without that endpoint gets the same slots
 * assembled here from the endpoints it has: motion from co-add centroids, forced photometry in every exposure
 * (following that motion when it is significant), the spectral analysis of that photometry, a periodogram
 * and catalogue counterparts. Everything measured comes from SPHEREx; the catalogues are only the
 * yardstick, compared in catalogChecks.
 */
import { ApiError, analyzePoint, analyzeSpectrum, fetchCatalog, fetchMotion, fetchPeriodogram, measureSource, type Progress } from "./api";
import { propagate, separation } from "./astro";
import type { CatalogMatch, CatalogSource, ClassifyResult, Meta, MotionFit, PeriodogramResult, Photometry, PointAnalysis, Slot, SpectrumResult, VariabilityResult } from "./types";

export interface AnalysisPos {
  ra: number;
  dec: number;
  pmra?: number;
  pmdec?: number;
  ref_epoch?: number;
  name?: string | null;
}

export const failed = <T,>(s: Slot<T>): s is { error: string } => !!s && typeof s === "object" && "error" in (s as object);
export const ok = <T,>(s: Slot<T>): s is T => !!s && !failed(s);

const errorSlot = (e: unknown) => ({ error: String((e as Error)?.message || e) });
const notAvailable = (e: unknown) => e instanceof ApiError && e.status === 404 && /not available/i.test(e.message);

/** Steps of the analysis, in the order they run, for the progress list. */
export const SERVER_STEPS = [
  { key: "identification", label: "Catalogue counterparts" },
  { key: "known_objects", label: "Known asteroids and comets" },
  { key: "tracklets", label: "Moving objects within a visit" },
  { key: "motion", label: "Motion between epochs" },
  { key: "photometry", label: "Photometry in every exposure" },
  { key: "spectrum", label: "Spectrum and classification" },
  { key: "periodogram", label: "Period search" },
  { key: "colours", label: "Infrared colours" },
  { key: "verdict", label: "Verdict" },
] as const;
export const CLIENT_STEPS = [
  { key: "motion", label: "Motion between epochs" },
  { key: "photometry", label: "Photometry in every exposure" },
  { key: "spectrum", label: "Spectrum and classification" },
  { key: "identification", label: "Catalogue counterparts" },
] as const;
export type Plan = "server" | "client";
export type AnalysisProgress = Progress & { plan: Plan };

export function stepList(plan: Plan) {
  return plan === "server" ? SERVER_STEPS : CLIENT_STEPS;
}

/** Index of the running step in its plan's list (0 while queued; the list's length when done). */
export function stepIndex(p: AnalysisProgress): number {
  const list = stepList(p.plan);
  if (p.stage === "done") return list.length;
  const i = list.findIndex((s) => s.key === p.stage);
  return i >= 0 ? i : 0;
}

export type StepState = "done" | "error" | "now" | "todo";

/** State of every step of the plan: from the job's per-step map when the server sends one (its steps run
 *  side by side, ADR-0056), else from the single running stage (one after another). */
export function stepStates(p: AnalysisProgress | null): StepState[] {
  const list = stepList(p?.plan ?? "server");
  if (!p) return list.map(() => "todo");
  if (p.stage === "done") return list.map(() => "done");
  const map = p.steps;
  if (map && Object.keys(map).length) {
    const finished = Object.values(map).every((s) => s === "done" || s === "error");
    return list.map(({ key }) => {
      if (key === "verdict") return finished || p.stage === "verdict" ? "now" : "todo";
      const s = map[key];
      return s === "done" ? "done" : s === "error" ? "error" : s === "running" ? "now" : "todo";
    });
  }
  const at = stepIndex(p);
  return list.map((_, i) => (i < at ? "done" : i === at ? "now" : "todo"));
}

/** Label of a step for messages ("Period search"), the key itself when unknown. */
export const stepLabel = (key: string) => SERVER_STEPS.find((s) => s.key === key)?.label ?? key.replace(/_/g, " ");

/** A spectral-variability answer always with an epochs list (the server leaves it out when nothing is usable). */
const withEpochs = (v: VariabilityResult | null | undefined) => (v ? { ...v, epochs: v.epochs ?? [] } : v);

/** force: measure again instead of taking the server's saved result. */
export async function runPointAnalysis(meta: Meta, pos: AnalysisPos, onProgress: (p: AnalysisProgress) => void, signal: AbortSignal, force = false): Promise<PointAnalysis> {
  let raw: unknown;
  try {
    raw = await analyzePoint(meta.id, pos, (p) => onProgress({ ...p, plan: "server" }), signal, force);
  } catch (e) {
    if (!notAvailable(e) || signal.aborted) throw e;
    return analyzeInBrowser(meta, pos, (p) => onProgress({ ...p, plan: "client" }), signal);
  }
  const out = fromServer(raw as ServerPoint);
  await enrich(out, signal);
  return out;
}

const meanYear = (meta: Meta) => {
  const e = meta.epochs;
  return e.length ? 2000 + (e.reduce((s, x) => s + x.mjd_mean, 0) / e.length - 51544.5) / 365.25 : 2026;
};

/** The slots of a point analysis from the endpoints every server has. */
async function analyzeInBrowser(meta: Meta, pos: AnalysisPos, onProgress: (p: Progress) => void, signal: AbortSignal): Promise<PointAnalysis> {
  const t0 = performance.now();
  const step = (i: number, stage: string) => onProgress({ stage, done: i, total: CLIENT_STEPS.length, elapsed: (performance.now() - t0) / 1000 });
  const out: PointAnalysis = { mode: "client", point: { ra: pos.ra, dec: pos.dec, name: pos.name ?? null }, notes: [] };

  step(0, "motion");
  let pm: [number, number] = [pos.pmra ?? 0, pos.pmdec ?? 0];
  let pmFrom: "spherex" | "prior" | null = pos.pmra || pos.pmdec ? "prior" : null;
  let anchor: { ra: number; dec: number; year: number } | null = null;
  try {
    const m = await fetchMotion(meta.id, pos.ra, pos.dec, signal);
    const c = m.combined;
    const sig = c.pm_total_err ? c.pm_total / c.pm_total_err : null;
    out.motion = { pmra: c.pmra, pmdec: c.pmdec, pmra_err: c.pmra_err, pmdec_err: c.pmdec_err, pm_total: c.pm_total, pm_total_err: c.pm_total_err, significance: sig ?? undefined, n_epochs: c.n_epochs };
    if (sig != null && sig >= 4 && m.track.length) {
      pm = [c.pmra, c.pmdec];
      pmFrom = "spherex";
      const t = m.track[0]; // the fitted line at the first epoch
      anchor = { ra: t.ra, dec: t.dec, year: t.year };
    }
  } catch (e) {
    if (signal.aborted) throw e;
    out.motion = errorSlot(e);
  }

  step(1, "photometry");
  const target = anchor
    ? { ra: anchor.ra, dec: anchor.dec, pmra: pm[0], pmdec: pm[1], ref_epoch: anchor.year, name: pos.name ?? null }
    : { ra: pos.ra, dec: pos.dec, pmra: pm[0], pmdec: pm[1], ref_epoch: pos.ref_epoch ?? 2000, name: pos.name ?? null };
  let records: NonNullable<Awaited<ReturnType<typeof measureSource>>>["photometry"] = [];
  try {
    const ph = await measureSource(meta.id, target, signal);
    records = ph.photometry;
    out.photometry = { n: records.length, records };
    if (pmFrom === "spherex") out.notes!.push("The photometry follows the motion measured from the co-adds.");
    else if (pmFrom === "prior") out.notes!.push("The photometry follows the catalogue motion of the selected object.");
  } catch (e) {
    if (signal.aborted) throw e;
    out.photometry = errorSlot(e);
  }

  step(2, "spectrum");
  if (records.length) {
    try {
      const sp = await analyzeSpectrum(`pa|${meta.id}|${JSON.stringify(target)}`, records, { ...target, name: target.name }, true);
      out.spectrum = sp.spectrum;
      out.classification = sp.classification;
      out.variability = withEpochs(sp.variability);
    } catch (e) {
      if (signal.aborted) throw e;
      out.classification = errorSlot(e);
    }
    const good = records.filter((r) => !r.flagged && r.flux_mjy != null).length;
    if (good >= 10) {
      try {
        out.periodogram = await fetchPeriodogram(meta.id, { key: `pa|${JSON.stringify(target)}`, photometry: records });
      } catch (e) {
        out.periodogram = errorSlot(e);
      }
    }
  }

  step(3, "identification");
  try {
    // compare at the epoch of the position we have: the anchor's, the prior's, or the shown image's
    out.identification = anchor
      ? await identify(anchor.ra, anchor.dec, anchor.year, pm, signal)
      : await identify(pos.ra, pos.dec, pos.ref_epoch ?? meanYear(meta), pm, signal);
    const gaia = out.identification.matches?.find((m) => m.catalog === "Gaia DR3" && m.pmra != null);
    const cw = out.identification.matches?.find((m) => m.catalog === "CatWISE2020" && m.pmra != null);
    const cm = gaia ?? cw;
    if (ok(out.motion) && cm) {
      const mo = out.motion;
      const err = Math.hypot(mo.pmra_err ?? 0, mo.pmdec_err ?? 0) / Math.SQRT2;
      const d = Math.hypot(mo.pmra - (cm.pmra ?? 0), mo.pmdec - (cm.pmdec ?? 0));
      mo.catalog = { catalog: cm.catalog ?? "catalogue", pmra: cm.pmra ?? 0, pmdec: cm.pmdec ?? 0 };
      mo.discrepancy_sigma = err > 0 ? d / Math.hypot(err, cm === gaia ? 5 : 30) : null;
    }
  } catch (e) {
    if (signal.aborted) throw e;
    out.identification = errorSlot(e);
  }
  step(4, "done");
  return out;
}

// ------------------------------------------------------------------ catalogue counterparts
const CATS = [
  { id: "gaia", label: "Gaia DR3", epoch: 2016.0 },
  { id: "catwise", label: "CatWISE2020", epoch: 2015.4 },
  { id: "2mass", label: "2MASS", epoch: 1999.0 },
  { id: "simbad", label: "SIMBAD", epoch: 2000.0 },
] as const;
const MATCH_ARCSEC = 6; // about one SPHEREx pixel
const CONE_ARCMIN = 0.5;

function back(ra: number, dec: number, pm: [number, number], dt: number): [number, number] {
  return propagate({ ra, dec, epoch: 0, pmra: pm[0], pmdec: pm[1] }, dt);
}

/** Best counterpart: several catalogue sources can share one 6″ pixel, so prefer the bright one. */
function bestMatch(rows: CatalogSource[], ra: number, dec: number, year: number, pm: [number, number], simbad: boolean, maxSep = MATCH_ARCSEC): (CatalogMatch & { ra: number; dec: number }) | null {
  let best: { score: number; m: CatalogMatch & { ra: number; dec: number } } | null = null;
  for (const r of rows) {
    const own = r.pmra != null && r.pmdec != null;
    const [rr, dd] = propagate({ ...r, pmra: own ? r.pmra : pm[0], pmdec: own ? r.pmdec : pm[1] }, year);
    const sep = separation(ra, dec, rr, dd) * 3600;
    if (sep > maxSep) continue;
    const refs = Number(r.refs ?? 0);
    const score = simbad ? (sep / 3) ** 2 - 0.3 * Math.log10(refs + 1) : (r.mag ?? 25) + (sep / 3) ** 2;
    if (!best || score < best.score) {
      best = {
        score,
        m: {
          id: r.id, name: r.id, ra: rr, dec: dd, sep_arcsec: sep, mag: r.mag, mag_band: r.mag_band, pmra: r.pmra, pmdec: r.pmdec,
          type: (r.type as string | null) ?? null, otype: (r.otype as string | null) ?? null, sp_type: (r.sp_type as string | null) ?? null,
          parallax: (r.parallax as number | null) ?? null, color: (r.color as number | null) ?? null,
          j: (r.j as number | null) ?? null, h: (r.h as number | null) ?? null, w1: (r.w1 as number | null) ?? null, refs,
        },
      };
    }
  }
  return best?.m ?? null;
}

/** The counterpart of (ra, dec) at `year` in one catalogue (a 0.5′ cone at the catalogue's epoch). */
async function searchCatalog(c: (typeof CATS)[number], ra: number, dec: number, year: number, pm: [number, number], signal: AbortSignal, maxSep = MATCH_ARCSEC) {
  const [cra, cdec] = back(ra, dec, pm, c.epoch - year);
  const rows = await fetchCatalog(c.id, cra, cdec, CONE_ARCMIN, signal);
  const m = bestMatch(rows, ra, dec, year, pm, c.id === "simbad", maxSep);
  return m ? { ...m, catalog: c.label } : null;
}

async function identify(ra: number, dec: number, year: number, pm: [number, number], signal: AbortSignal) {
  const errors: Record<string, string> = {};
  const search = async (c: (typeof CATS)[number], pmUse: [number, number]) => {
    try {
      return await searchCatalog(c, ra, dec, year, pmUse, signal);
    } catch (e) {
      if (signal.aborted) throw e;
      errors[c.label] = String((e as Error)?.message || e);
      return null;
    }
  };
  const [gaia, catwise] = await Promise.all([search(CATS[0], pm), search(CATS[1], pm)]);
  // 2MASS has no motions and SIMBAD's are J2000: move them with Gaia's motion when we have it
  const pmBest: [number, number] = gaia?.pmra != null && gaia.pmdec != null ? [gaia.pmra, gaia.pmdec] : pm;
  const [tmass, simbad] = await Promise.all([search(CATS[2], pmBest), search(CATS[3], pmBest)]);
  return {
    matches: [gaia, catwise, tmass].filter((m): m is NonNullable<typeof m> => !!m),
    simbad: simbad ? [simbad] : [],
    solar_system: [],
    errors: Object.keys(errors).length ? errors : undefined,
  };
}

// ------------------------------------------------------------------ the server's result (ADR-0053)
interface ServerMatch {
  id: string | number;
  ra: number;
  dec: number;
  epoch?: number | null;
  pmra?: number | null;
  pmdec?: number | null;
  mag?: number | null;
  mag_band?: string | null;
  otype?: string | null;
  sptype?: string | null;
  sp_type?: string | null;
  type?: string | null;
  parallax?: number | null;
  sep_arcsec: number;
}
type ServerCat = { matches: ServerMatch[]; n_in_radius: number; counterpart: boolean } | { error: string };
interface ServerFinding {
  kind: string;
  text: string;
  confidence: number;
  novelty: number;
  evidence?: Record<string, unknown>;
}
interface ServerPoint {
  id?: string;
  request?: { ra: number; dec: number; name?: string | null };
  point?: { ra: number; dec: number; x?: number; y?: number; motion_used?: { pmra?: number | null; pmdec?: number | null; source?: string } };
  identification?: Slot<{ gaia?: ServerCat; catwise?: ServerCat; "2mass"?: ServerCat; simbad?: ServerCat }>;
  photometry?: Slot<{ photometry: Photometry[]; summary?: { n?: number } & Record<string, unknown> }>;
  spectrum?: Slot<{ spectrum?: SpectrumResult; classification?: ClassifyResult; variability?: VariabilityResult }>;
  motion?: Slot<MotionFit>;
  periodogram?: Slot<PeriodogramResult>;
  colours?: Slot<{ ratio: number | null; long_snr: number | null; red_excess: boolean }>;
  known_objects?: Slot<{ matches?: { name: string; kind?: string; vmag?: number | null; sep_arcsec: number }[] }>;
  verdict?: { novelty?: number; headline?: string; findings?: ServerFinding[] };
  timings?: PointAnalysis["timings"];
}

/** Catalogue epoch position (for looking the row up again) next to the match the window shows. */
type Counterpart = CatalogMatch & { catRa?: number; catDec?: number };
const CAT_LABEL = { gaia: "Gaia DR3", catwise: "CatWISE2020", "2mass": "2MASS", simbad: "SIMBAD" } as const;

const toMatch = (m: ServerMatch, catalog: string): Counterpart => ({
  catalog, id: m.id, name: String(m.id), sep_arcsec: m.sep_arcsec, mag: m.mag ?? null, mag_band: m.mag_band ?? null, pmra: m.pmra ?? null, pmdec: m.pmdec ?? null,
  otype: m.otype ?? null, sp_type: m.sp_type ?? m.sptype ?? null, type: m.type ?? null, parallax: m.parallax ?? null, catRa: m.ra, catDec: m.dec,
});

const num = (v: number) => (Math.abs(v) >= 100 || Number.isInteger(v) ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(2));
/** A finding's evidence object as one short line ("sep 1.2″ · gaia pm 10393"). */
function evidenceText(ev?: Record<string, unknown>): string | null {
  if (!ev) return null;
  const parts = Object.entries(ev)
    .filter(([, v]) => v != null && v !== false && v !== "")
    .map(([k, v]) => {
      const key = k.replace(/_arcsec$/, "").replace(/_mas_yr$/, " (mas/yr)").replace(/_/g, " ");
      const val = typeof v === "number" ? num(v) + (k.endsWith("_arcsec") ? "″" : "") : v === true ? "yes" : Array.isArray(v) ? v.join(", ") : String(v);
      return `${key} ${val}`;
    });
  return parts.length ? parts.join(" · ") : null;
}

/** The server's result in the window's slots: one counterpart per catalogue (nearest within 4″), the
 *  spectral analysis split into spectrum / classification / variability, the motion fit with its significance
 *  and the catalogue motion it is compared with, and the verdict's findings. */
export function fromServer(raw: ServerPoint): PointAnalysis {
  const req = raw.request ?? raw.point ?? { ra: 0, dec: 0 };
  const out: PointAnalysis = { id: raw.id, mode: "server", notes: [], point: { ra: raw.point?.ra ?? req.ra, dec: raw.point?.dec ?? req.dec, x: raw.point?.x, y: raw.point?.y, name: raw.request?.name ?? null } };

  const id = raw.identification;
  if (failed(id)) out.identification = id;
  else if (id) {
    const errors: Record<string, string> = {};
    const nearby: Counterpart[] = [];
    const pick = (k: keyof typeof CAT_LABEL): Counterpart | null => {
      const c = id[k];
      if (!c) return null;
      if ("error" in c) {
        errors[CAT_LABEL[k]] = c.error;
        return null;
      }
      const ms = c.matches.map((m) => toMatch(m, CAT_LABEL[k]));
      nearby.push(...(c.counterpart ? ms.slice(1) : ms));
      return c.counterpart ? ms[0] : null;
    };
    const [gaia, catwise, tmass, simbad] = [pick("gaia"), pick("catwise"), pick("2mass"), pick("simbad")];
    const known = ok(raw.known_objects) ? raw.known_objects.matches ?? [] : [];
    const byName = new Map<string, (typeof known)[number]>();
    for (const k of known) if (!byName.has(k.name) || byName.get(k.name)!.sep_arcsec > k.sep_arcsec) byName.set(k.name, k);
    out.identification = {
      matches: [gaia, catwise, tmass].filter((m): m is Counterpart => !!m),
      simbad: simbad ? [simbad] : [],
      nearby: nearby.sort((a, b) => (a.sep_arcsec ?? 0) - (b.sep_arcsec ?? 0)),
      solar_system: [...byName.values()].map((k) => ({ name: k.name, kind: k.kind, vmag: k.vmag ?? null, sep_arcsec: k.sep_arcsec })),
      errors: Object.keys(errors).length ? errors : undefined,
    };
  } else if (failed(raw.known_objects)) out.notes!.push(`Known asteroids and comets: ${raw.known_objects.error}`);

  const ph = raw.photometry;
  out.photometry = failed(ph) ? ph : ph ? { n: ph.summary?.n ?? ph.photometry.length, records: ph.photometry, summary: ph.summary } : undefined;

  const sp = raw.spectrum;
  if (failed(sp)) out.classification = sp;
  else if (sp) {
    out.spectrum = sp.spectrum;
    out.classification = sp.classification;
    out.variability = withEpochs(sp.variability);
  }
  out.timings = raw.timings ?? null;

  const mo = raw.motion;
  if (failed(mo)) out.motion = mo;
  else if (mo?.combined) {
    const c = mo.combined;
    const motion: NonNullable<Exclude<PointAnalysis["motion"], { error: string }>> = {
      pmra: c.pmra, pmdec: c.pmdec, pmra_err: c.pmra_err, pmdec_err: c.pmdec_err, pm_total: c.pm_total, pm_total_err: c.pm_total_err,
      significance: c.pm_total_err ? c.pm_total / c.pm_total_err : undefined, n_epochs: c.n_epochs,
    };
    const ident = ok(out.identification) ? out.identification : null;
    const cm = ident?.matches?.find((m) => m.catalog === "Gaia DR3" && m.pmra != null) ?? ident?.matches?.find((m) => m.catalog === "CatWISE2020" && m.pmra != null);
    if (cm) {
      const err = Math.hypot(c.pmra_err ?? 0, c.pmdec_err ?? 0) / Math.SQRT2;
      motion.catalog = { catalog: cm.catalog ?? "catalogue", pmra: cm.pmra ?? 0, pmdec: cm.pmdec ?? 0 };
      motion.discrepancy_sigma = err > 0 ? Math.hypot(c.pmra - (cm.pmra ?? 0), c.pmdec - (cm.pmdec ?? 0)) / Math.hypot(err, cm.catalog === "Gaia DR3" ? 5 : 30) : null;
    }
    out.motion = motion;
  }

  if (raw.periodogram) out.periodogram = raw.periodogram;
  const col = raw.colours;
  if (ok(col) && col.ratio != null) out.red_excess = { ratio_long_short: col.ratio, excess_snr: col.long_snr ?? undefined, note: col.red_excess ? "very red" : undefined };

  const used = raw.point?.motion_used?.source ?? "";
  if (/^(Gaia|SIMBAD)/.test(used)) out.notes!.push(`The photometry follows the catalogue motion of ${used}.`);

  const v = raw.verdict;
  if (v) out.verdict = { findings: (v.findings ?? []).map((f) => ({ text: f.text, confidence: f.confidence, evidence: evidenceText(f.evidence) })), novelty: v.novelty ?? null, novelty_note: v.headline ?? null };
  return out;
}

/** Catalogues the server could not reach in time (IRSA or SIMBAD busy), asked again from here: the server
 *  keeps those queries running and caches their answers, so a second ask is usually quick. Returns a new
 *  result with the counterparts found, or null when nothing changed. */
export async function retryCatalogs(r: PointAnalysis, meta: Meta, signal: AbortSignal): Promise<PointAnalysis | null> {
  if (r.mode !== "server" || !ok(r.identification) || !r.identification.errors || !r.point) return null;
  const id = r.identification;
  const before = Object.keys(id.errors ?? {}).length;
  const errors = { ...id.errors };
  const gaia = id.matches?.find((m) => m.catalog === "Gaia DR3");
  const pm: [number, number] = gaia?.pmra != null && gaia.pmdec != null ? [gaia.pmra, gaia.pmdec] : [0, 0];
  const year = meanYear(meta);
  const found = await Promise.all(
    CATS.filter((c) => errors[c.label]).map(async (c) => {
      try {
        const m = await searchCatalog(c, r.point!.ra, r.point!.dec, year, pm, signal, 4);
        delete errors[c.label];
        return m;
      } catch (e) {
        if (signal.aborted) throw e;
        return null;
      }
    }),
  );
  if (Object.keys(errors).length === before) return null;
  const add = found.filter((m): m is NonNullable<typeof m> => !!m);
  const out: PointAnalysis = {
    ...r,
    identification: {
      ...id,
      matches: [...(id.matches ?? []), ...add.filter((m) => m.catalog !== "SIMBAD")],
      simbad: id.simbad?.length ? id.simbad : add.filter((m) => m.catalog === "SIMBAD"),
      errors: Object.keys(errors).length ? errors : undefined,
    },
  };
  // the motion is compared with Gaia (else CatWISE); a CatWISE counterpart found now can be that yardstick
  const cw = add.find((m) => m.catalog === "CatWISE2020" && m.pmra != null);
  if (ok(out.motion) && !out.motion.catalog && cw) {
    const mo = { ...out.motion };
    const err = Math.hypot(mo.pmra_err ?? 0, mo.pmdec_err ?? 0) / Math.SQRT2;
    mo.catalog = { catalog: "CatWISE2020", pmra: cw.pmra ?? 0, pmdec: cw.pmdec ?? 0 };
    mo.discrepancy_sigma = err > 0 ? Math.hypot(mo.pmra - (cw.pmra ?? 0), mo.pmdec - (cw.pmdec ?? 0)) / Math.hypot(err, 30) : null;
    out.motion = mo;
  }
  return out;
}

/** The server keeps only a few columns of each counterpart; fetch the rest (2MASS J/H, CatWISE W1, SIMBAD
 *  spectral and object type) from the same catalogue tables, so every SPHEREx value has its yardstick. */
async function enrich(r: PointAnalysis, signal: AbortSignal): Promise<void> {
  if (!ok(r.identification)) return;
  const id = r.identification;
  const want: [string, Counterpart, (row: CatalogSource) => Partial<CatalogMatch>][] = [];
  for (const m of (id.matches ?? []) as Counterpart[]) {
    if (m.catalog === "2MASS" && m.j == null) want.push(["2mass", m, (x) => ({ j: x.j as number | null, h: x.h as number | null })]);
    if (m.catalog === "CatWISE2020" && m.w1 == null) want.push(["catwise", m, (x) => ({ w1: x.w1 as number | null })]);
  }
  const sim = id.simbad?.[0] as Counterpart | undefined;
  if (sim && (sim.sp_type == null || sim.type == null)) want.push(["simbad", sim, (x) => ({ sp_type: (x.sp_type as string | null) ?? sim.sp_type, type: (x.type as string | null) ?? sim.type, refs: (x.refs as number | null) ?? null })]);
  const one = async ([cat, m, take]: (typeof want)[number]) => {
    if (m.catRa == null || m.catDec == null) return;
    const rows = await fetchCatalog(cat, m.catRa, m.catDec, 0.1, signal);
    const row = rows.find((x) => String(x.id) === String(m.id));
    if (row) Object.assign(m, take(row));
  };
  const timeout = new Promise<void>((resolve) => setTimeout(resolve, 8000));
  await Promise.race([Promise.allSettled(want.map(one)), timeout]);
}

// ------------------------------------------------------------------ presentation
export const FAMILY: Record<string, string> = {
  star: "Star",
  late_m_dwarf: "Late-M dwarf",
  brown_dwarf: "Brown dwarf",
  composite: "Blend: star + cold source",
  yso: "Young stellar object",
  yso_candidate: "Young star candidate",
  nonstellar: "Non-stellar: AGN/QSO or YSO",
  galaxy: "Galaxy",
};

const LETTERS: Record<string, number> = { O: 0, B: 10, A: 20, F: 30, G: 40, K: 50, M: 60, L: 70, T: 80, Y: 90 };

/** O0 = 0 … M0 = 60, L0 = 70, T0 = 80, Y0 = 90 (+ subclass); null for white dwarfs, carbon stars, blanks. */
export function sptypeCode(sp: string | null | undefined): number | null {
  if (!sp) return null;
  const s = sp.trim().replace(/^[wr](?=[A-Z])/, "");
  if (/^D[ABOQZCXP]/.test(s)) return null;
  const m = s.match(/(?:^|[^a-zA-Z]|sd|esd|usd|^d|^g)([OBAFGKMLTY])\s*(\d+(?:\.\d+)?)?/);
  if (!m) return null;
  return LETTERS[m[1]] + (m[2] ? Number(m[2]) : 0);
}

/** Our family for a SIMBAD object type (code + description). */
export function simbadFamily(otype?: string | null, type?: string | null): string | null {
  const t = (type ?? "").toLowerCase();
  const o = (otype ?? "").trim();
  if (!t && !o) return null;
  if (t.includes("brown") || /^BD|^BrownD/.test(o)) return "brown_dwarf";
  if (/young stellar|t tau|herbig|orion var|pre-main/.test(t) || ["Y*O", "YSO", "TT*", "Ae*"].includes(o)) return "yso";
  if (/quasar|agn|active galax|seyfert|blazar|bl lac|liner/.test(t) || ["QSO", "AGN", "Sy1", "Sy2", "Bla", "BLL"].includes(o)) return "qso";
  if (t.includes("galax") || ["G", "GiG", "GiP", "IG", "SBG", "EmG", "rG", "H2G", "LSB", "bCG"].includes(o)) return "galaxy";
  if (/nebula|hii|cloud/.test(t)) return "nebula";
  if (/star|binary|variable|dwarf|giant|cepheid|lyrae|mira|carbon|nova/.test(t) || o.endsWith("*")) return "star";
  return "other";
}

const AGREE = new Set(["star|star", "late_m_dwarf|star", "late_m_dwarf|brown_dwarf", "brown_dwarf|brown_dwarf", "yso|yso", "yso_candidate|yso", "nonstellar|qso", "nonstellar|galaxy", "nonstellar|yso", "galaxy|galaxy", "galaxy|qso"]);
const UNSURE = new Set(["yso_candidate|star", "star|yso", "star|brown_dwarf"]);
const VARIABLE_WORDS = /variable|mira|rr lyrae|cepheid|eclipsing|flare|long-period|pulsat|rotat/;

export interface Check {
  key: string;
  label: string;
  ours: string;
  catalog: string;
  theirs: string;
  delta: string | null;
  agree: boolean | null;
  note?: string | null;
}

const pmText = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(2)}″/yr` : `${v.toFixed(0)} mas/yr`);

/** SPHEREx measurements against the catalogue values of the matched counterparts, one row per quantity. */
/** Most measurements are relative ring fluxes or saturated (a bright star): there is no calibrated spectrum,
 *  so a class, blackbody or colour fitted to the few calibrated points says nothing about the star. */
export function saturated(r: PointAnalysis): boolean {
  const recs = ok(r.photometry) ? r.photometry.records ?? [] : [];
  return recs.length > 0 && recs.filter((p) => p.relative || p.saturated).length / recs.length > 0.5;
}

/** The classification, when the measurements can carry one (see saturated). */
export const usableClass = (r: PointAnalysis): ClassifyResult | null => (ok(r.classification) && !saturated(r) ? r.classification : null);

export function catalogChecks(r: PointAnalysis): { rows: Check[]; agree: number; checked: number } {
  const rows: Check[] = [];
  const id = ok(r.identification) ? r.identification : null;
  const matches = id?.matches ?? [];
  const find = (c: string) => matches.find((m) => m.catalog?.toLowerCase().replace(/[^a-z0-9]/g, "").startsWith(c));
  const gaia = find("gaia");
  const cw = find("catwise");
  const tm = find("2mass");
  const sim = id?.simbad?.[0];
  const sigma = r.point?.sigma_arcsec ?? 1.0;

  const posRef = gaia ?? cw ?? tm;
  if (posRef?.sep_arcsec != null) {
    const tol = Math.max(2, 3 * sigma);
    rows.push({ key: "pos", label: "Position", ours: r.point?.sigma_arcsec ? `±${r.point.sigma_arcsec.toFixed(1)}″` : "centre", catalog: posRef.catalog ?? "", theirs: String(posRef.name ?? posRef.id), delta: `${posRef.sep_arcsec.toFixed(1)}″`, agree: posRef.sep_arcsec <= tol, note: "Separation at the SPHEREx epoch (catalogue moved with its proper motion)" });
  }

  const mo = ok(r.motion) ? r.motion : null;
  const pmRef = mo?.catalog ?? (gaia?.pmra != null ? { catalog: "Gaia DR3", pmra: gaia.pmra!, pmdec: gaia.pmdec ?? 0 } : cw?.pmra != null ? { catalog: "CatWISE2020", pmra: cw.pmra!, pmdec: cw.pmdec ?? 0 } : null);
  if (mo && pmRef) {
    const ours = mo.pm_total ?? Math.hypot(mo.pmra, mo.pmdec);
    const theirs = Math.hypot(pmRef.pmra, pmRef.pmdec);
    const d = Math.hypot(mo.pmra - pmRef.pmra, mo.pmdec - pmRef.pmdec);
    const err = Math.hypot(mo.pmra_err ?? 0, mo.pmdec_err ?? 0) / Math.SQRT2;
    const tol = 3 * Math.hypot(err, /gaia/i.test(pmRef.catalog) ? 5 : 30);
    rows.push({ key: "pm", label: "Proper motion", ours: `${pmText(ours)}${err ? ` ± ${pmText(err)}` : ""}`, catalog: pmRef.catalog, theirs: pmText(theirs), delta: mo.discrepancy_sigma != null ? `${mo.discrepancy_sigma.toFixed(1)}σ` : pmText(d), agree: mo.discrepancy_sigma != null ? mo.discrepancy_sigma <= 3 : d <= tol, note: mo.n_epochs ? `${mo.n_epochs} epochs` : null });
  }

  const cls = usableClass(r);
  const synth = cls?.synthetic_photometry ?? {};
  const fromServer = cls?.catalog_comparison?.bands ?? {};
  const catMag: Record<string, [CatalogMatch | undefined, keyof CatalogMatch]> = { J: [tm, "j"], H: [tm, "h"], Ks: [tm, "mag"], W1: [cw, "w1"], W2: [cw, "mag"] };
  for (const band of ["J", "H", "Ks", "W1", "W2"]) {
    const s = synth[band];
    const k = fromServer[band];
    const [m, col] = catMag[band];
    const theirs = k?.catalog ?? ((m?.[col] as number | null | undefined) ?? null);
    if (s?.mag == null || theirs == null) continue;
    const delta = s.mag - theirs;
    const tol = Math.max(0.25, 3 * (s.mag_err ?? 0));
    rows.push({ key: `mag${band}`, label: `${band} magnitude`, ours: `${s.mag.toFixed(2)}${s.extrapolated ? "*" : ""}`, catalog: band.startsWith("W") ? "CatWISE2020" : "2MASS", theirs: theirs.toFixed(2), delta: `${delta > 0 ? "+" : ""}${delta.toFixed(2)}`, agree: Math.abs(delta) <= tol, note: s.extrapolated ? "Synthetic, partly extrapolated past 5 µm" : "Synthetic, from our spectrum" });
  }

  const best = cls?.templates?.best;
  const ours = sptypeCode(best?.sptype);
  const theirs = sptypeCode(sim?.sp_type);
  if (best && ours != null && sim?.sp_type && theirs != null) {
    const tol = theirs >= 50 ? 2 : 4;
    const d = ours - theirs;
    rows.push({ key: "sp", label: "Spectral type", ours: best.sptype, catalog: "SIMBAD", theirs: sim.sp_type, delta: d === 0 ? "same" : `${d > 0 ? "+" : ""}${d.toFixed(d % 1 ? 1 : 0)} subclass${Math.abs(d) === 1 ? "" : "es"}`, agree: Math.abs(d) <= tol, note: theirs < 30 ? "O/B/A stars are nearly featureless at 0.75–2.5 µm" : null });
  }

  const fam = cls?.summary?.family ?? null;
  const simFam = sim ? simbadFamily(sim.otype, sim.type) : null;
  if (fam && simFam && simFam !== "other") {
    const k = `${fam}|${simFam}`;
    const named = (fam === "star" || fam === "late_m_dwarf") && best ? starName(best.sptype) : null;
    rows.push({ key: "class", label: "Object class", ours: named ?? FAMILY[fam] ?? fam, catalog: "SIMBAD", theirs: sim?.type ?? sim?.otype ?? "", delta: null, agree: AGREE.has(k) ? true : UNSURE.has(k) || fam === "composite" ? null : false });
  }

  const v = ok(r.variability) ? r.variability : null;
  if (sim && v?.available && VARIABLE_WORDS.test((sim.type ?? "").toLowerCase())) {
    const varies = (v.epochs ?? []).some((e) => e.variable);
    rows.push({ key: "var", label: "Variability", ours: varies ? "changes between epochs" : "no change seen", catalog: "SIMBAD", theirs: sim.type ?? "", delta: null, agree: varies ? true : null, note: varies ? null : "A few epochs months apart can miss short or small variations" });
  }

  const judged = rows.filter((x) => x.agree != null);
  return { rows, checked: judged.length, agree: judged.filter((x) => x.agree).length };
}

export interface Headline {
  title: string;
  /** Spectral type or label from the classifier. */
  type: string | null;
  tags: string[];
  /** Best-template probability of its class, 0–1. */
  confidence: number | null;
  /** The name a catalogue knows it by. */
  knownAs: string | null;
}

export function headline(r: PointAnalysis): Headline {
  const cls = usableClass(r);
  const id = ok(r.identification) ? r.identification : null;
  const mo = ok(r.motion) ? r.motion : null;
  const tags: string[] = [];
  const sim = id?.simbad?.[0];
  const knownAs = sim ? String(sim.name ?? sim.id) : null;
  if (mo && (mo.significance ?? 0) >= 4 && (mo.pm_total ?? 0) >= 100) tags.push("high proper motion");
  if (ok(r.variability) && (r.variability.epochs ?? []).some((e) => e.variable)) tags.push("variable");
  if (cls?.ir_index && cls.ir_index.alpha > -1.6 && cls.summary?.family !== "brown_dwarf") tags.push("infrared excess");
  if (r.point?.found === false) return { title: "No source here", type: null, tags: [], confidence: null, knownAs: null };
  if (!cls?.available) {
    const rel = saturated(r);
    return { title: rel ? "Bright saturated star" : "Unclassified source", type: null, tags, confidence: null, knownAs }; // the reason is in the summary
  }
  const fam = cls.summary?.family ?? null;
  const best = cls.templates?.best;
  const letter = best?.sptype.replace(/^[wr](?=[A-Z])/, "").charAt(0).toUpperCase();
  const confidence = letter && cls.templates?.class_probabilities ? cls.templates.class_probabilities[letter] ?? best?.probability ?? null : best?.probability ?? null;
  const named = (fam === "star" || fam === "late_m_dwarf") && best ? starName(best.sptype) : null;
  return { title: named ?? (fam ? FAMILY[fam] ?? fam : "Source"), type: cls.summary?.label ?? best?.sptype ?? null, tags, confidence, knownAs };
}

/** One paragraph in plain words when the server sent no verdict. */
export function summaryText(r: PointAnalysis, h: Headline, located?: { offsetArcsec: number; pa: string } | null): string {
  const parts: string[] = [];
  const cls = usableClass(r);
  const raw = ok(r.classification) ? r.classification : null;
  const mo = ok(r.motion) ? r.motion : null;
  if (located && located.offsetArcsec >= 1) parts.push(`The brightest source near your click sits ${located.offsetArcsec.toFixed(1)}″ ${located.pa}; this is its analysis.`);
  if (saturated(r)) parts.push("It is saturated: most measurements are relative ring fluxes, so there is no calibrated spectrum to classify.");
  else if (cls?.available && cls.templates?.best) {
    const b = cls.templates.best;
    parts.push(`Its 0.75–5 µm SPHEREx spectrum matches ${/^[AEIOU]/.test(b.sptype) ? "an" : "a"} ${b.sptype} template${h.confidence != null ? ` (relative template weight ${Math.round(h.confidence * 100)} % for the class)` : ""}${goodBlackbody(cls) ? `, blackbody colour temperature ≈${Math.round(goodBlackbody(cls)!.teff_k / 10) * 10} K` : ""}.`);
  } else if (raw && !raw.available) parts.push(raw.reason ? `No calibrated spectrum: ${raw.reason}.` : "No calibrated spectrum.");
  if (mo && (mo.significance ?? 0) >= 4) parts.push(`It moves ${pmText(mo.pm_total ?? 0)} between the epochs.`);
  else if (mo) parts.push("No motion is measurable between the epochs.");
  if (h.knownAs) parts.push(`SIMBAD knows it as ${h.knownAs}.`);
  return parts.join(" ");
}

/** A blackbody colour temperature worth quoting: the fit must be decent (χ²/dof ≤ 3) and the spectrum free of
 *  strong molecular bands or a blend, else it says little. */
export const goodBlackbody = (c: ClassifyResult | null) =>
  c?.blackbody && (c.blackbody.chi2_red ?? 0) <= 3 && !["brown_dwarf", "late_m_dwarf", "composite"].includes(c.summary?.family ?? "") ? c.blackbody : null;

/** "M dwarf", "K giant", "A-type star" from a template's spectral type. */
function starName(sp: string): string | null {
  const m = sp.replace(/^[wr](?=[A-Z])/, "").match(/^([OBAFGKMLTY])\s*(\d+(?:\.\d+)?)?\s*(I{1,3}|IV|V)?/);
  if (!m) return null;
  const [, L, sub, lum] = m;
  if ("LTY".includes(L)) return "Brown dwarf";
  const giant = lum === "III" || lum === "II" ? "giant" : lum === "I" ? "supergiant" : null;
  if (L === "M") return giant ? `M ${giant}` : Number(sub ?? 0) >= 6 ? "Late-M dwarf" : "M dwarf";
  if (L === "K") return `K ${giant ?? "dwarf"}`;
  return giant ? `${L} ${giant}` : `${L}-type star`;
}

export const classifyNotes = (c: ClassifyResult | null) => c?.summary?.notes ?? [];
