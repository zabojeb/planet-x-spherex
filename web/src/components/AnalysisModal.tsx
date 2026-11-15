/**
 * The analysis window: one source, found under the click, measured by SPHEREx and checked against catalogues.
 *
 * Opens over the sky as soon as Analyze is pressed. The stamp and the located centre are known at once (they
 * come from the images the browser already has); the steps of the server job (or of the browser fallback,
 * see pointAnalysis.ts) tick off while it runs. The result leads with what it is (class, type, known name),
 * then the SPHEREx-vs-catalogue checks, then tabs with the spectrum, variability, motion and counterparts.
 * Closing the window withdraws the job.
 */
import { AlertTriangle, ArrowUpRight, Check, CircleSlash, Loader2, RotateCcw, X } from "lucide-react";
import { Component, useEffect, useId, useMemo, useRef, useState, type ErrorInfo, type ReactNode } from "react";
import { compassPoint, formatDec, formatPm, formatRA } from "../astro";
import { CATALOG_STYLE, STATUS } from "../colors";
import type { Stamp } from "../locate";
import { FAMILY, catalogChecks, failed, goodBlackbody, headline, ok, retryCatalogs, runPointAnalysis, stepLabel, stepList, stepStates, saturated, summaryText, usableClass, type AnalysisPos, type AnalysisProgress, type Check as CheckRow } from "../pointAnalysis";
import type { CatalogMatch, Meta, Photometry, PointAnalysis } from "../types";
import { ClassView, SpectrumView, VariabilityView } from "./chart/SpectralPanel";
import { CopyButton, externalLinks } from "./PickCard";
import { Segmented } from "./ui/primitives";

export interface AnalysisSubject {
  key: string;
  label: string;
  /** What to analyse: the located centre of a pin, a catalogue source (with its motion) or the target. */
  pos: AnalysisPos;
  /** Where the user clicked, when the centre was found away from it. */
  click?: [number, number] | null;
  /** The source under the click, found in the browser's images. */
  located?: { found: true; snr: number; offsetArcsec: number; pa: string; how: "under" | "nearby" } | { found: false; snr: number | null } | null;
  /** Pixels around the position, for the picture at the top. */
  stamp?: { stamp: Stamp; rgb: boolean; centre: [number, number]; click?: [number, number] | null; pixscale: number } | null;
}

type Tab = "overview" | "spectrum" | "variability" | "motion" | "catalogs";

interface Props {
  subject: AnalysisSubject;
  meta: Meta;
  ylog: boolean;
  onYlog: (v: boolean) => void;
  onClose: () => void;
  /** Show this source's light curve and periodogram in the drawer (with the photometry measured here). */
  onOpenCurves?: (photometry: Photometry[], label: string) => void;
}

export function AnalysisModal(p: Props) {
  return (
    <WindowBoundary key={p.subject.key} onClose={p.onClose}>
      <AnalysisWindow {...p} />
    </WindowBoundary>
  );
}

class WindowBoundary extends Component<{ onClose: () => void; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("analysis window:", error, info.componentStack);
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="am-backdrop" onMouseDown={(e) => e.target === e.currentTarget && this.props.onClose()}>
        <section className="am glass am-broken" role="alertdialog" aria-modal="true" aria-label="Analysis window error">
          <p>This result could not be shown: <span className="err">{this.state.error.message}</span></p>
          <button className="btn" onClick={this.props.onClose}>Close</button>
        </section>
      </div>
    );
  }
}

