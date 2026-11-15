/** The card next to a selected object, the target or a dropped pin: where it is in every frame we use, how
 *  far from the target, what catalogues say about it, and what to do next (measure, centre, follow, look up). */
import { ArrowUpRight, Check, Copy, Footprints, Loader2, LocateFixed, ScanEye, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { compassPoint, formatAngle, formatDec, formatDeg, formatDistance, formatPm, formatRA, positionAngle, separation, toEcliptic, toGalactic, totalPm } from "../astro";
import { CATALOG_STYLE } from "../colors";
import { abMag, flagNames, PROBE_R, type Hint, type ProbeResult } from "../probe";
import type { CatalogSource, FlagBit, Target } from "../types";
import { ProbeCharts } from "./chart/ProbeCharts";
import type { Selection } from "./SkyView";

/** Other tools at the same position. SIMBAD links use the official CfA mirror (the CDS site was down on
 *  2026-11-14; both serve the same database). WiseView answers only over plain HTTP; its hash keys are the
 *  ones its own buildUrl writes (size in arcsec, band 3 = W1+W2). */
export function externalLinks(ra: number, dec: number, fovArcmin: number, simbadId?: string | null) {
  const c = `${ra.toFixed(6)} ${dec >= 0 ? "+" : ""}${dec.toFixed(6)}`;
  const fovDeg = Math.min(Math.max(fovArcmin / 60, 0.02), 2);
  const simbad = simbadId
    ? `https://simbad.harvard.edu/simbad/sim-id?Ident=${encodeURIComponent(simbadId)}`
    : `https://simbad.harvard.edu/simbad/sim-coo?Coord=${encodeURIComponent(c)}&CooFrame=ICRS&Radius=10&Radius.unit=arcsec`;
  const size = Math.round(Math.min(Math.max(fovArcmin * 60, 60), 600));
  return [
    { label: "SIMBAD", href: simbad, hint: simbadId ? "this object in SIMBAD" : "SIMBAD objects within 10″" },
    { label: "Aladin", href: `https://aladin.cds.unistra.fr/AladinLite/?target=${encodeURIComponent(c)}&fov=${fovDeg.toFixed(3)}&survey=${encodeURIComponent("CDS/P/2MASS/color")}`, hint: "2MASS colour and other surveys in Aladin Lite" },
    { label: "WiseView", href: `http://byw.tools/wiseview#ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&size=${size}&band=3&speed=164.5&minbright=-50.0000&maxbright=500.0000&window=0.5&diff_window=1&linear=1&color=gray&zoom=9&border=1&gaia=1&invert=1`, hint: "the same field blinked in WISE (2010–2024)" },
  ];
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState<"ok" | "blocked" | null>(null);
  useEffect(() => {
    if (!done) return;
    const t = window.setTimeout(() => setDone(null), 1400);
    return () => window.clearTimeout(t);
  }, [done]);
  return (
    <button
      className="btn ghost icon xs copy-btn"
      title={done === "blocked" ? "Clipboard blocked: select the text" : `Copy ${label}`}
      aria-label={`Copy ${label}`}
      onClick={() => navigator.clipboard.writeText(text).then(() => setDone("ok"), () => setDone("blocked"))}
    >
      {done === "ok" ? <Check size={12} /> : <Copy size={12} />}
    </button>
  );
}

/** α δ in sexagesimal and degrees, plus Galactic and ecliptic, each line copyable. */
export function CoordBlock({ ra, dec }: { ra: number; dec: number }) {
  const sexa = `${formatRA(ra)} ${formatDec(dec)}`;
  const degs = `${ra.toFixed(6)} ${dec >= 0 ? "+" : ""}${dec.toFixed(6)}`;
  const [l, b] = toGalactic(ra, dec);
  const [lam, bet] = toEcliptic(ra, dec);
  return (
    <div className="coords">
      <div className="coords-row">
        <span className="coords-k">ICRS</span>
        <span className="coords-v">{sexa}</span>
        <CopyButton text={sexa} label="sexagesimal coordinates" />
      </div>
      <div className="coords-row">
        <span className="coords-k">deg</span>
        <span className="coords-v">{degs}</span>
        <CopyButton text={degs} label="decimal coordinates" />
      </div>
      <div className="coords-row">
        <span className="coords-k">gal</span>
        <span className="coords-v">l {formatDeg(l, 4)} b {formatDeg(b, 4, true)}</span>
      </div>
      <div className="coords-row">
        <span className="coords-k">ecl</span>
        <span className="coords-v">λ {formatDeg(lam, 3)} β {formatDeg(bet, 3, true)}</span>
      </div>
    </div>
  );
}

interface Props {
  sel: Selection;
  world: [number, number] | null;
  year: number;
  targetName: string;
  target: Target | null;
  targetWorld: [number, number] | null;
  value: string | null;
  counterparts: { cat: string; src: CatalogSource; sep: number }[];
  fovArcmin: number;
  onClose: () => void;
  onSelect: (s: Selection) => void;
  onDetails: () => void;
  /** Forced photometry here; resolves when its curves are in the drawer. */
  onMeasure?: () => Promise<void>;
  /** Full automatic analysis of this position (opens the Analysis tab). */
  onAnalyze?: () => void;
  /** Per-exposure PSF fits and cutouts here (opens the verification strip). */
  onVerify?: () => void;
  /** A new field centred on this position. */
  onCentre?: () => void;
  /** A new field that follows this source's proper motion. */
  onFollow?: () => void;
  /** Instant quick-look photometry from the planes already loaded (null when outside the field). */
  probe?: ProbeResult | null;
  /** Some planes the probe wants are still downloading. */
  probeLoading?: boolean;
  hints?: Hint[];
  flagDefs?: FlagBit[];
  audience?: "explore" | "research";
}

const f2 = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2));

