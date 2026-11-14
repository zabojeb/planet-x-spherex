/**
 * Analysis tab: the full automatic analysis of one position (POST /api/sequences/{id}/analyze, polled). The
 * verdict comes first (plain-language findings with confidence, novelty), then compact expandable sections.
 * Each slot of the result may fail on its own and says so. Closing the tab withdraws the job.
 */
import { ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { analyzePoint, type Progress } from "../../api";
import { formatPm } from "../../astro";
import type { Finding, Meta, PointAnalysis, Slot } from "../../types";
import { ClassView, SpectrumView, VariabilityView } from "./SpectralPanel";

export interface AnalysisSubject {
  key: string;
  label: string;
  pos: { ra: number; dec: number; pmra?: number; pmdec?: number; ref_epoch?: number; name?: string | null };
}

const failed = <T,>(s: Slot<T>): s is { error: string } => !!s && typeof s === "object" && "error" in (s as object);
const ok = <T,>(s: Slot<T>): s is T => !!s && !failed(s);

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
  const [st, setSt] = useState<{ key: string; progress: Progress | null; res?: PointAnalysis; error?: string }>({ key: "", progress: null });
  useEffect(() => {
    const ctrl = new AbortController();
    setSt({ key: subject.key, progress: null });
    analyzePoint(meta.id, subject.pos, (progress) => !ctrl.signal.aborted && setSt((s) => (s.key === subject.key ? { ...s, progress } : s)), ctrl.signal)
      .then((res) => !ctrl.signal.aborted && setSt({ key: subject.key, progress: null, res }))
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

function Result({ r, meta, ylog }: { r: PointAnalysis; meta: Meta; ylog: boolean }) {
  const v = r.verdict;
  const id = r.identification;
  const mo = r.motion;
  const pg = r.periodogram;
  const tr = r.tracklets;
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
            {v.findings.map((f, i) => {
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
        summary={ok(id) ? [id.matches?.length ? `${id.matches.length} catalogue match${id.matches.length > 1 ? "es" : ""}` : "no catalogue match", id.solar_system?.length ? `${id.solar_system.length} solar-system object${id.solar_system.length > 1 ? "s" : ""}` : null].filter(Boolean).join(" · ") : null}
      >
        {ok(id) && (
          <table className="tbl compact">
            <tbody>
              {[...(id.matches ?? []), ...(id.simbad ?? []).map((m) => ({ ...m, catalog: "SIMBAD" }))].map((m, i) => (
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
            </tbody>
          </table>
        )}
      </Section>

      <Section
        title="Spectrum & classification"
        slot={r.spectrum ?? r.classification}
        summary={ok(r.classification) && r.classification.available ? r.classification.summary?.label ?? r.classification.templates?.best?.sptype : ok(r.spectrum) ? `${r.spectrum.bins.length || r.spectrum.relative_bins.length} bins` : null}
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
          ok(r.variability) ? (r.variability.epochs.some((e) => e.variable) ? "varies between epochs" : r.variability.available ? "constant within errors" : r.variability.reason) : null,
          ok(pg) && pg.best ? `best period ${pg.best.period_days.toFixed(2)} d` : failed(pg) ? null : null,
        ].filter(Boolean).join(" · ")}
      >
        {ok(r.variability) && <VariabilityView v={r.variability} meta={meta} />}
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

      <Section title="Nearby tracklets" slot={tr} summary={ok(tr) ? `${tr.items?.length ?? 0} within the search radius` : null}>
        {ok(tr) && (tr.items?.length ? (
          <table className="tbl compact">
            <thead>
              <tr>
                <th>Visit</th>
                <th className="num">n</th>
                <th className="num">Rate ″/h</th>
                <th className="num">PA</th>
                <th>Known as</th>
              </tr>
            </thead>
            <tbody>
              {tr.items.map((t) => (
                <tr key={t.id}>
                  <td>{t.epoch + 1}</td>
                  <td className="num">{t.n}</td>
                  <td className="num">{t.rate_arcsec_per_hr.toFixed(2)}</td>
                  <td className="num">{t.pa_deg.toFixed(0)}°</td>
                  <td>{t.known ?? <span className="muted">unknown</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted small">No intra-visit tracklets near this position.</p>)}
      </Section>
    </div>
  );
}
