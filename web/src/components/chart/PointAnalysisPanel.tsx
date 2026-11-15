/**
 * Analysis tab: the full automatic analysis of one position (POST /api/sequences/{id}/analyze, polled). The
 * verdict comes first (plain-language findings with confidence, novelty), then compact expandable sections.
 * Each slot of the result may fail on its own and says so. Closing the tab withdraws the job.
 */
import { ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { analyzePoint, type Progress } from "../../api";
import { formatPm } from "../../astro";
import type { CatalogMatch, ClassifyResult, Finding, Meta, PeriodogramResult, Photometry, PointAnalysis, Slot, SpectrumResult, VariabilityResult } from "../../types";
import { ClassView, SpectrumView, VariabilityView } from "./SpectralPanel";

export interface AnalysisSubject {
  key: string;
  label: string;
  pos: { ra: number; dec: number; pmra?: number; pmdec?: number; ref_epoch?: number; name?: string | null };
}

const failed = <T,>(s: Slot<T>): s is { error: string } => !!s && typeof s === "object" && "error" in (s as object);
const ok = <T,>(s: Slot<T>): s is T => !!s && !failed(s);

// ------------------------------------------------------------------ the server's result (ADR-0053, ADR-0056)
interface ServerMatch {
  id: string | number;
  sep_arcsec: number;
  mag?: number | null;
  mag_band?: string | null;
  pmra?: number | null;
  pmdec?: number | null;
  otype?: string | null;
  type?: string | null;
}
type ServerCat = { matches: ServerMatch[]; counterpart: boolean } | { error: string };
const CAT_LABEL = { gaia: "Gaia DR3", catwise: "CatWISE2020", "2mass": "2MASS", simbad: "SIMBAD" } as const;
type CatKey = keyof typeof CAT_LABEL;

interface ServerTrackletCandidate {
  kind: string;
  id?: string;
  n?: number;
  rate_arcsec_per_hr?: number;
  pa_deg?: number | null;
  sep_arcsec?: number;
  known?: string | null;
}

interface ServerPoint {
  id?: string;
  point?: { ra: number; dec: number; x?: number; y?: number };
  request?: { name?: string | null };
  identification?: Slot<Partial<Record<CatKey, ServerCat>>>;
  photometry?: Slot<{ photometry: Photometry[]; summary?: { n?: number } & Record<string, unknown> }>;
  spectrum?: Slot<{ spectrum?: SpectrumResult; classification?: ClassifyResult; variability?: VariabilityResult }>;
  motion?: Slot<{ combined?: { pmra: number; pmdec: number; pmra_err: number; pmdec_err: number; pm_total: number; pm_total_err: number | null } }>;
  periodogram?: Slot<PeriodogramResult>;
  colours?: Slot<{ ratio: number | null; long_snr: number | null; red_excess: boolean }>;
  known_objects?: Slot<{ matches?: { name: string; kind?: string; vmag?: number | null; sep_arcsec: number }[]; epochs?: { error?: string }[]; skipped_epochs?: number[] }>;
  tracklets?: Slot<{ available?: boolean; reason?: string; candidates?: ServerTrackletCandidate[] }>;
  verdict?: Slot<{ novelty?: number; headline?: string; findings?: { text: string; confidence: number; evidence?: Record<string, unknown> }[] }>;
}

interface TrackletRow {
  id: string;
  n?: number;
  rate_arcsec_per_hr: number;
  pa_deg?: number | null;
  sep_arcsec?: number;
  known?: string | null;
}

/** What the panel shows: the server's slots reshaped, plus the catalogues that could not be asked. */
type PanelResult = Omit<PointAnalysis, "tracklets"> & {
  tracklets?: Slot<{ items: TrackletRow[]; reason?: string }>;
  unavailable: Record<string, string>;
};

const num = (v: number) => (Math.abs(v) >= 100 || Number.isInteger(v) ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(2));

/** A finding's evidence object as one short line ("sep 1.2″ · pm total 10393"). */
function evidenceText(ev?: Record<string, unknown>): string | null {
  if (!ev || typeof ev !== "object") return null;
  const parts = Object.entries(ev)
    .filter(([, v]) => v != null && v !== false && v !== "" && typeof v !== "object")
    .map(([k, v]) => {
      const key = k.replace(/_arcsec$/, "").replace(/_mas_yr$/, " (mas/yr)").replace(/_/g, " ");
      const val = typeof v === "number" ? num(v) + (k.endsWith("_arcsec") ? "″" : "") : v === true ? "yes" : String(v);
      return `${key} ${val}`;
    });
  return parts.length ? parts.join(" · ") : null;
}

/** The server's result in the panel's slots: one counterpart per catalogue, the spectral analysis split into
 *  spectrum / classification / variability, the combined motion fit compared with the catalogue motion, the
 *  known asteroids and comets, and the field-analysis tracklets near the point. */
function fromServer(raw: ServerPoint): PanelResult {
  const unavailable: Record<string, string> = {};
  const out: PanelResult = { id: raw.id, point: raw.point && { ...raw.point, name: raw.request?.name ?? null }, unavailable };

  const id = raw.identification;
  if (failed(id)) out.identification = id;
  else if (id) {
    const pick = (k: CatKey): CatalogMatch | null => {
      const c = id[k];
      if (!c) return null;
      if ("error" in c) {
        unavailable[CAT_LABEL[k]] = c.error;
        return null;
      }
      const m = c.counterpart ? c.matches?.[0] : undefined;
      return m
        ? { catalog: CAT_LABEL[k], id: m.id, name: String(m.id), type: m.otype ?? m.type ?? null, mag: m.mag ?? null, mag_band: m.mag_band ?? null, sep_arcsec: m.sep_arcsec, pmra: m.pmra ?? null, pmdec: m.pmdec ?? null }
        : null;
    };
    const ko = raw.known_objects;
    if (failed(ko)) unavailable["Asteroids & comets (JPL)"] = ko.error;
    else if (ko?.epochs?.length && ko.epochs.every((e) => e.error)) unavailable["Asteroids & comets (JPL)"] = ko.epochs[0].error!;
    const byName = new Map<string, { name: string; kind?: string; vmag?: number | null; sep_arcsec?: number | null }>();
    for (const k of ok(ko) ? ko.matches ?? [] : []) if (!byName.has(k.name) || (byName.get(k.name)!.sep_arcsec ?? Infinity) > k.sep_arcsec) byName.set(k.name, k);
    const simbad = pick("simbad");
    out.identification = {
      matches: [pick("gaia"), pick("catwise"), pick("2mass")].filter((m): m is CatalogMatch => !!m),
      simbad: simbad ? [simbad] : [],
      solar_system: [...byName.values()],
    };
  }

  const ph = raw.photometry;
  out.photometry = failed(ph) ? ph : ph ? { n: ph.summary?.n ?? ph.photometry.length, records: ph.photometry, summary: ph.summary } : undefined;

  const sp = raw.spectrum;
  if (failed(sp)) out.spectrum = sp;
  else if (sp) {
    out.spectrum = sp.spectrum;
    out.classification = sp.classification;
    out.variability = sp.variability;
  }

  const mo = raw.motion;
  if (failed(mo)) out.motion = mo;
  else if (mo?.combined) {
    const c = mo.combined;
    const ident = ok(out.identification) ? out.identification : null;
    const cm = ident?.matches?.find((m) => m.catalog === "Gaia DR3" && m.pmra != null) ?? ident?.matches?.find((m) => m.catalog === "CatWISE2020" && m.pmra != null);
    const err = Math.hypot(c.pmra_err ?? 0, c.pmdec_err ?? 0) / Math.SQRT2;
    out.motion = {
      pmra: c.pmra, pmdec: c.pmdec, pmra_err: c.pmra_err, pmdec_err: c.pmdec_err, pm_total: c.pm_total,
      significance: c.pm_total_err ? c.pm_total / c.pm_total_err : undefined,
      catalog: cm ? { catalog: cm.catalog ?? "catalogue", pmra: cm.pmra ?? 0, pmdec: cm.pmdec ?? 0 } : null,
      discrepancy_sigma: cm && err > 0 ? Math.hypot(c.pmra - (cm.pmra ?? 0), c.pmdec - (cm.pmdec ?? 0)) / Math.hypot(err, cm.catalog === "Gaia DR3" ? 5 : 30) : null,
    };
  }

  out.periodogram = raw.periodogram;
  const col = raw.colours;
  if (failed(col)) out.red_excess = col;
  else if (col && col.ratio != null) out.red_excess = { ratio_long_short: col.ratio, excess_snr: col.long_snr ?? undefined, note: col.red_excess ? "very red: bright at 3.8–5 µm for its flux below 2.4 µm" : undefined };

  const tr = raw.tracklets;
  if (failed(tr)) out.tracklets = tr;
  else if (tr) {
    const items = (tr.candidates ?? [])
      .filter((c) => c.kind === "tracklet" && c.rate_arcsec_per_hr != null)
      .map((c, i) => ({ id: c.id ?? String(i), n: c.n, rate_arcsec_per_hr: c.rate_arcsec_per_hr!, pa_deg: c.pa_deg, sep_arcsec: c.sep_arcsec, known: c.known ?? null }));
    out.tracklets = { items, reason: tr.available === false ? tr.reason : undefined };
  }

  const v = raw.verdict;
  if (failed(v)) out.verdict = v;
  else if (v) out.verdict = { summary: v.findings?.some((f) => f.text === v.headline) ? undefined : v.headline, findings: (v.findings ?? []).map((f) => ({ text: f.text, confidence: f.confidence, evidence: evidenceText(f.evidence) })), novelty: v.novelty ?? null };
  return out;
}

function confidence(c: Finding["confidence"]): { pct: number; label: string } {
  if (typeof c === "number") return { pct: Math.round(c * 100), label: c >= 0.8 ? "high" : c >= 0.5 ? "medium" : "low" };
  const pct = c === "high" ? 90 : c === "medium" ? 60 : 30;
  return { pct, label: c };
}

function stageText(p: Progress | null): string {
  if (!p) return "Starting";
  if (p.stage === "busy") return `Many users right now · retrying in ${p.retryIn ?? 1}s`;
  if (p.ahead) return `Waiting for a free worker · ${p.ahead} ahead`;
  const step = p.total ? ` · ${p.done}/${p.total}` : "";
  return `${p.stage === "queued" ? "Queued" : p.stage.replace(/_/g, " ")}${step}`;
}

export function PointAnalysisPanel({ subject, meta, ylog, onClose }: { subject: AnalysisSubject; meta: Meta; ylog: boolean; onClose: () => void }) {
  const [st, setSt] = useState<{ key: string; progress: Progress | null; res?: PanelResult; error?: string }>({ key: "", progress: null });
  useEffect(() => {
    const ctrl = new AbortController();
    setSt({ key: subject.key, progress: null });
    analyzePoint(meta.id, subject.pos, (progress) => !ctrl.signal.aborted && setSt((s) => (s.key === subject.key ? { ...s, progress } : s)), ctrl.signal)
      .then((raw) => !ctrl.signal.aborted && setSt({ key: subject.key, progress: null, res: fromServer(raw as unknown as ServerPoint) }))
      .catch((e) => !ctrl.signal.aborted && setSt({ key: subject.key, progress: null, error: String(e?.message || e) }));
    return () => ctrl.abort(); // withdraws the job when the tab closes or the subject changes
  }, [subject.key, meta.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const cur = st.key === subject.key ? st : { progress: null, res: undefined, error: undefined };
  if (cur.error) return <p className="chart-empty static">{cur.error}</p>;
  if (!cur.res) {
    return (
      <div className="pa-running" role="status" aria-live="polite">
        <Loader2 size={16} className="spin" />
        <div>
          <div>Analysing {subject.label}</div>
          <div className="muted small">{stageText(cur.progress)} · identification, photometry, spectrum, motion, variability, tracklets</div>
          {cur.progress?.total ? (
            <div className="pa-bar">
              <span style={{ width: `${Math.max(3, (100 * cur.progress.done) / cur.progress.total)}%` }} />
            </div>
          ) : null}
        </div>
        <button className="btn ghost small-btn" onClick={onClose}>Cancel</button>
      </div>
    );
  }
  return <Result r={cur.res} meta={meta} ylog={ylog} />;
}

function Section({ title, summary, slot, children, open }: { title: string; summary?: ReactNode; slot: Slot<unknown>; children?: ReactNode; open?: boolean }) {
  if (slot == null) return null;
  return (
    <details className="pa-section" open={open}>
      <summary>
        <ChevronRight size={14} className="pa-chev" />
        <span className="pa-title">{title}</span>
        <span className="pa-sum muted small">{failed(slot) ? <span className="err">{slot.error}</span> : summary}</span>
      </summary>
      {!failed(slot) && <div className="pa-body">{children}</div>}
    </details>
  );
}

function Result({ r, meta, ylog }: { r: PanelResult; meta: Meta; ylog: boolean }) {
  const v = r.verdict;
  const id = r.identification;
  const mo = r.motion;
  const pg = r.periodogram;
  const tr = r.tracklets;
  const nMatch = ok(id) ? (id.matches?.length ?? 0) + (id.simbad?.length ?? 0) : 0;
  const novelty = ok(v) ? v.novelty : null;
  const noveltyText = typeof novelty === "number" ? (novelty >= 0.7 ? "possibly new" : novelty >= 0.4 ? "unusual" : "known / ordinary") : novelty;
  return (
    <div className="pa">
      {failed(v) ? (
        <p className="err">{v.error}</p>
      ) : ok(v) ? (
        <div className="pa-verdict">
          <div className="pa-verdict-head">
            <div className="micro">Verdict</div>
            {noveltyText && (
              <span className={`badge novelty${typeof novelty === "number" && novelty >= 0.7 ? " hot" : ""}`} title={v.novelty_note ?? "How unexpected this is given the catalogues"}>
                {noveltyText}
                {typeof novelty === "number" && <span className="num"> {Math.round(novelty * 100)}</span>}
              </span>
            )}
          </div>
          {v.summary && <p className="pa-summary">{v.summary}</p>}
          <ul className="pa-findings">
            {(v.findings ?? []).map((f, i) => {
              const c = confidence(f.confidence);
              return (
                <li key={i}>
                  <span className={`conf ${c.label}`} title={`Confidence ${c.pct}%`}>
                    <span style={{ width: `${c.pct}%` }} />
                  </span>
                  <span>
                    {f.text}
                    {f.evidence && <span className="pa-evidence">{Array.isArray(f.evidence) ? f.evidence.join(" · ") : f.evidence}</span>}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      <Section
        title="Identification"
        slot={id}
        open
        summary={ok(id) ? [nMatch ? `${nMatch} catalogue match${nMatch > 1 ? "es" : ""}` : "no catalogue match", id.solar_system?.length ? `${id.solar_system.length} solar-system object${id.solar_system.length > 1 ? "s" : ""}` : null].filter(Boolean).join(" · ") : null}
      >
        {ok(id) && (
          <table className="tbl compact">
            <tbody>
              {[...(id.matches ?? []), ...(id.simbad ?? [])].map((m, i) => (
                <tr key={`${m.catalog}${m.id}${i}`}>
                  <td className="muted">{m.catalog}</td>
                  <td className="mono">{m.name ?? String(m.id)}{m.type ? <span className="muted"> · {m.type}</span> : null}</td>
                  <td className="num">{m.mag != null ? `${m.mag_band ?? ""} ${m.mag.toFixed(2)}` : ""}</td>
                  <td className="num">{m.sep_arcsec != null ? `${m.sep_arcsec.toFixed(1)}″` : ""}</td>
                </tr>
              ))}
              {(id.solar_system ?? []).map((o) => (
                <tr key={o.name}>
                  <td className="muted">{o.kind ?? "Solar system"}</td>
                  <td>{o.name}</td>
                  <td className="num">{o.vmag != null ? `V ${o.vmag.toFixed(1)}` : ""}</td>
                  <td className="num">{o.sep_arcsec != null ? `${o.sep_arcsec.toFixed(1)}″` : ""}</td>
                </tr>
              ))}
              {Object.entries(r.unavailable).map(([cat, e]) => (
                <tr key={`na-${cat}`}>
                  <td className="muted">{cat}</td>
                  <td colSpan={3} className="err small" title={e}>not checked: {e.length > 90 ? `${e.slice(0, 90)}…` : e}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section
        title="Spectrum & classification"
        slot={r.spectrum ?? r.classification}
        summary={ok(r.classification) && r.classification.available ? r.classification.summary?.label ?? r.classification.templates?.best?.sptype : ok(r.spectrum) && (r.spectrum.bins?.length || r.spectrum.relative_bins?.length) ? `${r.spectrum.bins.length || r.spectrum.relative_bins.length} bins` : ok(r.classification) ? r.classification.reason : null}
      >
        {ok(r.spectrum) && <SpectrumView s={r.spectrum} meta={meta} ylog={ylog} />}
        {ok(r.classification) && <ClassView c={r.classification} s={ok(r.spectrum) ? r.spectrum : null} meta={meta} ylog={ylog} />}
      </Section>

      <Section
        title="Motion vs catalogue"
        slot={mo}
        summary={ok(mo) ? `${formatPm(mo.pmra, mo.pmdec)}${mo.significance != null ? ` · ${mo.significance.toFixed(1)}σ` : ""}${mo.discrepancy_sigma != null ? ` · ${mo.discrepancy_sigma.toFixed(1)}σ from ${mo.catalog?.catalog ?? "catalogue"}` : ""}` : null}
      >
        {ok(mo) && (
          <table className="tbl compact">
            <thead>
              <tr>
                <th />
                <th className="num">μα* (mas/yr)</th>
                <th className="num">μδ (mas/yr)</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>SPHEREx (this sequence)</td>
                <td className="num">{mo.pmra.toFixed(0)}{mo.pmra_err != null && <span className="muted"> ± {mo.pmra_err.toFixed(0)}</span>}</td>
                <td className="num">{mo.pmdec.toFixed(0)}{mo.pmdec_err != null && <span className="muted"> ± {mo.pmdec_err.toFixed(0)}</span>}</td>
              </tr>
              {mo.catalog && (
                <tr>
                  <td>{mo.catalog.catalog}</td>
                  <td className="num">{mo.catalog.pmra.toFixed(0)}</td>
                  <td className="num">{mo.catalog.pmdec.toFixed(0)}</td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </Section>

      <Section
        title="Light curve & period"
        slot={r.variability ?? pg}
        summary={[
          ok(r.variability) ? (!r.variability.available ? r.variability.reason : (r.variability.epochs ?? []).some((e) => e.variable) ? "varies between epochs" : "constant within errors") : null,
          ok(pg) && pg.best ? `best period ${pg.best.period_days.toFixed(2)} d` : failed(pg) ? null : null,
        ].filter(Boolean).join(" · ")}
      >
        {ok(r.variability) && r.variability.available && r.variability.epochs?.length ? <VariabilityView v={r.variability} meta={meta} /> : null}
        {ok(pg) && pg.best && (
          <p className="small">
            Lomb–Scargle best period <span className="num">{pg.best.period_days.toFixed(2)} d</span>, power {pg.best.power.toFixed(2)}
            {pg.best.false_alarm_probability != null && <> · false-alarm {pg.best.false_alarm_probability.toExponential(0)}</>}
            {pg.peaks?.length > 1 && <span className="muted"> · other peaks {pg.peaks.slice(1, 4).map((q) => `${q.period_days.toFixed(1)} d`).join(", ")}</span>}
          </p>
        )}
      </Section>

      <Section title="Red excess" slot={r.red_excess} summary={ok(r.red_excess) ? (r.red_excess.ratio_long_short != null ? `long/short ${r.red_excess.ratio_long_short.toFixed(2)}` : r.red_excess.note) : null}>
        {ok(r.red_excess) && r.red_excess.note && <p className="small">{r.red_excess.note}</p>}
      </Section>

      <Section title="Nearby tracklets" slot={tr} summary={ok(tr) ? (tr.reason ?? `${tr.items.length} within the search radius`) : null}>
        {ok(tr) && (tr.items.length ? (
          <table className="tbl compact">
            <thead>
              <tr>
                <th className="num">Sep</th>
                <th className="num">n</th>
                <th className="num">Rate ″/h</th>
                <th className="num">PA</th>
                <th>Known as</th>
              </tr>
            </thead>
            <tbody>
              {tr.items.map((t) => (
                <tr key={t.id}>
                  <td className="num">{t.sep_arcsec != null ? `${t.sep_arcsec.toFixed(1)}″` : ""}</td>
                  <td className="num">{t.n ?? ""}</td>
                  <td className="num">{t.rate_arcsec_per_hr.toFixed(2)}</td>
                  <td className="num">{t.pa_deg != null ? `${t.pa_deg.toFixed(0)}°` : ""}</td>
                  <td>{t.known ?? <span className="muted">unknown</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : tr.reason ? null : <p className="muted small">No intra-visit tracklets near this position.</p>)}
      </Section>
    </div>
  );
}
