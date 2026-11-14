/**
 * Spectrum tab: the server's spectral analysis (binned spectrum, continuum, features, indices), a
 * classification card (templates, blackbody, synthetic vs catalogue magnitudes) and spectral variability
 * (ratio spectra per epoch). Everything is fetched through api.ts only when this tab opens; a measured
 * catalogue source is analysed with POST /api/spectrum/analyze. Without the endpoints (older server) the
 * per-exposure spectrum from the photometry table is shown instead.
 */
import { AlertTriangle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiError, analyzeSpectrum, fetchClassification, fetchSpectrum, fetchVariability } from "../../api";
import { formatDate } from "../../astro";
import { SERIES, STATUS, epochColor } from "../../colors";
import type { ClassifyResult, Meta, Photometry, SpecBin, SpectrumResult, Target, VariabilityResult } from "../../types";
import { Segmented } from "../ui/primitives";
import { ChartFrame, formatNumber, type ChartLine, type ChartPoint, type LegendItem, type Scales } from "./ChartFrame";
import { SpectrumChart } from "./SpectrumChart";

type View = "spectrum" | "class" | "var";
type Load<T> = { state: "loading" } | { state: "ready"; data: T } | { state: "missing" } | { state: "error"; message: string };

interface Props {
  meta: Meta;
  epoch: number;
  ylog: boolean;
  target: Target | null;
  source?: { key: string; label: string; photometry: Photometry[] } | null;
  /** The legacy per-exposure chart needs the photometry table: asked for only when the endpoints are missing. */
  onNeedPhotometry?: () => void;
  photometryReady: boolean;
}

const unsupported = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.status === 405);
const X_DOMAIN: [number, number] = [0.7, 5.05];
const ticks = (vs: number[]) => vs.map((v) => ({ value: v, label: String(v) }));
const WIDE_TICKS = ticks([0.75, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5]);
const NARROW_TICKS = ticks([1, 2, 3, 4, 5]);

const xTicks = () => (typeof window !== "undefined" && window.innerWidth < 600 ? NARROW_TICKS : WIDE_TICKS);