function AnalysisWindow(p: Props) {
  const titleId = useId();
  const box = useRef<HTMLElement>(null);
  const [anyway, setAnyway] = useState(false);
  /** Times "Run again" was pressed: each measures afresh instead of taking the server's saved result. */
  const [rerun, setRerun] = useState(0);
  const skip = p.subject.located?.found === false && !anyway;
  const [st, setSt] = useState<{ key: string; progress: AnalysisProgress | null; res?: PointAnalysis; error?: string }>({ key: "", progress: null });
  const [tab, setTab] = useState<Tab>("overview");
  const runKey = `${p.subject.key}|${anyway}|${rerun}`;

  useEffect(() => {
    if (skip) return;
    const ctrl = new AbortController();
    setSt({ key: runKey, progress: null });
    setTab("overview");
    runPointAnalysis(p.meta, p.subject.pos, (progress) => !ctrl.signal.aborted && setSt((s) => (s.key === runKey ? { ...s, progress } : s)), ctrl.signal, rerun > 0)
      .then((res) => {
        if (ctrl.signal.aborted) return;
        setSt({ key: runKey, progress: null, res });
        // catalogues the server could not reach in time: ask again and update the checks when they answer
        retryCatalogs(res, p.meta, ctrl.signal)
          .then((more) => more && !ctrl.signal.aborted && setSt((s) => (s.key === runKey ? { ...s, res: more } : s)))
          .catch(() => {});
      })
      .catch((e) => !ctrl.signal.aborted && setSt({ key: runKey, progress: null, error: String(e?.message || e) }));
    return () => ctrl.abort(); // withdraws the job when the window closes or the subject changes
  }, [runKey, p.meta.id, skip]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keys belong to the window while it is open: Esc closes it, nothing reaches the sky's shortcuts.
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      e.stopPropagation();
      if (e.key === "Escape") {
        e.preventDefault();
        p.onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      prev?.focus?.();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const cur = st.key === runKey ? st : { progress: null, res: undefined, error: undefined };
  const r = cur.res;
  const h = useMemo(() => (r ? headline(r) : null), [r]);
  const checks = useMemo(() => (r ? catalogChecks(r) : null), [r]);
  const pos = r?.point ?? p.subject.pos;
  const loc = p.subject.located;
  const records = r && ok(r.photometry) ? r.photometry.records ?? [] : [];
  const nv = r && ok(r.verdict) ? r.verdict.novelty : null;
  const novelty = typeof nv === "number" ? nv : null;

  let kicker = "Source analysis";
  if (r?.mode === "client") kicker += " · quick";
  const title = skip ? "No source under the click" : r ? (h?.title ?? "Source") : cur.error ? "The analysis failed" : "Analysing…";

  return (
    <div className="am-backdrop" onMouseDown={(e) => e.target === e.currentTarget && p.onClose()}>
      <section ref={box} className="am glass" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header className="am-head">
          <div className="am-head-main">
            <div className="micro am-kicker">
              {kicker}
              <span className="am-subject mono">{p.subject.label}</span>
            </div>
            <h2 id={titleId} className="am-title">
              {title}
              {h?.type && <span className="am-type mono">{h.type}</span>}
            </h2>
            <div className="am-sub mono">
              <span>{formatRA(pos.ra)} {formatDec(pos.dec)}</span>
              <CopyButton text={`${pos.ra.toFixed(6)} ${pos.dec >= 0 ? "+" : ""}${pos.dec.toFixed(6)}`} label="coordinates" />
              {loc?.found && loc.offsetArcsec >= 0.5 && <span className="muted">centre {loc.offsetArcsec.toFixed(1)}″ {loc.pa} of the click</span>}
              {loc?.found && <span className="muted">peak S/N {loc.snr >= 100 ? loc.snr.toFixed(0) : loc.snr.toFixed(1)}</span>}
              {h?.knownAs && <span className="am-known">≈ {h.knownAs}</span>}
            </div>
            {(!!h?.tags.length || novelty != null) && (
              <div className="am-tags">
                {novelty != null && (
                  <span className={`badge novelty${novelty >= 0.4 ? " hot" : ""}`} title="How unusual the findings are, 0 (well known) to 1 (nothing like it in the catalogues)">
                    novelty<span className="num">{novelty.toFixed(2)}</span>
                  </span>
                )}
                {h?.tags.map((t) => <span key={t} className="badge">{t}</span>)}
              </div>
            )}
          </div>
          {checks && checks.checked > 0 && <Agreement agree={checks.agree} checked={checks.checked} />}
          {(r || cur.error) && (
            <button className="btn ghost icon am-rerun" onClick={() => setRerun((n) => n + 1)} aria-label="Run the analysis again" title="Run again: measure afresh instead of using the saved result">
              <RotateCcw size={15} />
            </button>
          )}
          <button className="btn ghost icon am-close" onClick={p.onClose} aria-label="Close analysis" title="Close (Esc)">
            <X size={16} />
          </button>
        </header>

        <div className="am-hero">
          {p.subject.stamp && <StampView {...p.subject.stamp} />}
          <div className="am-hero-text">
            {skip ? (
              <div className="am-empty">
                <p>
                  Nothing stands out within {Math.round(5 * (p.subject.stamp?.pixscale ?? 6.15))}″ of this point
                  {loc && !loc.found && loc.snr != null ? ` (S/N ${loc.snr.toFixed(1)} at the pin)` : ""}: it looks like empty sky in the image shown.
                  Click closer to a source, or measure this position anyway.
                </p>
                <button className="btn" onClick={() => setAnyway(true)}>Analyze this position anyway</button>
              </div>
            ) : cur.error ? (
              <div className="am-empty">
                <p className="err">{cur.error}</p>
                <button className="btn" onClick={() => setRerun((n) => n + 1)}><RotateCcw size={13} /> Try again</button>
              </div>
            ) : !r ? (
              <Steps progress={cur.progress} located={!!loc?.found} />
            ) : (
              <>
                <Verdict r={r} h={h!} located={loc?.found ? { offsetArcsec: loc.offsetArcsec, pa: loc.pa } : null} />
                <Incomplete r={r} onRerun={() => setRerun((n) => n + 1)} />
              </>
            )}
          </div>
        </div>

        {r && (
          <>
            <nav className="am-tabs">
              <Segmented<Tab>
                label="Analysis sections"
                size="sm"
                value={tab}
                onChange={setTab}
                options={[
                  { value: "overview", label: "Checks" },
                  { value: "spectrum", label: "Spectrum" },
                  { value: "variability", label: "Variability" },
                  { value: "motion", label: "Motion" },
                  { value: "catalogs", label: "Catalogues" },
                ]}
              />
              {tab === "spectrum" && (
                <Segmented<"linear" | "log">
                  label="Flux scale"
                  size="sm"
                  value={p.ylog ? "log" : "linear"}
                  onChange={(v) => p.onYlog(v === "log")}
                  options={[
                    { value: "linear", label: "Lin" },
                    { value: "log", label: "Log" },
                  ]}
                />
              )}
              {records.length > 0 && p.onOpenCurves && (
                <button className="btn ghost small-btn am-open" onClick={() => p.onOpenCurves!(records, p.subject.label)} title="Light curve, periodogram and data of this source in the drawer">
                  Open in drawer <ArrowUpRight size={12} />
                </button>
              )}
            </nav>
            <div className="am-body">
              {tab === "overview" && <Overview r={r} checks={checks!} />}
              {tab === "spectrum" && <SpectrumTab r={r} meta={p.meta} ylog={p.ylog} />}
              {tab === "variability" && <VariabilityTab r={r} meta={p.meta} />}
              {tab === "motion" && <MotionTab r={r} />}
              {tab === "catalogs" && <CatalogsTab r={r} fov={p.meta.grid.fov_arcmin} />}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ header pieces

function Agreement({ agree, checked }: { agree: number; checked: number }) {
  const f = agree / checked;
  const R = 15;
  const C = 2 * Math.PI * R;
  const color = f >= 0.8 ? STATUS.good : f >= 0.5 ? STATUS.warning : STATUS.critical;
  return (
    <div className="am-agree" title={`${agree} of ${checked} SPHEREx measurements agree with the catalogues`}>
      <svg width="38" height="38" viewBox="0 0 38 38" aria-hidden>
        <circle cx="19" cy="19" r={R} className="am-agree-track" />
        <circle cx="19" cy="19" r={R} fill="none" stroke={color} strokeWidth="3" strokeDasharray={`${C * f} ${C}`} transform="rotate(-90 19 19)" strokeLinecap="round" />
      </svg>
      <div>
        <div className="am-agree-num num">{agree}<span className="muted">/{checked}</span></div>
        <div className="micro">match catalogues</div>
      </div>
    </div>
  );
}

/** The pixels around the source: false colour (D5+D6, D3+D4, D1+D2) or S/N, with the centre and the click. */
function StampView({ stamp, rgb, centre, click, pixscale }: NonNullable<AnalysisSubject["stamp"]>) {
  const cv = useRef<HTMLCanvasElement>(null);
  const { size, x0, y0, channels } = stamp;
  useEffect(() => {
    const c = cv.current;
    if (!c) return;
    c.width = size;
    c.height = size;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const norm = channels.map((ch) => {
      const v = Array.from(ch).filter(Number.isFinite).sort((a, b) => a - b);
      const med = v.length ? v[v.length >> 1] : 0;
      const dev = v.map((x) => Math.abs(x - med)).sort((a, b) => a - b);
      const sig = dev.length ? 1.4826 * dev[dev.length >> 1] || 1 : 1;
      return Array.from(ch, (x) => (x - med) / sig);
    });
    // one stretch for all channels so colours stay honest; the peak near the centre sets the top
    const mid = (size - 1) / 2;
    let peak = 5;
    for (const ch of norm)
      for (let j = 0; j < size; j++)
        for (let i = 0; i < size; i++) if (Math.abs(i - mid) <= 3 && Math.abs(j - mid) <= 3 && ch[j * size + i] > peak) peak = ch[j * size + i];
    const beta = 8;
    const f = (v: number) => (Number.isFinite(v) ? Math.max(0, Math.min(1, Math.asinh((Math.max(v, -1) / peak) * beta) / Math.asinh(beta))) : 0);
    const img = ctx.createImageData(size, size);
    for (let j = 0; j < size; j++) {
      const row = size - 1 - j; // north up
      for (let i = 0; i < size; i++) {
        const k = (row * size + i) * 4;
        const vals = rgb ? norm.map((ch) => f(ch[j * size + i])) : [f(norm[0][j * size + i])];
        const [rr, gg, bb] = rgb ? vals : [vals[0], vals[0], vals[0]];
        const nan = !Number.isFinite(channels[0][j * size + i]);
        img.data[k] = nan ? 22 : rr * 255;
        img.data[k + 1] = nan ? 18 : gg * 255;
        img.data[k + 2] = nan ? 14 : bb * 255;
        img.data[k + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }, [stamp, rgb]); // eslint-disable-line react-hooks/exhaustive-deps

  const sx = (x: number) => x - x0 + 0.5;
  const sy = (y: number) => size - 1 - (y - y0) + 0.5;
  const [cx, cy] = [sx(centre[0]), sy(centre[1])];
  const bar = 30 / pixscale;
  return (
    <figure className="am-stamp">
      <div className="am-stamp-img">
        <canvas ref={cv} aria-label="Image around the source" />
        <svg viewBox={`0 0 ${size} ${size}`} preserveAspectRatio="none" aria-hidden>
          {click && (
            <g className="am-click" transform={`translate(${sx(click[0])} ${sy(click[1])})`}>
              <line x1={-0.6} y1={-0.6} x2={0.6} y2={0.6} />
              <line x1={-0.6} y1={0.6} x2={0.6} y2={-0.6} />
            </g>
          )}
          {click && <line className="am-click-link" x1={sx(click[0])} y1={sy(click[1])} x2={cx} y2={cy} />}
          <g className="am-reticle" transform={`translate(${cx} ${cy})`}>
            <circle r={2.2} />
            <line x1={-4.2} x2={-2.8} y1={0} y2={0} />
            <line x1={2.8} x2={4.2} y1={0} y2={0} />
            <line y1={-4.2} y2={-2.8} x1={0} x2={0} />
            <line y1={2.8} y2={4.2} x1={0} x2={0} />
          </g>
          <g className="am-scale">
            <line x1={1} x2={1 + bar} y1={size - 1.2} y2={size - 1.2} />
          </g>
          <g className="am-compass" transform={`translate(${size - 2} ${size - 5})`}>
            <line x1={0} y1={0} x2={0} y2={-2.6} />
            <line x1={0} y1={0} x2={-2.6} y2={0} />
          </g>
        </svg>
        <span className="am-stamp-label mono am-n" style={{ right: `${(1.6 / size) * 100}%`, bottom: `${((5 + 2.8) / size) * 100}%` }}>N</span>
        <span className="am-stamp-label mono am-e" style={{ right: `${((2 + 3.3) / size) * 100}%`, bottom: `${((5 - 0.4) / size) * 100}%` }}>E</span>
        <span className="am-stamp-label mono am-bar">30″</span>
      </div>
      <figcaption className="mono">{rgb ? "false colour · 3.8–5 / 1.6–3.8 / 0.75–1.6 µm" : "S/N, all detectors"}</figcaption>
    </figure>
  );
}

function Steps({ progress, located }: { progress: AnalysisProgress | null; located: boolean }) {
  // until the first answer, assume the server job (the browser fallback reports its own plan when it starts)
  const plan = progress?.plan ?? "server";
  const list = stepList(plan);
  const states = stepStates(progress);
  const busy = progress?.stage === "busy" ? `Many users right now · retrying in ${progress.retryIn ?? 1}s` : progress?.ahead ? `Waiting for a free worker · ${progress.ahead} ahead` : null;
  const elapsed = progress?.elapsed ?? 0;
  const rows = [...(located ? [{ key: "locate", label: "Find the source and its centre", state: "done" }] : []), ...list.map((s, i) => ({ ...s, state: states[i] }))];
  return (
    <div className="am-steps" role="status" aria-live="polite">
      <ol>
        {rows.map((s) => (
          <li key={s.key} className={s.state}>
            <span className="am-step-mark">
              {s.state === "done" ? <Check size={12} /> : s.state === "error" ? <AlertTriangle size={11} /> : s.state === "now" ? <Loader2 size={12} className="spin" /> : null}
            </span>
            <span>
              {s.label}
              {s.state === "error" && <span className="muted"> · failed or unavailable</span>}
            </span>
          </li>
        ))}
      </ol>
      {busy && <p className="muted small">{busy}</p>}
      {!busy && elapsed >= 5 && (
        <p className="muted small num">
          {Math.round(elapsed)} s{plan === "server" ? " · whatever has not finished after 60 s is reported as such" : ""}
        </p>
      )}
    </div>
  );
}

/** Services by host, for "could not reach …". */
const SERVICE: Record<string, string> = {
  "irsa.ipac.caltech.edu": "IRSA (SPHEREx frames, 2MASS, CatWISE)",
  "gea.esac.esa.int": "the ESA Gaia archive",
  "ssd-api.jpl.nasa.gov": "JPL small-body search",
  "ssd.jpl.nasa.gov": "JPL Horizons",
  "simbad.cds.unistra.fr": "SIMBAD (CDS)",
  "simbad.harvard.edu": "SIMBAD (Harvard mirror)",
};

/** What this result is missing and why (steps cut off at the deadline, services down, failed measurements),
 *  with a way to measure again. Nothing when the run was complete. */
function Incomplete({ r, onRerun }: { r: PointAnalysis; onRerun: () => void }) {
  const late = r.timings?.timed_out ?? [];
  const down = Object.entries(r.timings?.services_unavailable ?? {});
  const broken = (["photometry", "identification"] as const).filter((k) => failed(r[k]) && !late.includes(k));
  if (r.mode === "client" || (!late.length && !down.length && !broken.length)) return null;
  const list = (keys: string[]) => keys.map(stepLabel).join(", ").toLowerCase();
  return (
    <div className="am-incomplete" role="note">
      <AlertTriangle size={14} />
      <div>
        <p>
          {late.length > 0 && <>Not finished within 60 s: {list(late)}. </>}
          {broken.length > 0 && <>Failed: {list(broken)}. </>}
          {down.length > 0 && (
            <>
              Could not reach {down.map(([host]) => SERVICE[host] ?? host).join("; ")}.
            </>
          )}
        </p>
        <p className="muted small">The server keeps what it fetched, so running again usually finishes what was missing.</p>
      </div>
      <button className="btn small-btn" onClick={onRerun}>
        <RotateCcw size={12} /> Run again
      </button>
    </div>
  );
}

function Verdict({ r, h, located }: { r: PointAnalysis; h: ReturnType<typeof headline>; located: { offsetArcsec: number; pa: string } | null }) {
  const v = ok(r.verdict) ? r.verdict : null;
  const cls = usableClass(r);
  const mo = ok(r.motion) ? r.motion : null;
  const best = cls?.templates?.best;
  const synth = cls?.synthetic_photometry ?? {};
  const color = (a: string, b: string) => (synth[a]?.mag != null && synth[b]?.mag != null ? synth[a].mag! - synth[b].mag! : null);
  const jk = color("J", "Ks");
  const w12 = color("W1", "W2");
  const facts: [string, ReactNode, string?][] = [];
  if (best) facts.push(["Spectral type", <>{best.sptype}{h.confidence != null && <span className="muted"> · {Math.round(h.confidence * 100)}%</span>}</>, `Best of ${cls?.templates?.ranked.length ?? 0} templates (Pickles, SpeX), χ²ᵣ ${best.chi2_red.toFixed(1)}`]);
  const bb = goodBlackbody(cls);
  if (bb) facts.push(["T (blackbody)", `${Math.round(bb.teff_k / 10) * 10} K`, `Blackbody fit over 0.75–5 µm, χ²/dof ${bb.chi2_red.toFixed(1)}`]);
  if (synth.Ks?.mag != null) facts.push(["Ks", synth.Ks.mag.toFixed(2), "Synthetic 2MASS Ks from the spectrum"]);
  if (jk != null) facts.push(["J − Ks", jk.toFixed(2)]);
  if (w12 != null) facts.push(["W1 − W2", w12.toFixed(2), "Synthetic WISE colour (W2 extrapolated past 5 µm)"]);
  if (mo) facts.push(["Motion", (mo.significance ?? 0) >= 3 ? `${formatPm(mo.pmra, mo.pmdec)} ${compassPoint((Math.atan2(mo.pmra, mo.pmdec) * 180) / Math.PI + 360)}` : "none measurable", mo.significance != null ? `${mo.significance.toFixed(1)}σ from ${mo.n_epochs ?? "several"} epochs` : undefined]);
  if (cls?.ir_index) facts.push(["IR index α", cls.ir_index.alpha.toFixed(2), cls.ir_index.lada_class]);
  const red = ok(r.red_excess) ? r.red_excess : null;
  if (red?.ratio_long_short != null) facts.push(["3.8–5 / <2.4 µm", red.ratio_long_short.toFixed(2), "Mean flux density at 3.8–5 µm over that below 2.4 µm; above 0.8 is very red (cold brown dwarfs)"]);
  const n = ok(r.photometry) ? r.photometry.records?.length ?? r.photometry.n ?? 0 : 0;
  if (n) facts.push(["Measurements", String(n), "Exposures measured at this position"]);

  return (
    <div className="am-verdict">
      <p className="am-summary">{v?.summary ?? summaryText(r, h, located)}</p>
      {facts.length > 0 && (
        <dl className="am-facts">
          {facts.map(([k, val, hint]) => (
            <div key={k} title={hint}>
              <dt>{k}</dt>
              <dd className="num">{val}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ tabs

function Mark({ agree }: { agree: boolean | null }) {
  if (agree === true) return <span className="am-mark good"><Check size={12} /> agrees</span>;
  if (agree === false) return <span className="am-mark bad"><AlertTriangle size={12} /> differs</span>;
  return <span className="am-mark none"><CircleSlash size={12} /> can’t tell</span>;
}

function Overview({ r, checks }: { r: PointAnalysis; checks: ReturnType<typeof catalogChecks> }) {
  const v = ok(r.verdict) ? r.verdict : null;
  const notes = [...(ok(r.classification) ? r.classification.summary?.notes ?? [] : []), ...(r.notes ?? [])];
  // cut off at the deadline: said once, in the banner above the tabs
  const errs = (["photometry", "classification", "motion", "identification", "periodogram"] as const)
    .map((k) => [k, r[k]] as const)
    .filter(([, s]) => failed(s) && !s.error.startsWith("TimeoutError: not finished within")) as [string, { error: string }][];
  const id = ok(r.identification) ? r.identification : null;
  const counterpart = !!(id?.matches?.length || id?.simbad?.length);
  const unasked = Object.keys(id?.errors ?? {});
  return (
    <div className="am-overview">
      <section>
        <h3 className="micro">SPHEREx against the catalogues</h3>
        {checks.rows.length ? (
          <table className="tbl compact am-checks">
            <thead>
              <tr>
                <th>Quantity</th>
                <th className="num">SPHEREx</th>
                <th className="num">Catalogue</th>
                <th className="num">Δ</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {checks.rows.map((c: CheckRow) => (
                <tr key={c.key} title={c.note ?? undefined} className={c.agree === false ? "bad" : undefined}>
                  <td>{c.label}</td>
                  <td className="num">{c.ours}</td>
                  <td className="num">
                    {c.theirs}
                    <span className="am-cat">{c.catalog}</span>
                  </td>
                  <td className="num">{c.delta ?? "—"}</td>
                  <td><Mark agree={c.agree} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted small">
            {saturated(r)
              ? "Saturated: no calibrated measurement to compare with the catalogues."
              : counterpart
              ? "Nothing measured here yet to compare with the counterpart."
              : unasked.length
                ? "No counterpart found, but not every catalogue could be asked, so that means little here."
                : "No catalogue counterpart close enough to compare with. A source seen by SPHEREx but missing from Gaia, 2MASS, CatWISE and SIMBAD is worth a closer look."}
            {unasked.length > 0 && ` Not asked: ${unasked.join(", ")}.`}
          </p>
        )}
      </section>
      {v?.findings?.length ? (
        <section>
          <h3 className="micro">Findings</h3>
          <ul className="pa-findings">
            {v.findings.map((f, i) => {
              const pct = typeof f.confidence === "number" ? Math.round(f.confidence * 100) : f.confidence === "high" ? 90 : f.confidence === "medium" ? 60 : 30;
              return (
                <li key={i}>
                  <span className={`conf ${pct >= 80 ? "high" : pct >= 50 ? "medium" : "low"}`} title={`Confidence ${pct}%`}>
                    <span style={{ width: `${pct}%` }} />
                  </span>
                  <span>
                    {f.text}
                    {f.evidence && <span className="pa-evidence">{Array.isArray(f.evidence) ? f.evidence.join(" · ") : f.evidence}</span>}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      {notes.length > 0 && (
        <section>
          <h3 className="micro">Notes</h3>
          <ul className="am-notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </section>
      )}
      {errs.length > 0 && <p className="small err">{errs.map(([k, s]) => `${k}: ${s.error}`).join(" · ")}</p>}
      {r.mode === "client" && <p className="fine">Quick analysis: this server has no point-analysis job yet, so the window combined its photometry, spectrum, motion and catalogue endpoints.</p>}
    </div>
  );
}

function SpectrumTab({ r, meta, ylog }: { r: PointAnalysis; meta: Meta; ylog: boolean }) {
  const s = ok(r.spectrum) ? r.spectrum : null;
  const c = ok(r.classification) ? r.classification : null;
  if (failed(r.classification)) return <p className="chart-empty static">{r.classification.error}</p>;
  if (!c && !s) return <p className="chart-empty static">No spectrum for this position.</p>;
  return (
    <div className="am-spectrum">
      {c ? <ClassView c={c} s={s} meta={meta} ylog={ylog} /> : s && <SpectrumView s={s} meta={meta} ylog={ylog} />}
      {c && s && (
        <details className="am-more">
          <summary>Spectral features and indices</summary>
          <SpectrumView s={s} meta={meta} ylog={ylog} />
        </details>
      )}
    </div>
  );
}

function VariabilityTab({ r, meta }: { r: PointAnalysis; meta: Meta }) {
  const v = ok(r.variability) ? r.variability : null;
  const pg = ok(r.periodogram) ? r.periodogram : null;
  return (
    <div className="am-var">
      {v?.available && v.epochs?.length ? (
        <VariabilityView v={v} meta={meta} />
      ) : (
        <p className="chart-empty static">{failed(r.variability) ? r.variability.error : v?.reason ?? "No epoch comparison."}</p>
      )}
      {pg?.best && (
        <p className="small">
          Lomb–Scargle best period <span className="num">{pg.best.period_days.toFixed(pg.best.period_days < 10 ? 3 : 1)} d</span>, power {pg.best.power.toFixed(2)}
          {pg.best.false_alarm_probability != null && <> · false-alarm probability {pg.best.false_alarm_probability.toExponential(0)}</>}
          {(pg.best.false_alarm_probability ?? 1) > 0.01 && <span className="muted"> · not significant</span>}
          <span className="muted"> · {pg.n_points} points over {pg.baseline_days.toFixed(0)} d</span>
        </p>
      )}
      {failed(r.periodogram) && <p className="muted small">Periodogram: {r.periodogram.error}</p>}
    </div>
  );
}

function MotionTab({ r }: { r: PointAnalysis }) {
  if (failed(r.motion)) return <p className="chart-empty static">Motion: {r.motion.error}</p>;
  const mo = ok(r.motion) ? r.motion : null;
  if (!mo) return <p className="chart-empty static">No motion measurement.</p>;
  const cat = mo.catalog;
  const lim = Math.max(50, Math.abs(mo.pmra) + (mo.pmra_err ?? 0), Math.abs(mo.pmdec) + (mo.pmdec_err ?? 0), cat ? Math.max(Math.abs(cat.pmra), Math.abs(cat.pmdec)) : 0) * 1.25;
  const S = 150;
  const k = S / 2 / lim;
  // east (positive μα*) is to the left, as on the sky
  const X = (pmra: number) => S / 2 - pmra * k;
  const Y = (pmdec: number) => S / 2 - pmdec * k;
  return (
    <div className="am-motion">
      <svg className="am-pm" width={S} height={S} viewBox={`0 0 ${S} ${S}`} role="img" aria-label="Proper-motion vectors">
        <line x1={0} x2={S} y1={S / 2} y2={S / 2} className="axis" />
        <line y1={0} y2={S} x1={S / 2} x2={S / 2} className="axis" />
        <text x={S / 2 + 3} y={10} className="lbl">N</text>
        <text x={4} y={S / 2 - 4} className="lbl">E</text>
        <ellipse cx={X(mo.pmra)} cy={Y(mo.pmdec)} rx={Math.max(1.5, (mo.pmra_err ?? 0) * k)} ry={Math.max(1.5, (mo.pmdec_err ?? 0) * k)} className="err-ell" />
        <line x1={S / 2} y1={S / 2} x2={X(mo.pmra)} y2={Y(mo.pmdec)} className="ours" />
        <circle cx={X(mo.pmra)} cy={Y(mo.pmdec)} r={2.5} className="ours-dot" />
        {cat && <circle cx={X(cat.pmra)} cy={Y(cat.pmdec)} r={4} className="cat-dot" />}
      </svg>
      <div className="am-motion-text">
        <table className="tbl compact">
          <thead>
            <tr>
              <th />
              <th className="num">μα* mas/yr</th>
              <th className="num">μδ mas/yr</th>
              <th className="num">total</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span className="am-key ours" /> SPHEREx</td>
              <td className="num">{mo.pmra.toFixed(0)}{mo.pmra_err != null && <span className="muted"> ± {mo.pmra_err.toFixed(0)}</span>}</td>
              <td className="num">{mo.pmdec.toFixed(0)}{mo.pmdec_err != null && <span className="muted"> ± {mo.pmdec_err.toFixed(0)}</span>}</td>
              <td className="num">{formatPm(mo.pmra, mo.pmdec)}</td>
            </tr>
            {cat && (
              <tr>
                <td><span className="am-key cat" /> {cat.catalog}</td>
                <td className="num">{cat.pmra.toFixed(0)}</td>
                <td className="num">{cat.pmdec.toFixed(0)}</td>
                <td className="num">{formatPm(cat.pmra, cat.pmdec)}</td>
              </tr>
            )}
          </tbody>
        </table>
        <p className="small muted">
          From the centre of the source in each epoch&apos;s co-add{mo.n_epochs ? ` (${mo.n_epochs} epochs)` : ""}
          {mo.significance != null && <>, {mo.significance.toFixed(1)}σ</>}
          {mo.discrepancy_sigma != null && <>; {mo.discrepancy_sigma.toFixed(1)}σ from {cat?.catalog ?? "the catalogue"}</>}. SPHEREx pixels are 6.15″, so only fast movers (≳ 0.3″/yr over a year) stand out.
        </p>
      </div>
    </div>
  );
}

const CAT_KEY: Record<string, string> = { "gaia dr3": "gaia", catwise2020: "catwise", "2mass": "2mass", simbad: "simbad" };

function CatalogsTab({ r, fov }: { r: PointAnalysis; fov: number }) {
  if (failed(r.identification)) return <p className="chart-empty static">Catalogues: {r.identification.error}</p>;
  const id = ok(r.identification) ? r.identification : null;
  const rows = [...(id?.matches ?? []), ...(id?.simbad ?? []).map((m) => ({ ...m, catalog: m.catalog ?? "SIMBAD" }))];
  const nearby = id?.nearby ?? [];
  const solar = id?.solar_system ?? [];
  const pos = r.point;
  const simbadName = id?.simbad?.[0] ? String(id.simbad[0].name ?? id.simbad[0].id) : null;
  const row = (m: CatalogMatch, i: number, far: boolean) => {
    const st = CATALOG_STYLE[CAT_KEY[(m.catalog ?? "").toLowerCase()] ?? ""];
    return (
      <tr key={`${far ? "n" : "c"}${m.catalog}${m.id}${i}`} className={far ? "am-far" : undefined} title={far ? "Close by, but not the counterpart" : undefined}>
        <td className="nowrap">
          {st && <span className={`swatch ${st.shape}`} style={{ color: st.color }} />} {m.catalog}
        </td>
        <td>
          <span className="mono">{m.name ?? String(m.id)}</span>
          {(m.type || m.otype || m.sp_type) && <span className="muted"> · {[m.type ?? m.otype, m.sp_type].filter(Boolean).join(" · ")}</span>}
        </td>
        <td className="num">{m.mag != null ? `${m.mag_band ?? ""} ${m.mag.toFixed(2)}` : ""}</td>
        <td className="num">{m.pmra != null && m.pmdec != null ? formatPm(m.pmra, m.pmdec) : ""}</td>
        <td className="num">{m.sep_arcsec != null ? `${m.sep_arcsec.toFixed(1)}″` : ""}</td>
      </tr>
    );
  };
  return (
    <div className="am-cats">
      {rows.length || nearby.length || solar.length ? (
        <table className="tbl compact">
          <thead>
            <tr>
              <th>Catalogue</th>
              <th>Source</th>
              <th className="num">Mag</th>
              <th className="num">μ</th>
              <th className="num">Sep</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m, i) => row(m, i, false))}
            {solar.map((o) => (
              <tr key={o.name}>
                <td>{o.kind ?? "Solar system"}</td>
                <td>{o.name}</td>
                <td className="num">{o.vmag != null ? `V ${o.vmag.toFixed(1)}` : ""}</td>
                <td />
                <td className="num">{o.sep_arcsec != null ? `${o.sep_arcsec.toFixed(0)}″` : ""}</td>
              </tr>
            ))}
            {nearby.length > 0 && (
              <tr className="am-sep">
                <td colSpan={5} className="micro">Also nearby, not the counterpart</td>
              </tr>
            )}
            {nearby.map((m, i) => row(m, i, true))}
          </tbody>
        </table>
      ) : null}
      {!rows.length && <p className="muted small">No counterpart in Gaia DR3, CatWISE2020, 2MASS or SIMBAD close enough to be this source.</p>}
      {id?.errors && <p className="small err">{Object.entries(id.errors).map(([k, e]) => `${k}: ${e}`).join(" · ")}</p>}
      {pos && (
        <div className="pick-links am-links">
          {externalLinks(pos.ra, pos.dec, fov, simbadName).map((l) => (
            <a key={l.label} href={l.href} target="_blank" rel="noreferrer" title={l.hint}>
              {l.label} <ArrowUpRight size={11} />
            </a>
          ))}
        </div>
      )}
      <p className="fine">
        {r.mode === "client"
          ? "Counterparts are moved with their proper motion to the SPHEREx epoch (2MASS and SIMBAD with Gaia's), within 6″; when several fall in one pixel, the brighter one is taken."
          : "Catalogue sources are moved with their proper motion to the SPHEREx epochs; the nearest within 4″ is the counterpart. Asteroids and comets are those within 30″ at an epoch."}
      </p>
    </div>
  );
}

export { FAMILY };
