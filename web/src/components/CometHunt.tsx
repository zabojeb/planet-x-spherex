/**
 * Comet hunt (ADR-0063), one rail panel in Explore and Research: run the comet search on the field (or the object
 * check on a co-moving sequence), see the candidates ranked by comet-likeness with a known / unknown badge, their
 * cutout strip and spectrum, export ADES; and "Comets now" from the server's known-comet watch, a click away from a
 * co-moving sequence of any of them. State lives in useCometHunt (the panel unmounts when its popover closes).
 */
import { Download, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { adesUrl, cometSearch, cometWatch, refreshCometWatch, type Progress } from "../api";
import { evidenceLine, motionLine, scoreBand, spectrumPath, stampPixels, wlToX, type CometCandidate, type CometSearch, type CometWatch } from "../comets";
import type { Meta } from "../types";
import type { SkyMark } from "./SkyView";
import { Switch } from "./ui/primitives";
import "./CometHunt.css";

const SCORE_LABEL = "comet-likeness (heuristic, not a probability)";

type Job<T> = { state: "idle" } | { state: "running"; progress: Progress | null } | { state: "done"; result: T } | { state: "error"; message: string };

const progressText = (p: Progress | null) =>
  !p ? "Starting" : p.ahead ? `${p.ahead} ahead in the queue` : `${p.stage.replace(/_/g, " ")}${p.total ? ` · ${p.done}/${p.total}` : ""}`;

/** All comet-hunt state for one sequence; its marks go on the sky. */
export function useCometHunt(meta: Meta | null) {
  const id = meta?.id ?? null;
  const [job, setJob] = useState<Job<CometSearch>>({ state: "idle" });
  const [show, setShow] = useState(true);
  const [active, setActive] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);

  useEffect(() => {
    setJob({ state: "idle" });
    setActive(null);
    return () => {
      ctrl.current?.abort();
      ctrl.current = null;
    };
  }, [id]);

  const run = useCallback(() => {
    if (!id) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setJob({ state: "running", progress: null });
    cometSearch(id, (progress) => !c.signal.aborted && setJob({ state: "running", progress }), c.signal).then(
      (result) => !c.signal.aborted && setJob({ state: "done", result }),
      (e) => !c.signal.aborted && setJob({ state: "error", message: String(e?.message || e) }),
    );
  }, [id]);
  const cancel = () => {
    ctrl.current?.abort();
    setJob({ state: "idle" });
  };

  const cands = useMemo(() => (job.state === "done" ? job.result.candidates.comets : []), [job]);
  const marks: SkyMark[] = useMemo(() => {
    if (!show || job.state !== "done" || job.result.mode !== "field") return [];
    return cands.slice(0, 40).map((c) => ({
      key: `h:${c.id}`,
      kind: "tracklet" as const,
      x: c.x,
      y: c.y,
      path: c.path && c.path.length > 1 ? [c.path[0], c.path[c.path.length - 1]] : undefined,
      label: `${c.id} · ${c.comet_likeness.score.toFixed(0)}`,
      active: active === c.id,
      title: `Comet hunt ${c.id}: comet-likeness ${c.comet_likeness.score.toFixed(0)} · ${c.status}`,
    }));
  }, [show, job, cands, active]);
  return { id, job, run, cancel, cands, marks, show, setShow, active, setActive };
}
export type CometHunt = ReturnType<typeof useCometHunt>;

/** A stamp drawn on a canvas, one cell per pixel, scaled with CSS (pixelated). */
function Stamp({ img, size = 38, title }: { img: (number | null)[][] | null | undefined; size?: number; title?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv || !img || !img.length) return;
    const h = img.length;
    const w = img[0].length;
    cv.width = w;
    cv.height = h;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const px = stampPixels(img);
    const data = ctx.createImageData(w, h);
    for (let i = 0; i < px.length; i++) {
      // grid rows run south to north: flip so north is up
      const r = h - 1 - Math.floor(i / w);
      const o = (r * w + (i % w)) * 4;
      const v = px[i];
      data.data[o] = data.data[o + 1] = data.data[o + 2] = v < 0 ? 0 : v;
      data.data[o + 3] = v < 0 ? 0 : 255;
    }
    ctx.putImageData(data, 0, 0);
  }, [img]);
  if (!img) return null;
  return <canvas ref={ref} className="comet-stamp" style={{ width: size, height: size }} title={title} />;
}

