/**
 * Field tools (Astrometrica-style, in one rail panel): "Analyze field" with candidate lists, the known
 * solar-system object overlay, Track & Stack with a trial-motion search, and ADES / CSV exports. State lives
 * in useFieldTools (the panel unmounts when its popover closes); everything is fetched through api.ts.
 */
import { Download, Loader2, Play, ScanSearch, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { adesUrl, analyzeField, fetchTrackStack, knownObjects, stackSearch, type Progress } from "../api";
import { worldToPixel } from "../astro";
import type { Display } from "../display";
import { percentiles } from "../render";
import type { FieldResult, KnownObjects, Meta, StackSearch } from "../types";
import type { SkyMark } from "./SkyView";
import { Segmented, Switch } from "./ui/primitives";

type Job<T> = { state: "idle" } | { state: "running"; progress: Progress | null } | { state: "done"; result: T } | { state: "error"; message: string };
export type CandKind = "movers" | "tracklets" | "variables" | "red" | "anomalies";

/** One row of a candidate list: what to show, where it is, and how to verify it. */
export interface Candidate {
  key: string;
  kind: CandKind;
  ra: number;
  dec: number;
  x: number;
  y: number;
  title: string;
  sub: string;
  score: number;
  /** Trial motion for verification / Track & Stack (tracklets). */
  rate?: number;
  pa?: number;
  epoch?: number;
  mjd?: number;
  path?: [number, number][];
}

const KIND_LABEL: Record<CandKind, string> = { movers: "Movers", tracklets: "Tracklets", variables: "Variables", red: "Red excess", anomalies: "Odd" };
const MARK_KIND: Record<CandKind, SkyMark["kind"]> = { movers: "mover", tracklets: "tracklet", variables: "variable", red: "red", anomalies: "anomaly" };

function candidates(r: FieldResult): Candidate[] {
  const c = r.candidates;
  const out: Candidate[] = [];
  for (const m of [...(c.movers ?? []), ...(c.faint_movers ?? [])])
    out.push({
      key: `m:${m.id}`, kind: "movers", ra: m.ra, dec: m.dec, x: m.x, y: m.y, score: m.score,
      title: m.category ? m.category.replace(/_/g, " ") : "mover",
      sub: `${m.motion ? `${(m.motion.pm_total / 1000).toFixed(2)}″/yr` : ""}${m.catalog_discrepancy_sigma != null ? ` · ${m.catalog_discrepancy_sigma.toFixed(1)}σ off ${m.catalog_motion?.catalog ?? "catalogue"}` : ""}`,
      path: m.track?.map((t) => [t.x, t.y] as [number, number]),
    });
  for (const t of [...(c.tracklets ?? []), ...(c.pairs ?? [])]) {
    const mid = t.detections[Math.floor(t.detections.length / 2)] ?? t.detections[0];
    if (!mid) continue;
    out.push({
      key: `t:${t.id}`, kind: "tracklets", ra: mid.ra, dec: mid.dec, x: mid.x, y: mid.y, score: t.score,
      title: t.known?.name ?? `tracklet · visit ${t.epoch + 1}`,
      sub: `${t.n} det · ${t.rate_arcsec_per_hr.toFixed(2)}″/h PA ${t.pa_deg.toFixed(0)}° · S/N ${t.mean_snr.toFixed(1)}${t.known ? "" : " · unknown"}`,
      rate: t.rate_arcsec_per_hr, pa: t.pa_deg, epoch: t.epoch, mjd: t.mjd_mid,
      path: t.detections.map((d) => [d.x, d.y] as [number, number]),
    });
  }
  for (const v of c.variables ?? [])
    out.push({ key: `v:${v.id}`, kind: "variables", ra: v.ra, dec: v.dec, x: v.x, y: v.y, score: v.score, title: `amplitude ${(v.amplitude * 100).toFixed(0)}%`, sub: `χ²ᵣ ${v.chi2_red.toFixed(1)}${v.period ? ` · P ≈ ${v.period.best_period_days.toFixed(1)} d` : ""}` });
  for (const v of c.red ?? [])
    out.push({ key: `r:${v.id}`, kind: "red", ra: v.ra, dec: v.dec, x: v.x, y: v.y, score: v.score, title: v.ratio_long_short != null ? `long/short ${v.ratio_long_short.toFixed(1)}` : "red excess", sub: v.no_gaia ? "no Gaia counterpart" : "" });
  for (const v of c.anomalies ?? [])
    out.push({ key: `a:${v.id}`, kind: "anomalies", ra: v.ra, dec: v.dec, x: v.x, y: v.y, score: v.score, title: `anomaly ${v.score.toFixed(2)}`, sub: v.no_gaia ? "no Gaia counterpart" : "" });
  return out.sort((a, b) => b.score - a.score);
}

const progressText = (p: Progress | null) =>
  !p ? "Starting" : p.ahead ? `${p.ahead} ahead in the queue` : `${p.stage.replace(/_/g, " ")}${p.total ? ` · ${p.done}/${p.total}` : ""}`;

/** All field-tool state for one sequence. */
export function useFieldTools(meta: Meta | null, epoch: number) {
  const id = meta?.id ?? null;
  const [field, setField] = useState<Job<FieldResult>>({ state: "idle" });
  const [known, setKnown] = useState<Job<KnownObjects>>({ state: "idle" });
  const [search, setSearch] = useState<Job<StackSearch>>({ state: "idle" });
  const [showCands, setShowCands] = useState(true);
  const [showKnown, setShowKnown] = useState(false);
  const [kind, setKind] = useState<CandKind>("movers");
  const [active, setActive] = useState<string | null>(null);
  const [stack, setStack] = useState({ on: false, rate: 1, pa: 90, subtract: true });
  const [stackImg, setStackImg] = useState<{ key: string; img: Float32Array | null; error: string | null; loading: boolean }>({ key: "", img: null, error: null, loading: false });
  const ctrls = useRef<AbortController[]>([]);

  // a new sequence resets everything (and withdraws running jobs)
  useEffect(() => {
    setField({ state: "idle" });
    setKnown({ state: "idle" });
    setSearch({ state: "idle" });
    setShowKnown(false);
    setActive(null);
    setStack((s) => ({ ...s, on: false }));
    return () => {
      ctrls.current.forEach((c) => c.abort());
      ctrls.current = [];
    };
  }, [id]);

  const run = useCallback(<T,>(set: (j: Job<T>) => void, start: (onP: (p: Progress) => void, signal: AbortSignal) => Promise<T>) => {
    const ctrl = new AbortController();
    ctrls.current.push(ctrl);
    set({ state: "running", progress: null });
    start((progress) => !ctrl.signal.aborted && set({ state: "running", progress }), ctrl.signal).then(
      (result) => !ctrl.signal.aborted && set({ state: "done", result }),
      (e) => !ctrl.signal.aborted && set({ state: "error", message: String(e?.message || e) }),
    );
    return ctrl;
  }, []);

  const fieldCtrl = useRef<AbortController | null>(null);
  const analyze = () => id && (fieldCtrl.current = run<FieldResult>(setField, (p, s) => analyzeField(id, p, s)));
  const cancel = () => {
    fieldCtrl.current?.abort();
    setField({ state: "idle" });
  };
  useEffect(() => {
    if (showKnown && id && known.state === "idle") run<KnownObjects>(setKnown, (p, s) => knownObjects(id, p, s));
  }, [showKnown, id]); // eslint-disable-line react-hooks/exhaustive-deps
  const searchMotion = () => id && run<StackSearch>(setSearch, (p, s) => stackSearch(id, epoch, p, s));

  // Track & Stack image for the current visit and trial motion (debounced while dragging the sliders)
  const stackKey = stack.on && id ? `${id}|${epoch}|${stack.rate.toFixed(2)}|${stack.pa.toFixed(0)}|${stack.subtract}` : "";
  useEffect(() => {
    if (!stackKey || !id) return;
    const ctrl = new AbortController();
    setStackImg((s) => ({ ...s, key: stackKey, loading: true, error: null }));
    const t = window.setTimeout(() => {
      fetchTrackStack(id, { epoch, rate: stack.rate, pa: stack.pa, subtractStatic: stack.subtract }, ctrl.signal).then(
        (img) => setStackImg({ key: stackKey, img, error: null, loading: false }),
        (e) => !ctrl.signal.aborted && setStackImg({ key: stackKey, img: null, error: String(e?.message || e), loading: false }),
      );
    }, 250);
    return () => {
      window.clearTimeout(t);
      ctrl.abort();
    };
  }, [stackKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const cands = useMemo(() => (field.state === "done" ? candidates(field.result) : []), [field]);
  const stackDisplay: Display | null = useMemo(() => {
    if (!stack.on || !stackImg.img || !meta || stackImg.img.length !== meta.grid.n ** 2) return null;
    const [lo, hi] = percentiles([stackImg.img], [1, 99.7]);
    return { kind: "scalar", img: stackImg.img, lo, hi: hi > lo ? hi : lo + 1e-6, unit: "MJy/sr" };
  }, [stack.on, stackImg.img, meta]);

  const marks: SkyMark[] = useMemo(() => {
    if (!meta) return [];
    const out: SkyMark[] = [];
    if (showCands) for (const c of cands) out.push({ key: c.key, kind: MARK_KIND[c.kind], x: c.x, y: c.y, path: c.kind === "tracklets" || c.kind === "movers" ? c.path : undefined, active: c.key === active, title: `${KIND_LABEL[c.kind]}: ${c.title}` });
    if (showKnown && known.state === "done") {
      const ep = meta.epochs[epoch];
      const t = ep ? known.result.times.reduce((a, b) => (Math.abs(b.mjd - ep.mjd_mean) < Math.abs(a.mjd - ep.mjd_mean) ? b : a), known.result.times[0]) : null;
      for (const o of t?.objects ?? []) {
        if (!o.inside) continue;
        // arrow: where the object is one hour later (rates in ″/h; RA rate includes cos dec)
        const cosd = Math.cos((o.dec * Math.PI) / 180) || 1;
        const later = worldToPixel(meta.grid, o.ra + ((o.ra_rate ?? 0) / 3600) / cosd, o.dec + (o.dec_rate ?? 0) / 3600);
        out.push({ key: `k:${o.name}`, kind: "known", x: o.x, y: o.y, label: `${o.name}${o.vmag != null ? ` V${o.vmag.toFixed(1)}` : ""}`, to: later, title: `${o.name} · ${o.speed_arcsec_per_hr?.toFixed(1) ?? "?"}″/h` });
      }
    }
    if (stack.on && search.state === "done")
      for (const c of search.result.candidates) out.push({ key: `s:${c.id}`, kind: "stack", x: c.x, y: c.y, title: `Track & Stack: S/N ${c.snr.toFixed(1)} at ${c.rate_arcsec_per_hr.toFixed(2)}″/h PA ${c.pa_deg.toFixed(0)}°` });
    return out;
  }, [meta, cands, showCands, showKnown, known, epoch, active, stack.on, search]);

  return { id, field, known, search, cands, kind, setKind, showCands, setShowCands, showKnown, setShowKnown, active, setActive, stack, setStack, stackImg, stackDisplay, marks, analyze, cancel, searchMotion };
}
export type FieldTools = ReturnType<typeof useFieldTools>;

function csv(cands: Candidate[]): string {
  const rows = [["kind", "id", "ra_deg", "dec_deg", "x_px", "y_px", "score", "rate_arcsec_per_hr", "pa_deg", "epoch", "description"]];
  for (const c of cands) rows.push([c.kind, c.key.slice(2), c.ra.toFixed(6), c.dec.toFixed(6), c.x.toFixed(2), c.y.toFixed(2), c.score.toFixed(3), c.rate?.toFixed(3) ?? "", c.pa?.toFixed(1) ?? "", c.epoch != null ? String(c.epoch) : "", `${c.title} ${c.sub}`.replace(/[",\n]/g, " ")]);
  return rows.map((r) => r.join(",")).join("\n");
}

export function FieldPanel({ t, onPick, epochLabel }: { t: FieldTools; onPick: (c: Candidate) => void; epochLabel: string }) {
  const counts = useMemo(() => {
    const n: Record<CandKind, number> = { movers: 0, tracklets: 0, variables: 0, red: 0, anomalies: 0 };
    for (const c of t.cands) n[c.kind]++;
    return n;
  }, [t.cands]);
  const list = t.cands.filter((c) => c.kind === t.kind);
  const csvHref = useMemo(() => (t.cands.length ? URL.createObjectURL(new Blob([csv(t.cands)], { type: "text/csv" })) : null), [t.cands]);
  useEffect(() => () => void (csvHref && URL.revokeObjectURL(csvHref)), [csvHref]);
  const f = t.field;
  return (
    <div className="field-panel">
      <div className="micro menu-title">Field analysis</div>
      {f.state === "idle" && (
        <button className="btn primary-cta" onClick={t.analyze}>
          <ScanSearch size={14} /> Analyze field
        </button>
      )}
      {f.state === "running" && (
        <div className="field-running">
          <Loader2 size={14} className="spin" /> <span className="small">{progressText(f.progress)}</span>
          <button className="btn ghost small-btn" onClick={t.cancel}>Cancel</button>
        </div>
      )}
      {f.state === "error" && (
        <p className="form-hint err">
          {f.message} <button className="btn ghost small-btn" onClick={t.analyze}>Retry</button>
        </p>
      )}
      {f.state === "done" && (
        <>
          <Switch checked={t.showCands} onChange={t.setShowCands} label="Show candidates on the sky" hint={`${t.cands.length} candidates · ${f.result.stats?.sources ?? "?"} sources`} />
          <Segmented<CandKind>
            label="Candidates"
            size="sm"
            value={t.kind}
            onChange={t.setKind}
            options={(Object.keys(KIND_LABEL) as CandKind[]).map((k) => ({ value: k, label: `${KIND_LABEL[k]} ${counts[k]}`, disabled: !counts[k] }))}
          />
          <div className="cand-list">
            {list.length ? (
              list.slice(0, 60).map((c) => (
                <button key={c.key} className={`cand-row${t.active === c.key ? " on" : ""}`} onClick={() => onPick(c)}>
                  <span className="cand-title">{c.title}</span>
                  <span className="cand-score num">{c.score.toFixed(2)}</span>
                  <span className="cand-sub">{c.sub}</span>
                </button>
              ))
            ) : (
              <p className="form-hint">None found.</p>
            )}
          </div>
          <div className="exports">
            <a className="btn ghost" href={adesUrl(f.result.id)} download>
              <Download size={14} /> Tracklets <small>ADES</small>
            </a>
            {csvHref && (
              <a className="btn ghost" href={csvHref} download={`${t.id}-candidates.csv`}>
                <Download size={14} /> Candidates <small>CSV</small>
              </a>
            )}
          </div>
        </>
      )}

      <div className="micro menu-title field-sep">Known objects</div>
      <Switch
        checked={t.showKnown}
        onChange={t.setShowKnown}
        label="Asteroids & comets"
        hint={
          t.known.state === "running" ? `Computing positions as seen from SPHEREx · ${progressText(t.known.progress)}`
            : t.known.state === "error" ? t.known.message
              : t.known.state === "done" ? `${t.known.result.times.reduce((n, x) => n + x.objects.filter((o) => o.inside).length, 0)} positions in the field, V ≤ 21; arrows: 1 h of motion`
                : "Labels and motion arrows at each epoch (V ≤ 21)"
        }
      />

      <div className="micro menu-title field-sep">Track &amp; Stack · {epochLabel}</div>
      <Switch checked={t.stack.on} onChange={(on) => t.setStack({ ...t.stack, on })} label="Stack along a trial motion" hint="Moves every exposure of the visit by rate × time, so a slow mover adds up while stars smear" />
      {t.stack.on && (
        <div className="stack-controls">
          <label className="form-row">
            <span className="micro">Rate · {t.stack.rate.toFixed(2)}″/h</span>
            <input type="range" min={0} max={10} step={0.05} value={t.stack.rate} onChange={(e) => t.setStack({ ...t.stack, rate: Number(e.target.value) })} aria-label="Rate in arcseconds per hour" />
          </label>
          <label className="form-row">
            <span className="micro">Position angle · {t.stack.pa.toFixed(0)}°</span>
            <input type="range" min={0} max={359} step={1} value={t.stack.pa} onChange={(e) => t.setStack({ ...t.stack, pa: Number(e.target.value) })} aria-label="Position angle east of north" />
          </label>
          <Switch checked={t.stack.subtract} onChange={(subtract) => t.setStack({ ...t.stack, subtract })} label="Subtract the static sky" />
          {t.stackImg.loading && <p className="form-hint">Stacking…</p>}
          {t.stackImg.error && <p className="form-hint err">{t.stackImg.error}</p>}
          <button className="btn ghost small-btn" onClick={t.searchMotion} disabled={t.search.state === "running"}>
            {t.search.state === "running" ? <Loader2 size={13} className="spin" /> : <Play size={13} />} Search trial motions
          </button>
          {t.search.state === "error" && <p className="form-hint err">{t.search.message}</p>}
          {t.search.state === "done" && (
            <div className="cand-list short">
              {t.search.result.candidates.length ? (
                t.search.result.candidates.slice(0, 12).map((c) => (
                  <button key={c.id} className="cand-row" onClick={() => t.setStack({ ...t.stack, rate: c.rate_arcsec_per_hr, pa: c.pa_deg })} title="Use this motion">
                    <span className="cand-title">
                      <Square size={10} /> {c.rate_arcsec_per_hr.toFixed(2)}″/h · PA {c.pa_deg.toFixed(0)}°
                    </span>
                    <span className="cand-score num">S/N {c.snr.toFixed(1)}</span>
                    <span className="cand-sub">{c.sub_snr ? `thirds ${c.sub_snr.map((x) => x.toFixed(1)).join(" / ")}` : ""}</span>
                  </button>
                ))
              ) : (
                <p className="form-hint">No candidate above S/N 6 in {t.search.result.trials ?? "the"} trials.</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Measuring and display aids for the Display panel. */
export function MeasureTools(p: { ruler: boolean; onRuler: (v: boolean) => void; magnifier: boolean; onMagnifier: (v: boolean) => void; invert: boolean; onInvert: (v: boolean) => void }) {
  return (
    <div className="measure-tools">
      <div className="micro menu-title">Tools</div>
      <Switch checked={p.invert} onChange={p.onInvert} label="Invert" hint="Dark stars on white, as on a printed plate" />
      <Switch checked={p.magnifier} onChange={p.onMagnifier} label="Magnifier" hint="11×11 pixels under the cursor" />
      <Switch checked={p.ruler} onChange={p.onRuler} label="Ruler" hint="Click two points: distance and position angle" />
    </div>
  );
}