/** The instant probe: a quick estimate here, as plain language (Explore) or numbers with errors (Research). */
function ProbeBlock({ r, loading, hints, flagDefs, research }: { r: ProbeResult; loading: boolean; hints: Hint[]; flagDefs: FlagBit[]; research: boolean }) {
  const c = r.current;
  const flags = flagNames(c?.flags ?? null, flagDefs).filter((f) => f !== "SOURCE");
  const mag = c ? abMag(c.flux) : null;
  return (
    <div className="probe">
      <div className="micro probe-head">
        <span>{research ? `Quick estimate · ${PROBE_R} px aperture` : "Brightness here"}</span>
        {loading && <Loader2 size={11} className="spin" aria-label="Loading more epochs and detectors" />}
      </div>
      {c ? (
        research ? (
          <>
            <div className="probe-main mono">
              {f2(c.flux)} ± {Number.isFinite(c.err) ? f2(c.err) : "?"} mJy <span className="muted">· S/N {Number.isFinite(c.snr) ? c.snr.toFixed(1) : "?"}{mag != null ? ` · AB ${mag.toFixed(2)}` : ""}</span>
            </div>
            <div className="probe-sub mono small muted">
              {c.band} {c.wlLo.toFixed(2)}–{c.wlHi.toFixed(2)} µm · px {r.x.toFixed(1)}, {r.y.toFixed(1)}
              {c.coverage != null ? ` · ${c.coverage} exp` : ""}
              {flags.length ? ` · flagged in some exposure: ${flags.join(", ")}` : c.flags != null ? " · no flags" : ""}
            </div>
          </>
        ) : (
          <div className="probe-main">
            {mag != null && c.snr >= 3 ? (
              <>≈ magnitude {mag.toFixed(1)} <span className="muted small">at {c.wlLo.toFixed(1)}–{c.wlHi.toFixed(1)} µm</span></>
            ) : (
              <span className="muted">Too faint to measure at {c.wlLo.toFixed(1)}–{c.wlHi.toFixed(1)} µm</span>
            )}
          </div>
        )
      ) : (
        <div className="small muted">{loading ? "Measuring…" : "No data at this position in the shown detector."}</div>
      )}
      {hints.length > 0 && (
        <ul className="probe-hints">
          {hints.map((h) => (
            <li key={h.kind} className={`hint-${h.kind}`}>{h.text}</li>
          ))}
        </ul>
      )}
      <ProbeCharts spectrum={r.spectrum} lightCurve={r.lightCurve} epoch={c?.epoch ?? -1} band={r.lcBand} />
      {research && r.lightCurve.length > 1 && (
        <details className="probe-table">
          <summary className="micro">Per visit · {r.lcBand}</summary>
          <table className="mono small">
            <tbody>
              {r.lightCurve.map((pt) => (
                <tr key={pt.epoch} className={pt.epoch === c?.epoch ? "on" : undefined}>
                  <td>{pt.date}</td>
                  <td>{f2(pt.flux)} ± {Number.isFinite(pt.err) ? f2(pt.err) : "?"}</td>
                  <td className="muted">S/N {Number.isFinite(pt.snr) ? pt.snr.toFixed(0) : "?"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

export function PickCard(p: Props) {
  const [measure, setMeasure] = useState<{ state: "idle" | "busy" } | { state: "error"; message: string }>({ state: "idle" });
  const selKey = p.sel.kind === "source" ? `${p.sel.cat}:${p.sel.src.id}` : p.sel.kind === "point" ? `${p.sel.ra},${p.sel.dec}` : "target";
  useEffect(() => setMeasure({ state: "idle" }), [selKey]);
  const w = p.world;
  const sep = w && p.targetWorld && p.sel.kind !== "target" ? separation(...p.targetWorld, ...w) : null;
  const src = p.sel.kind === "source" ? p.sel.src : null;
  const simbad = p.sel.kind === "source" && p.sel.cat === "simbad";
  const pm = src ? totalPm(src) : p.sel.kind === "target" && p.target ? Math.hypot(p.target.pmra, p.target.pmdec) : 0;
  const pmVec = src ? [src.pmra ?? 0, src.pmdec ?? 0] : p.target ? [p.target.pmra, p.target.pmdec] : [0, 0];
  const plx = src ? (src.parallax as number | null | undefined) : p.sel.kind === "target" ? p.target?.parallax : null;
  const dist = formatDistance(plx);

  const runMeasure = () => {
    if (!p.onMeasure) return;
    setMeasure({ state: "busy" });
    p.onMeasure().then(
      () => setMeasure({ state: "idle" }),
      (e) => setMeasure({ state: "error", message: String(e?.message || e) }),
    );
  };

  let kicker: string;
  let title: string;
  let sub: string | null = null;
  if (p.sel.kind === "target") {
    kicker = "Target";
    title = p.targetName;
    sub = p.target?.description ?? null;
  } else if (p.sel.kind === "point") {
    kicker = "Pinned position";
    title = w ? `${formatRA(w[0]).slice(0, 8)} ${formatDec(w[1]).slice(0, 9)}` : "Position";
    sub = p.value ? `Pixel value ${p.value}` : null;
  } else {
    const s = p.sel.src;
    kicker = simbad ? `SIMBAD${s.type ? ` · ${s.type}` : ""}` : CATALOG_STYLE[p.sel.cat]?.label ?? p.sel.cat;
    title = String(s.id);
    const bits = [s.sp_type ? `Sp ${s.sp_type}` : null, s.mag != null ? `${s.mag_band} ${s.mag.toFixed(2)}` : null, s.color != null ? `BP−RP ${(s.color as number).toFixed(2)}` : null, s.w1 != null && s.mag_band === "W2" ? `W1−W2 ${((s.w1 as number) - (s.mag ?? 0)).toFixed(2)}` : null];
    sub = bits.filter(Boolean).join(" · ") || null;
  }

  return (
    <div className="pick-card">
      <button className="pick-close" onClick={p.onClose} aria-label="Close">
        <X size={14} />
      </button>
      <div className="micro pick-kicker">
        {p.sel.kind === "source" && <span className={`swatch ${CATALOG_STYLE[p.sel.cat]?.shape}`} style={{ color: CATALOG_STYLE[p.sel.cat]?.color }} />}
        {kicker}
      </div>
      <div className={`pick-title${p.sel.kind === "source" && !simbad ? " mono" : ""}`}>{title}</div>
      {sub && <div className="small muted pick-sub">{sub}</div>}

      {(pm >= 1 || dist) && (
        <div className="pick-facts mono small">
          {pm >= 1 && (
            <span title="Total proper motion and its direction on the sky">
              μ {formatPm(pmVec[0], pmVec[1])} → {compassPoint((Math.atan2(pmVec[0], pmVec[1]) * 180) / Math.PI + 360)}
            </span>
          )}
          {dist && <span title={`Parallax ${(plx as number).toFixed(2)} mas`}>ϖ {(plx as number).toFixed(1)} mas · {dist}</span>}
        </div>
      )}

      {p.probe && p.probe.inside && <ProbeBlock r={p.probe} loading={!!p.probeLoading} hints={p.hints ?? []} flagDefs={p.flagDefs ?? []} research={p.audience === "research"} />}

      {w && (p.audience === "research" ? <CoordBlock ra={w[0]} dec={w[1]} /> : (
        <div className="coords-row coords-compact">
          <span className="coords-v">{formatRA(w[0])} {formatDec(w[1])}</span>
          <CopyButton text={`${formatRA(w[0])} ${formatDec(w[1])}`} label="coordinates" />
        </div>
      ))}
      <div className="small muted">
        {sep != null && p.targetWorld && w && sep > 1e-5 ? `${formatAngle(sep)} ${compassPoint(positionAngle(...p.targetWorld, ...w))} of the target · ` : ""}
        {p.sel.kind === "source" ? `at ${p.year.toFixed(2)} (catalogue epoch ${p.sel.src.epoch.toFixed(1)})` : p.sel.kind === "target" ? `at ${p.year.toFixed(2)}` : "fixed position"}
      </div>

      {p.counterparts.length > 0 && (
        <div className="pick-also">
          <span className="micro">Same object in</span>
          {p.counterparts.map((c) => (
            <button key={`${c.cat}:${c.src.id}`} className="also-row" onClick={() => p.onSelect({ kind: "source", cat: c.cat, src: c.src })} title={String(c.src.id)}>
              <span className={`swatch ${CATALOG_STYLE[c.cat].shape}`} style={{ color: CATALOG_STYLE[c.cat].color }} />
              <span className="also-id">{String(c.src.id)}</span>
              <span className="muted">{formatAngle(c.sep)}</span>
            </button>
          ))}
        </div>
      )}

      <div className="pick-actions">
        {p.onAnalyze && (
          <button className="btn pick-cta primary-cta" onClick={p.onAnalyze} title="Identification, photometry, spectrum, classification, variability, motion and nearby tracklets, with a verdict">
            <Sparkles size={13} /> Analyze
          </button>
        )}
        {p.sel.kind === "target" ? (
          <button className="btn pick-cta" onClick={p.onDetails}>Spectrum &amp; light curve</button>
        ) : (
          p.onMeasure && p.audience === "research" && (
            <button className="btn pick-cta" disabled={measure.state === "busy"} onClick={runMeasure} title="Forced photometry at this position in every exposure">
              {measure.state === "busy" ? <><Loader2 size={13} className="spin" /> Measuring every exposure…</> : "Measure spectrum & light curve"}
            </button>
          )
        )}
        <div className="pick-row">
          {p.onCentre && (
            <button className="btn ghost small-btn" onClick={p.onCentre} title="Build a new field centred here">
              <LocateFixed size={13} /> Centre field here
            </button>
          )}
          {p.onVerify && p.audience === "research" && (
            <button className="btn ghost small-btn" onClick={p.onVerify} title="PSF fit and cutout in every exposure: is it real?">
              <ScanEye size={13} /> Verify
            </button>
          )}
          {p.onFollow && p.audience === "research" && (
            <button className="btn ghost small-btn" onClick={p.onFollow} title="Build a new field that moves with this source, so it stays centred in every epoch">
              <Footprints size={13} /> Follow its motion
            </button>
          )}
        </div>
      </div>
      {measure.state === "error" && <div className="small" style={{ color: "var(--err)" }}>{measure.message}</div>}

      {w && (
        <div className="pick-links">
          {externalLinks(w[0], w[1], p.fovArcmin, simbad ? String(p.sel.kind === "source" ? p.sel.src.id : "") : null).map((l) => (
            <a key={l.label} href={l.href} target="_blank" rel="noreferrer" title={l.hint}>
              {l.label} <ArrowUpRight size={11} />
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