function Spectrum({ c }: { c: CometCandidate }) {
  const W = 220;
  const H = 54;
  const pts = c.spectrum.points ?? [];
  if (pts.length < 6) return null;
  return (
    <svg className="comet-spec" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="Spectrum along the track">
      {[
        [4.26, "CO₂"],
        [4.67, "CO"],
        [3.0, "H₂O"],
      ].map(([wl, name]) => (
        <g key={name as string}>
          <line x1={wlToX(wl as number, W)} x2={wlToX(wl as number, W)} y1={0} y2={H} className="comet-spec-band" />
          <text x={wlToX(wl as number, W) + 2} y={9} className="comet-spec-label">{name as string}</text>
        </g>
      ))}
      <polyline points={spectrumPath(pts, W, H)} className="comet-spec-line" />
    </svg>
  );
}

function Badge({ c }: { c: CometCandidate }) {
  if (c.status === "known") return <span className={`comet-badge known${c.known?.kind === "comet" ? " comet" : ""}`} title={c.known?.name}>{c.known?.kind === "comet" ? "known comet" : "known"}</span>;
  if (c.status === "unidentified") return <span className="comet-badge new" title="No JPL small body moved along this track as seen from SPHEREx">unknown</span>;
  return <span className="comet-badge" title="Not checked against JPL (outside the best candidates, or JPL unavailable)">unchecked</span>;
}

function Detail({ c, research }: { c: CometCandidate; research: boolean }) {
  const size = c.coma.size;
  return (
    <div className="comet-detail">
      <div className="comet-strip" aria-label="Cutouts along the track (static sky removed), the co-added stamp and a field star">
        {(c.strip ?? []).map((s, i) => (
          <Stamp key={i} img={s.image} title={`${s.band} · MJD ${s.mjd.toFixed(3)}`} />
        ))}
        {c.coma.stamp && <Stamp img={c.coma.stamp} size={46} title="Co-added along the track" />}
        {c.coma.psf_stamp && <Stamp img={c.coma.psf_stamp} size={46} title="A field star, built the same way" />}
      </div>
      <Spectrum c={c} />
      <ul className="comet-why">
        {c.comet_likeness.why.map((w) => (
          <li key={w}>{w}</li>
        ))}
        {research && size?.fwhm_arcsec != null && (
          <li className="num">
            FWHM {size.fwhm_arcsec.toFixed(1)}″ vs star {size.psf_fwhm_arcsec?.toFixed(1) ?? "?"}″{size.intrinsic_fwhm_arcsec > 0 ? ` · coma ≈ ${size.intrinsic_fwhm_arcsec.toFixed(1)}″` : ""}
          </li>
        )}
        {research && c.distance_au != null && <li className="num">parallax distance ≈ {c.distance_au.toFixed(2)} au</li>}
        {c.r_au != null && <li className="num">r = {c.r_au.toFixed(2)} au · Δ = {c.delta_au?.toFixed(2)} au</li>}
      </ul>
    </div>
  );
}