/** Load one analysis; `key` changes restart it. */
function useLoad<T>(key: string | null, make: () => Promise<T>): Load<T> {
  const [st, setSt] = useState<{ key: string | null; v: Load<T> }>({ key: null, v: { state: "loading" } });
  useEffect(() => {
    if (!key) return;
    let live = true;
    setSt({ key, v: { state: "loading" } });
    make().then(
      (data) => live && setSt({ key, v: { state: "ready", data } }),
      (e) => live && setSt({ key, v: unsupported(e) ? { state: "missing" } : { state: "error", message: String(e?.message || e) } }),
    );
    return () => {
      live = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return st.key === key ? st.v : { state: "loading" };
}

export function SpectralPanel(p: Props) {
  const [view, setView] = useState<View>("spectrum");
  const id = p.meta.id;
  const src = p.source ?? null;

  // A measured source: one POST gives all three analyses. The target: three cached GETs, each on demand.
  const analysis = useLoad(src ? `a|${id}|${src.key}` : null, () => analyzeSpectrum(`${id}|${src!.key}`, src!.photometry, null));
  const spectrumT = useLoad(!src ? `s|${id}` : null, () => fetchSpectrum(id));
  const classT = useLoad(!src && view === "class" ? `c|${id}` : null, () => fetchClassification(id, true));
  const varT = useLoad(!src && view === "var" ? `v|${id}` : null, () => fetchVariability(id));

  const pick = <T,>(fromAnalysis: (a: { spectrum: SpectrumResult | null; classification: ClassifyResult | null; variability: VariabilityResult | null }) => T | null, own: Load<T>): Load<T> => {
    if (!src) return own;
    if (analysis.state !== "ready") return analysis as Load<T>;
    const v = fromAnalysis(analysis.data);
    return v ? { state: "ready", data: v } : { state: "error", message: "No result for this source" };
  };
  const spectrum = pick((a) => a.spectrum, spectrumT);
  const classification = pick((a) => a.classification, classT);
  const variability = pick((a) => a.variability, varT);

  const legacy = spectrum.state === "missing";
  useEffect(() => {
    if (legacy) p.onNeedPhotometry?.();
  }, [legacy]); // eslint-disable-line react-hooks/exhaustive-deps

  if (legacy) {
    return p.photometryReady ? <SpectrumChart meta={p.meta} epoch={p.epoch} ylog={p.ylog} /> : <div className="skeleton" style={{ height: 260 }} aria-label="Loading measurements" />;
  }

  return (
    <div className="spectral">
      <Segmented<View>
        label="Spectral view"
        size="sm"
        value={view}
        onChange={setView}
        options={[
          { value: "spectrum", label: "Spectrum" },
          { value: "class", label: "Classification" },
          { value: "var", label: "Variability" },
        ]}
      />
      {view === "spectrum" && <Gate load={spectrum}>{(s) => <SpectrumView s={s} meta={p.meta} ylog={p.ylog} />}</Gate>}
      {view === "class" && (
        <Gate load={classification}>{(c) => <ClassView c={c} s={spectrum.state === "ready" ? spectrum.data : null} meta={p.meta} ylog={p.ylog} />}</Gate>
      )}
      {view === "var" && <Gate load={variability}>{(v) => <VariabilityView v={v} meta={p.meta} />}</Gate>}
    </div>
  );
}

function Gate<T>({ load, children }: { load: Load<T>; children: (d: T) => React.ReactNode }) {
  if (load.state === "loading") return <div className="skeleton" style={{ height: 240 }} aria-label="Analysing" />;
  if (load.state === "missing") return <p className="chart-empty static">This analysis is not available on this server yet.</p>;
  if (load.state === "error") return <p className="chart-empty static">{load.message}</p>;
  return <>{children(load.data)}</>;
}

// ------------------------------------------------------------------ shared pieces

/** Detector bands (alternating shading, labelled) and, optionally, the detected spectral features. */
function SpectralBackground({ s, meta, features }: { s: Scales; meta: Meta; features?: SpectrumResult["features"] }) {
  const shown = (features ?? []).filter((f) => f.detected).sort((a, b) => (b.significance ?? 0) - (a.significance ?? 0));
  // Labels in two rows; a label that would collide with its row neighbour is skipped (the table lists all).
  const taken: [number[], number[]] = [[], []];
  const labelled = shown.map((f) => {
    const x = s.x(f.center);
    const row = taken[0].every((t) => Math.abs(t - x) > 46) ? 0 : taken[1].every((t) => Math.abs(t - x) > 46) ? 1 : -1;
    if (row === 0 || row === 1) taken[row].push(x);
    return { f, x, row };
  });
  return (
    <g>
      {meta.bands.map((b, i) => (
        <g key={b.id}>
          <rect x={s.x(b.wl_min)} y={s.top} width={Math.max(0, s.x(b.wl_max) - s.x(b.wl_min))} height={s.bottom - s.top} fill={i % 2 ? "rgba(255,255,255,0.02)" : "rgba(255,255,255,0.045)"} />
          <text className="band-label" x={(s.x(b.wl_min) + s.x(b.wl_max)) / 2} y={s.top - 27} textAnchor="middle">
            {b.id}
          </text>
        </g>
      ))}
      {labelled.map(({ f, x, row }) => (
        <g key={f.id}>
          <rect x={s.x(f.band[0])} y={s.top} width={Math.max(1, s.x(f.band[1]) - s.x(f.band[0]))} height={s.bottom - s.top} className="feature-band" />
          {row >= 0 && (
            <text className="feature-label" x={x} y={s.top - (row ? 4 : 15)} textAnchor="middle">
              {f.name.replace(/\s*[\d.]+\s*µm$/, "")}
            </text>
          )}
        </g>
      ))}
    </g>
  );
}

function yRange(values: number[], ylog: boolean): [number, number] {
  const ys = values.filter((v) => Number.isFinite(v) && (!ylog || v > 0)).sort((a, b) => a - b);
  if (!ys.length) return ylog ? [0.01, 1] : [0, 1];
  let lo = ys[0];
  let hi = ys[ys.length - 1];
  if (ylog) return [Math.max(lo / 1.6, hi / 1e5), hi * 1.6];
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.1 || 1;
  lo = Math.min(0, lo - pad);
  hi += pad;
  return [lo, hi];
}

function binPoints(bins: SpecBin[], ylog: boolean, color: string): ChartPoint[] {
  return bins
    .filter((b) => Number.isFinite(b.flux_mjy) && (!ylog || b.flux_mjy > 0))
    .map((b) => {
      const err = b.err_mjy ?? 0;
      return {
        key: `${b.band}${b.bin}`,
        x: b.wl,
        y: b.flux_mjy,
        yLo: ylog ? Math.max(b.flux_mjy - err, b.flux_mjy * 0.05) : b.flux_mjy - err,
        yHi: b.flux_mjy + err,
        color,
        tooltip: [
          `${b.band} · ${b.wl_lo.toFixed(3)}–${b.wl_hi.toFixed(3)} µm`,
          `${formatNumber(b.flux_mjy)} ± ${formatNumber(err)} ${b.relative ? "(relative)" : "mJy"}`,
          `${b.n} measurement${b.n === 1 ? "" : "s"}${b.n_rejected ? `, ${b.n_rejected} rejected` : ""}${b.chi2 != null ? ` · χ²ᵣ ${b.chi2.toFixed(1)}` : ""}`,
        ],
      };
    });
}

/** A model curve with gaps (nulls) as separate line segments. */
function segments(key: string, wl: number[], flux: (number | null)[], color: string, ylog: boolean, dash = false): ChartLine[] {
  const out: ChartLine[] = [];
  let cur: [number, number][] = [];
  wl.forEach((x, i) => {
    const y = flux[i];
    if (y == null || !Number.isFinite(y) || (ylog && y <= 0)) {
      if (cur.length > 1) out.push({ key: `${key}${out.length}`, points: cur, color, dash });
      cur = [];
    } else cur.push([x, y]);
  });
  if (cur.length > 1) out.push({ key: `${key}${out.length}`, points: cur, color, dash });
  return out;
}

// ------------------------------------------------------------------ spectrum

export function SpectrumView({ s, meta, ylog }: { s: SpectrumResult; meta: Meta; ylog: boolean }) {
  const relative = !s.bins.length && s.relative_bins.length > 0;
  const bins = relative ? s.relative_bins : s.bins;
  const { points, lines, yDomain } = useMemo(() => {
    const pts = binPoints(bins, ylog, SERIES.blue);
    const cont = bins.filter((b) => b.continuum_mjy != null);
    const ls: ChartLine[] = cont.length > 1 && s.continuum?.available
      ? segments("cont", cont.map((b) => b.wl), cont.map((b) => b.continuum_mjy ?? null), "var(--text-3)", ylog, true)
      : [];
    return { points: pts, lines: ls, yDomain: yRange(pts.flatMap((q) => [q.y, q.yHi ?? q.y]), ylog) };
  }, [bins, ylog, s.continuum?.available]);

  const detected = s.features.filter((f) => f.detected).sort((a, b) => a.center - b.center);
  const indices = Object.entries(s.indices).filter(([, v]) => v.value != null);
  const legend: LegendItem[] = [{ label: relative ? "binned (relative)" : "binned spectrum", color: SERIES.blue }];
  if (lines.length) legend.push({ label: s.continuum?.reliable ? "continuum" : "continuum (indicative)", color: "var(--text-3)", kind: "dash" });
  if (detected.length) legend.push({ label: "detected feature", color: SERIES.orange, kind: "hollow-square" });

  return (
    <div className="spectral-view">
      <ChartFrame
        subtitle={`${s.n_measurements} measurements binned per detector${relative ? " · relative fluxes only (saturated)" : ""}.`}
        xDomain={X_DOMAIN}
        yDomain={yDomain}
        yLog={ylog}
        xLabel="Wavelength (µm)"
        yLabel={relative ? "Relative flux" : "Flux density (mJy)"}
        xTicks={xTicks()}
        points={points}
        lines={lines}
        legend={legend}
        note={s.notes?.length ? s.notes.join(" ") : null}
        empty={points.length ? null : "No usable measurements"}
        background={(sc) => <SpectralBackground s={sc} meta={meta} features={s.features} />}
      />
      {(detected.length > 0 || indices.length > 0) && (
        <div className="spectral-tables">
          {detected.length > 0 && (
            <table className="tbl compact">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th className="num">Depth</th>
                  <th className="num">σ</th>
                </tr>
              </thead>
              <tbody>
                {detected.map((f) => (
                  <tr key={f.id}>
                    <td>{f.name}</td>
                    <td className="num">{f.depth != null ? `${(f.kind === "emission" ? "+" : "")}${f.depth.toFixed(2)}` : "—"}</td>
                    <td className="num">{f.significance != null ? f.significance.toFixed(0) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {indices.length > 0 && (
            <table className="tbl compact">
              <thead>
                <tr>
                  <th>Index</th>
                  <th className="num">Value</th>
                </tr>
              </thead>
              <tbody>
                {indices.map(([k, v]) => (
                  <tr key={k} title={v.description}>
                    <td className="mono">{k}</td>
                    <td className="num">
                      {v.value!.toFixed(2)}
                      {v.err != null && <span className="muted"> ± {v.err.toFixed(2)}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ classification

const FAMILY: Record<string, string> = {
  star: "Star",
  late_m_dwarf: "Late-M dwarf",
  brown_dwarf: "Brown dwarf",
  composite: "Star + cold companion",
  yso: "Young stellar object",
  yso_candidate: "YSO candidate",
  nonstellar: "Non-stellar (AGN/QSO or YSO)",
};

export function ClassView({ c, s, meta, ylog }: { c: ClassifyResult; s: SpectrumResult | null; meta: Meta; ylog: boolean }) {
  const curves = c.model_curves ?? {};
  const { points, lines, yDomain } = useMemo(() => {
    const pts = s ? binPoints(s.bins, ylog, SERIES.blue) : [];
    const ls: ChartLine[] = [];
    const t = curves.best_template;
    if (t) ls.push(...segments("tpl", t.wl, t.flux_mjy, SERIES.orange, ylog));
    const bb = curves.blackbody;
    if (bb) ls.push(...segments("bb", bb.wl, bb.flux_mjy, SERIES.aqua, ylog, true));
    const ys = [...pts.map((q) => q.y), ...ls.flatMap((l) => l.points.map((q) => q[1]))];
    return { points: pts, lines: ls, yDomain: yRange(ys, ylog) };
  }, [s, curves, ylog]);

  if (!c.available) return <p className="chart-empty static">{c.reason ?? "No calibrated measurements to classify."}</p>;
  const best = c.templates?.best ?? null;
  const ranked = (c.templates?.ranked ?? []).slice(0, 5);
  const mags = Object.entries(c.synthetic_photometry ?? {});
  const cat = c.catalog_comparison?.bands ?? {};
  const legend: LegendItem[] = [];
  if (points.length) legend.push({ label: "binned spectrum", color: SERIES.blue });
  if (curves.best_template) legend.push({ label: `template ${best?.sptype ?? ""}`.trim(), color: SERIES.orange, kind: "line" });
  if (curves.blackbody && c.blackbody) legend.push({ label: `blackbody ${Math.round(c.blackbody.teff_k)} K`, color: SERIES.aqua, kind: "dash" });

  return (
    <div className="class-view">
      <div className="class-card">
        <div className="class-head">
          <div>
            <div className="micro">Classification</div>
            <div className="class-label">{c.summary?.label ?? best?.sptype ?? "Unclassified"}</div>
            {c.summary?.family && <div className="muted small">{FAMILY[c.summary.family] ?? c.summary.family}</div>}
          </div>
          <dl className="class-facts">
            {c.blackbody && (
              <>
                <dt>T<sub>eff</sub> (blackbody)</dt>
                <dd className="num">
                  {Math.round(c.blackbody.teff_k)} K
                  {c.blackbody.teff_range_k && <span className="muted"> ({c.blackbody.teff_range_k[0]}–{c.blackbody.teff_range_k[1]})</span>}
                </dd>
              </>
            )}
            {c.ir_index && (
              <>
                <dt>IR index α</dt>
                <dd className="num">
                  {c.ir_index.alpha.toFixed(2)} ± {c.ir_index.alpha_err.toFixed(2)} <span className="muted">{c.ir_index.lada_class}</span>
                </dd>
              </>
            )}
          </dl>
        </div>
        {ranked.length > 0 && (
          <ul className="prob-list" aria-label="Best-matching templates">
            {ranked.map((t) => (
              <li key={t.name} title={`${t.source} · χ²ᵣ ${t.chi2_red.toFixed(1)} over ${t.n_bins} bins`}>
                <span className="prob-name">{t.sptype}</span>
                <span className="prob-bar">
                  <span style={{ width: `${Math.max(1, t.probability * 100)}%` }} />
                </span>
                <span className="prob-val num">{(t.probability * 100).toFixed(t.probability < 0.1 ? 1 : 0)}%</span>
              </li>
            ))}
          </ul>
        )}
        {c.summary?.notes?.length ? <p className="fine">{c.summary.notes.join(" ")}</p> : null}
      </div>
      {(points.length > 0 || lines.length > 0) && (
        <ChartFrame
          xDomain={X_DOMAIN}
          yDomain={yDomain}
          yLog={ylog}
          xLabel="Wavelength (µm)"
          yLabel="Flux density (mJy)"
          xTicks={xTicks()}
          points={points}
          lines={lines}
          legend={legend}
          height={240}
          note={c.templates?.max_wl_um ? `Templates reach ${c.templates.max_wl_um.toFixed(2)} µm; the blackbody covers the full range.` : null}
          background={(sc) => <SpectralBackground s={sc} meta={meta} />}
        />
      )}
      {mags.length > 0 && (
        <table className="tbl compact mags">
          <thead>
            <tr>
              <th>Band</th>
              <th className="num">Synthetic</th>
              <th className="num">Catalogue</th>
              <th className="num">Δ mag</th>
            </tr>
          </thead>
          <tbody>
            {mags.map(([band, m]) => {
              const k = cat[band];
              return (
                <tr key={band} className={k?.suspect ? "suspect" : undefined}>
                  <td>{band}</td>
                  <td className="num">
                    {m.mag != null ? m.mag.toFixed(2) : "—"}
                    {m.extrapolated && <span className="muted" title={`Extrapolated: ${(m.coverage * 100).toFixed(0)}% of the band is covered`}> *</span>}
                  </td>
                  <td className="num">{k?.catalog != null ? k.catalog.toFixed(2) : "—"}</td>
                  <td className="num">
                    {k?.delta_mag != null ? `${k.delta_mag > 0 ? "+" : ""}${k.delta_mag.toFixed(2)}` : "—"}
                    {k?.suspect && (
                      <span className="suspect-mark" title="Differs by more than 0.75 mag: wrong match, blend, variability or a fast mover">
                        <AlertTriangle size={12} /> suspect
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {mags.some(([, m]) => m.extrapolated) && <p className="fine">* extrapolated past 5 µm with a power law.</p>}
    </div>
  );
}

// ------------------------------------------------------------------ variability

export function VariabilityView({ v, meta }: { v: VariabilityResult; meta: Meta }) {
  const nE = meta.epochs.length;
  const eps = v.epochs.filter((e) => e.epoch !== v.reference_epoch);
  const { points, yDomain } = useMemo(() => {
    const pts: ChartPoint[] = [];
    const ys: number[] = [];
    for (const e of eps)
      for (const r of e.ratios ?? []) {
        if (!Number.isFinite(r.ratio)) continue;
        const sig = Math.abs(r.z) >= 3;
        pts.push({
          key: `${e.epoch}-${r.band}${r.bin}`,
          x: r.wl,
          y: r.ratio,
          yLo: r.ratio - r.err,
          yHi: r.ratio + r.err,
          color: epochColor(e.epoch, nE),
          emphasized: sig,
          dim: !sig,
          tooltip: [`Epoch ${e.epoch + 1} / epoch ${(v.reference_epoch ?? 0) + 1} · ${r.band}, ${r.wl.toFixed(3)} µm`, `ratio ${r.ratio.toFixed(3)} ± ${r.err.toFixed(3)} · z = ${r.z.toFixed(1)}`],
        });
        ys.push(r.ratio);
      }
    ys.sort((a, b) => a - b);
    const q = (t: number) => ys[Math.min(ys.length - 1, Math.max(0, Math.round(t * (ys.length - 1))))] ?? 1;
    let lo = Math.min(q(0.01), 0.9);
    let hi = Math.max(q(0.99), 1.1);
    const pad = (hi - lo) * 0.1;
    lo -= pad;
    hi += pad;
    return { points: pts, yDomain: [lo, hi] as [number, number] };
  }, [eps, nE, v.reference_epoch]);

  if (!v.available) return <p className="chart-empty static">{v.reason ?? "Not enough epochs for a comparison."}</p>;
  const ref = v.reference_epoch ?? 0;
  const legend: LegendItem[] = eps.map((e) => ({ label: `${e.epoch + 1}: ${formatDate(meta.epochs[e.epoch]?.mjd_mean ?? 0).slice(0, 7)}`, color: epochColor(e.epoch, nE) }));
  return (
    <div className="var-view">
      <ChartFrame
        subtitle={`Each epoch divided by epoch ${ref + 1} (${formatDate(meta.epochs[ref]?.mjd_mean ?? 0).slice(0, 7)}), per spectral bin. Bins with |z| ≥ 3 are drawn full strength.`}
        xDomain={X_DOMAIN}
        yDomain={yDomain}
        xLabel="Wavelength (µm)"
        yLabel="Flux ratio"
        xTicks={xTicks()}
        yFormat={(x) => x.toFixed(2)}
        points={points}
        legend={legend}
        height={240}
        note={v.systematic ? `Includes a ${(v.systematic * 100).toFixed(0)}% systematic error per bin.` : null}
        empty={points.length ? null : "No ratios to show"}
        background={(sc) => (
          <g>
            <SpectralBackground s={sc} meta={meta} />
            <line className="ref-line" x1={sc.left} x2={sc.right} y1={sc.y(1)} y2={sc.y(1)} />
          </g>
        )}
      />
      <table className="tbl compact">
        <thead>
          <tr>
            <th>Epoch</th>
            <th>Verdict</th>
            <th className="num">χ²ᵣ const.</th>
            <th>Significant in</th>
          </tr>
        </thead>
        <tbody>
          {eps.map((e) => (
            <tr key={e.epoch}>
              <td>
                <span className="swatch dot" style={{ color: epochColor(e.epoch, nE) }} /> {e.epoch + 1} · {formatDate(meta.epochs[e.epoch]?.mjd_mean ?? 0).slice(0, 7)}
              </td>
              <td>{e.variable ? <strong style={{ color: STATUS.warning }}>changed</strong> : <span className="muted">constant</span>}</td>
              <td className="num">{e.chi2_red_constant != null ? e.chi2_red_constant.toFixed(1) : "—"}</td>
              <td>{e.significant_bands?.length ? e.significant_bands.join(", ") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