export function CometPanel(p: {
  t: CometHunt;
  research: boolean;
  comoving: boolean;
  building: boolean;
  onPick: (c: CometCandidate) => void;
  /** Open a co-moving sequence of a known comet (the MOST search and build). */
  onComet: (designation: string, label: string) => void;
}) {
  const { t } = p;
  const j = t.job;
  const [watch, setWatch] = useState<{ data: CometWatch | null; error: string | null }>({ data: null, error: null });
  const loadWatch = useCallback(() => {
    cometWatch().then(
      (data) => setWatch({ data, error: null }),
      (e) => setWatch({ data: null, error: String(e?.message || e) }),
    );
  }, []);
  useEffect(loadWatch, [loadWatch]);
  const refresh = () => refreshCometWatch().then(() => window.setTimeout(loadWatch, 1500), () => undefined);
  const list = t.cands.slice(0, 30);
  const sel = list.find((c) => c.id === t.active) ?? null;
  const res = j.state === "done" ? j.result : null;
  return (
    <div className="field-panel comet-panel">
      <div className="micro menu-title">{p.comoving ? "Is it a comet?" : "Comet hunt"}</div>
      {p.building && <p className="small muted">Available once every exposure has loaded.</p>}
      {!p.building && j.state === "idle" && (
        <>
          <p className="small muted">
            {p.comoving
              ? "Coma against field stars, CO₂ / CO bands and the orbit, visit by visit."
              : "Moving sources in this field, fast and slow, ranked by how comet-like they look: a coma, gas bands, an orbit no asteroid explains."}
          </p>
          <button className="btn primary-cta" onClick={t.run}>
            <Sparkles size={14} /> {p.comoving ? "Check this object" : "Search this field"}
          </button>
        </>
      )}
      {j.state === "running" && (
        <div className="field-running">
          <Loader2 size={14} className="spin" /> <span className="small">{progressText(j.progress)}</span>
          <button className="btn ghost small-btn" onClick={t.cancel}>Cancel</button>
        </div>
      )}
      {j.state === "error" && (
        <p className="form-hint err">
          {j.message} <button className="btn ghost small-btn" onClick={t.run}>Retry</button>
        </p>
      )}
      {res && (
        <>
          {res.mode === "field" ? (
            <Switch
              checked={t.show}
              onChange={t.setShow}
              label="Show on the sky"
              hint={`${res.counts?.candidates ?? 0} movers · ${res.counts?.comet_like ?? 0} comet-like · ${res.counts?.unidentified ?? 0} unknown`}
            />
          ) : (
            <p className="small muted">{res.object}: best visit {res.summary?.max_score?.toFixed(0) ?? "?"} / 100</p>
          )}
          {list.length > 0 && <p className="form-hint">Score: {SCORE_LABEL}, 0–100.</p>}
          <div className="cand-list">
            {list.length ? (
              list.map((c) => {
                const b = scoreBand(c.comet_likeness.score);
                return (
                  <button
                    key={c.id}
                    className={`cand-row${t.active === c.id ? " on" : ""}`}
                    onClick={() => {
                      t.setActive(t.active === c.id ? null : c.id);
                      p.onPick(c);
                    }}
                  >
                    <span className="cand-title">
                      <span className={`comet-score ${b.tone}`} title={`${SCORE_LABEL}: ${b.label}`}>{c.comet_likeness.score.toFixed(0)}</span>
                      {c.kind === "object" ? `Visit ${c.epoch + 1}` : c.id} <Badge c={c} />
                    </span>
                    <span className="cand-score num">{motionLine(c)}</span>
                    <span className="cand-sub">{p.research ? evidenceLine(c) : b.label}</span>
                  </button>
                );
              })
            ) : (
              <p className="form-hint">No moving source in this field.</p>
            )}
          </div>
          {sel && <Detail c={sel} research={p.research} />}
          {res.mode === "field" && p.research && (
            <div className="exports">
              <a className="btn ghost" href={adesUrl(res.id)} download>
                <Download size={14} /> Candidates <small>ADES</small>
              </a>
              <a className="btn ghost" href={adesUrl(res.id) + "?all=true"} download>
                <Download size={14} /> All tracks <small>ADES</small>
              </a>
            </div>
          )}
          {res.caveats?.length > 0 && (
            <ul className="fine comet-caveats" aria-label="Caveats">
              {res.caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          )}
        </>
      )}

      <div className="micro menu-title field-sep">Comets now</div>
      {watch.error && <p className="form-hint err">{watch.error}</p>}
      {!watch.data && !watch.error && <p className="form-hint">Loading…</p>}
      {watch.data && <CometsNow w={watch.data} research={p.research} onComet={p.onComet} onRefresh={refresh} />}
    </div>
  );
}

function CometsNow({ w, research, onComet, onRefresh }: { w: CometWatch; research: boolean; onComet: (d: string, label: string) => void; onRefresh: () => void }) {
  const watched = new Map(w.watched.map((c) => [c.designation, c]));
  const rows = [...w.watched.map((c) => c.now ?? ({ designation: c.designation, name: c.name } as CometWatch["now"][number])), ...w.now.filter((c) => !watched.has(c.designation))].slice(0, 12);
  return (
    <>
      <div className="cand-list short">
        {rows.map((c) => {
          const s = watched.get(c.designation);
          const ob = s?.activity?.outbursts?.length;
          const co2 = s?.latest?.co2_snr;
          return (
            <button key={c.designation} className="cand-row" onClick={() => onComet(c.designation, c.name ?? c.designation)} title="Open a co-moving SPHEREx sequence of this comet">
              <span className="cand-title">
                {c.name ?? c.designation}
                {ob ? <span className="comet-badge new">outburst</span> : null}
              </span>
              <span className="cand-score num">{c.mag != null ? `m ${c.mag.toFixed(1)}` : ""}</span>
              <span className="cand-sub">
                {c.r_au != null ? `r ${c.r_au.toFixed(2)} au · Δ ${c.delta_au.toFixed(2)} au` : s?.stage ?? ""}
                {s?.latest?.afrho_cm != null ? ` · Afρ ${s.latest.afrho_cm.toFixed(0)} cm` : ""}
                {co2 != null && co2 >= 3 ? ` · CO₂ ${co2.toFixed(0)}σ` : ""}
              </span>
            </button>
          );
        })}
      </div>
      {research && (
        <p className="form-hint">
          {w.refresh.status === "running" ? `Watch refreshing: ${w.refresh.stage ?? ""}` : w.updated ? `Watch updated ${w.updated.slice(0, 16).replace("T", " ")} UTC` : "Watch not run yet"}{" "}
          {w.refresh.status !== "running" && (
            <button className="btn ghost small-btn" onClick={onRefresh} title="Find the watched comets in SPHEREx data and measure their activity (runs on the server at low priority)">
              <RefreshCw size={12} /> Refresh
            </button>
          )}
        </p>
      )}
    </>
  );
}
