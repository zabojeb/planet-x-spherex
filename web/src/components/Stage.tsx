/**
 * The hero image: a square frame on black with reticle corners, the canvas, aligned SVG overlays and
 * monospaced HUD readouts in the corners. Zoomed content pans inside the fixed frame. A new sequence
 * plays a short lock-on animation; blinking itself is instant.
 */
import { Maximize2, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Progress } from "../api";
import { formatDec, formatRA, pixelToWorld, propagate, totalPm, worldToPixel } from "../astro";
import { CATALOG_STYLE, FLAG_OVERLAY } from "../colors";
import type { Display } from "../display";
import { LUTS, paintDiverging, paintFlags, paintRGB, paintScalar } from "../render";
import type { Overlay, ViewState } from "../state";
import type { CatalogSource, Grid } from "../types";

export interface StageHud {
  epoch: string; // "EPOCH 02 / 03"
  dates: string; // "11 MAR → 05 MAY 2026"
  spectral: string; // "D6 · λ 4.42–5.00 µm"
  frames: string; // "24 FRAMES" or "MINUS EPOCH 01"
  extra?: string;
  target?: [number, number] | null; // RA, Dec of the target at the shown date
}

interface Props {
  grid: Grid | null;
  display: Display | null;
  view: ViewState;
  overlays: Set<Overlay>;
  flags: Uint32Array | null;
  flagMask: number;
  catalogs: Record<string, CatalogSource[]>;
  year: number;
  span: [number, number] | null;
  targetPx: [number, number] | null;
  track: [number, number][];
  trackIndex: number;
  hud: StageHud | null;
  progress: Progress | null;
  lockKey: string;
  error: string | null;
  onRetry: () => void;
  size: number;
  onZoom: (zoom: number) => void;
  compact?: boolean;
}

const HIGH_PM = 100; // mas/yr
const MAX_MARKERS = 160; // per catalog: the brightest sources in the field plus every high-proper-motion one
const ZOOM_STEPS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 24];

function scaleBar(pixscale: number, scale: number): { px: number; label: string } {
  for (const arcmin of [0.25, 0.5, 1, 2, 5, 10]) {
    const px = ((arcmin * 60) / pixscale) * scale;
    if (px >= 48) return { px, label: arcmin < 1 ? `${arcmin * 60}″` : `${arcmin}′` };
  }
  return { px: ((10 * 60) / pixscale) * scale, label: "10′" };
}

function loaderText(p: Progress): { text: string; pct: number | null } {
  const pct = p.total > 0 ? Math.round((100 * p.done) / p.total) : null;
  switch (p.stage) {
    case "search":
      return { text: "Querying the IRSA archive", pct: null };
    case "fetch":
      return { text: `Fetching ${p.total || ""} exposures from S3`, pct };
    case "stack":
      return { text: "Aligning and co-adding epochs", pct: null };
    case "download":
    case "done":
      return { text: "Downloading results", pct: null };
    default:
      return { text: "Waiting for a free worker", pct: null };
  }
}

function hexRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

export function Stage(p: Props) {
  const frameRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLCanvasElement>(null);
  const flagRef = useRef<HTMLCanvasElement>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [tip, setTip] = useState<{ src: CatalogSource; cat: string; sx: number; sy: number } | null>(null);
  const n = p.grid?.n ?? 0;
  const fitScale = n ? p.size / n : 1;
  const scale = n ? (p.view.zoom ? p.view.zoom : fitScale) : 1;
  const content = n * scale;
  const u = 1 / scale; // data units per screen pixel

  useLayoutEffect(() => {
    const f = frameRef.current;
    if (!f) return;
    f.scrollLeft = Math.max(0, (content - p.size) / 2);
    f.scrollTop = Math.max(0, (content - p.size) / 2);
  }, [content, p.size]);

  useEffect(() => {
    const c = imgRef.current;
    const d = p.display;
    if (!c || !n || !d || d.kind === "empty") return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const out = ctx.createImageData(n, n);
    if (d.kind === "scalar") paintScalar(d.img, n, d.lo, d.hi, p.view.stretch, LUTS[p.view.cmap], out);
    else if (d.kind === "diverging") paintDiverging(d.img, n, d.lim, p.view.stretch, out);
    else paintRGB(d.ch, n, d.lims, p.view.stretch, out);
    ctx.putImageData(out, 0, 0);
  }, [p.display, p.view.stretch, p.view.cmap, n]);

  const showFlags = p.overlays.has("flags") && !!p.flags && p.flagMask !== 0;
  useEffect(() => {
    const c = flagRef.current;
    if (!c || !n || !showFlags || !p.flags) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const out = ctx.createImageData(n, n);
    paintFlags(p.flags, n, p.flagMask, out, hexRgb(FLAG_OVERLAY));
    ctx.putImageData(out, 0, 0);
  }, [p.flags, p.flagMask, n, showFlags]);

  const toSvg = (x: number, y: number): [number, number] => [x + 0.5, n - y - 0.5];

  const markers = useMemo(() => {
    if (!p.grid) return [];
    const out: { cat: string; src: CatalogSource; x: number; y: number; from?: [number, number]; to?: [number, number]; pm: number }[] = [];
    for (const cat of ["gaia", "catwise", "2mass"] as const) {
      if (!p.overlays.has(cat)) continue;
      let kept = 0;
      for (const src of p.catalogs[cat] ?? []) {
        const [ra, dec] = propagate(src, p.year);
        const [x, y] = worldToPixel(p.grid, ra, dec);
        if (x < -2 || y < -2 || x > n + 1 || y > n + 1) continue;
        const pm = totalPm(src);
        if (pm <= HIGH_PM && kept >= MAX_MARKERS) continue;
        kept++;
        const m: (typeof out)[number] = { cat, src, x, y, pm };
        if (pm > HIGH_PM && p.span) {
          m.from = worldToPixel(p.grid, ...propagate(src, p.span[0]));
          m.to = worldToPixel(p.grid, ...propagate(src, p.span[1]));
        }
        out.push(m);
      }
    }
    return out;
  }, [p.grid, p.catalogs, p.overlays, p.year, p.span, n]);

  const readout = (() => {
    if (!cursor || !p.display || !n || !p.grid) return null;
    const ix = Math.round(cursor.x);
    const iy = Math.round(cursor.y);
    if (ix < 0 || iy < 0 || ix >= n || iy >= n) return null;
    const i = iy * n + ix;
    const d = p.display;
    let value: string | null = null;
    if (d.kind === "scalar" || d.kind === "diverging") {
      const v = d.img[i];
      value = Number.isFinite(v) ? `${v.toPrecision(3)} ${d.unit}` : "NO DATA";
    } else if (d.kind === "rgb") value = d.ch.map((c, k) => `${"RGB"[k]} ${Number.isFinite(c[i]) ? c[i].toPrecision(3) : "—"}`).join(" ");
    const [ra, dec] = pixelToWorld(p.grid, cursor.x, cursor.y);
    return { ra: formatRA(ra), dec: formatDec(dec), value, px: `X ${ix} Y ${iy}` };
  })();

  function onMove(e: React.MouseEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    setCursor({ x: (e.clientX - box.left) / scale - 0.5, y: n - (e.clientY - box.top) / scale - 0.5 });
  }

  function zoomBy(dir: 1 | -1) {
    if (dir > 0) {
      const next = ZOOM_STEPS.find((z) => z > scale + 1e-6);
      if (next) p.onZoom(next);
    } else {
      const prev = [...ZOOM_STEPS].reverse().find((z) => z < scale - 1e-6);
      p.onZoom(prev && prev > fitScale + 1e-6 ? prev : 0);
    }
  }

  const bar = p.grid ? scaleBar(p.grid.pixscale_arcsec, scale) : null;
  const d = p.display;
  const hasImage = n > 0 && d && d.kind !== "empty";
  const loader = p.progress ? loaderText(p.progress) : null;

  return (
    <div className={`stage-shell${p.compact ? " compact" : ""}`} style={{ width: p.size, height: p.size }}>
      <div className="reticle" key={`r${p.lockKey}`} aria-hidden>
        <span className="rc tl" />
        <span className="rc tr" />
        <span className="rc bl" />
        <span className="rc br" />
      </div>
      <div className="stage" style={{ width: p.size, height: p.size }}>
        <div className="stage-frame" ref={frameRef}>
          {n > 0 ? (
            <div className={`stage-content${p.progress ? " dim" : ""}`} style={{ width: content, height: content }} onMouseMove={onMove} onMouseLeave={() => setCursor(null)}>
              <canvas
                ref={imgRef}
                width={n}
                height={n}
                className={p.view.smooth ? "smooth" : "crisp"}
                style={{ width: content, height: content, visibility: hasImage ? "visible" : "hidden" }}
                aria-label="SPHEREx image"
              />
              {showFlags && <canvas ref={flagRef} width={n} height={n} className="crisp flags-layer" style={{ width: content, height: content }} />}
              <svg className="overlay" width={content} height={content} viewBox={`0 0 ${n} ${n}`}>
                {p.overlays.has("track") && p.track.length > 1 && (
                  <g className="track">
                    <polyline points={p.track.map(([x, y]) => toSvg(x, y).join(",")).join(" ")} vectorEffect="non-scaling-stroke" />
                    {p.track.map(([x, y], i) => {
                      const [sx, sy] = toSvg(x, y);
                      return <circle key={i} cx={sx} cy={sy} r={(i === p.trackIndex ? 2.5 : 1.5) * u} className={i === p.trackIndex ? "on" : ""} />;
                    })}
                  </g>
                )}
                {markers.map((m, i) => {
                  const st = CATALOG_STYLE[m.cat];
                  const [sx, sy] = toSvg(m.x, m.y);
                  const r = (m.pm > HIGH_PM ? 7 : 5) * u;
                  const common = {
                    fill: "none",
                    stroke: st.color,
                    strokeWidth: m.pm > HIGH_PM ? 2 : 1.25,
                    vectorEffect: "non-scaling-stroke" as const,
                    onMouseEnter: (e: React.MouseEvent) => {
                      const frame = frameRef.current?.getBoundingClientRect();
                      setTip({ src: m.src, cat: m.cat, sx: e.clientX - (frame?.left ?? 0), sy: e.clientY - (frame?.top ?? 0) });
                    },
                    onMouseLeave: () => setTip(null),
                  };
                  const shape =
                    st.shape === "circle" ? (
                      <circle cx={sx} cy={sy} r={r} {...common} />
                    ) : st.shape === "square" ? (
                      <rect x={sx - r * 0.85} y={sy - r * 0.85} width={r * 1.7} height={r * 1.7} {...common} />
                    ) : (
                      <path d={`M${sx},${sy - r} L${sx + r},${sy} L${sx},${sy + r} L${sx - r},${sy} Z`} {...common} />
                    );
                  let vector = null;
                  if (m.from && m.to) {
                    const [fx, fy] = toSvg(...m.from);
                    const [tx, ty] = toSvg(...m.to);
                    const ang = Math.atan2(ty - fy, tx - fx);
                    const len = 16 * u;
                    const hx = sx + Math.cos(ang) * (r + len);
                    const hy = sy + Math.sin(ang) * (r + len);
                    vector = (
                      <g className="pm-vector" stroke={st.color}>
                        <line x1={fx} y1={fy} x2={tx} y2={ty} vectorEffect="non-scaling-stroke" strokeWidth={2} />
                        <line x1={sx + Math.cos(ang) * r} y1={sy + Math.sin(ang) * r} x2={hx} y2={hy} vectorEffect="non-scaling-stroke" strokeWidth={1.5} />
                        <path
                          d={`M${hx},${hy} L${hx - Math.cos(ang - 0.5) * 5 * u},${hy - Math.sin(ang - 0.5) * 5 * u} M${hx},${hy} L${hx - Math.cos(ang + 0.5) * 5 * u},${hy - Math.sin(ang + 0.5) * 5 * u}`}
                          vectorEffect="non-scaling-stroke"
                          strokeWidth={1.5}
                          fill="none"
                        />
                        <text x={sx + r + 3 * u} y={sy - r - 2 * u} fontSize={11 * u} strokeWidth={2.5 * u} className="pm-label">
                          {(m.pm / 1000).toFixed(m.pm >= 1000 ? 1 : 2)}″/yr
                        </text>
                      </g>
                    );
                  }
                  return (
                    <g key={`${m.cat}${i}`}>
                      {vector}
                      {shape}
                    </g>
                  );
                })}
                {p.overlays.has("target") && p.targetPx && (() => {
                  const [sx, sy] = toSvg(...p.targetPx);
                  const a = 7 * u;
                  const b = 16 * u;
                  const lines = [
                    [sx - b, sy, sx - a, sy],
                    [sx + a, sy, sx + b, sy],
                    [sx, sy - b, sx, sy - a],
                    [sx, sy + a, sx, sy + b],
                  ];
                  return (
                    <g className="target-mark" key={`t${p.lockKey}`} style={{ transformOrigin: `${sx}px ${sy}px` }}>
                      {lines.map((l, i) => (
                        <line key={`h${i}`} x1={l[0]} y1={l[1]} x2={l[2]} y2={l[3]} className="halo" vectorEffect="non-scaling-stroke" />
                      ))}
                      {lines.map((l, i) => (
                        <line key={`l${i}`} x1={l[0]} y1={l[1]} x2={l[2]} y2={l[3]} vectorEffect="non-scaling-stroke" />
                      ))}
                    </g>
                  );
                })()}
              </svg>
            </div>
          ) : (
            <div className="skeleton stage-skeleton" aria-hidden />
          )}
        </div>

        {/* HUD, fixed to the frame */}
        {p.hud && n > 0 && (
          <div className="hud hud-tl">
            <div className="hud-key">{p.hud.epoch}</div>
            <div className="hud-val">{p.hud.dates}</div>
            {p.hud.extra && <div className="hud-key">{p.hud.extra}</div>}
          </div>
        )}
        {n > 0 && (
          <div className="hud hud-tr">
            <div className="hud-tools" role="group" aria-label="Zoom">
              <button className="hud-btn" onClick={() => zoomBy(-1)} aria-label="Zoom out" title="Zoom out" disabled={!p.view.zoom}>
                <ZoomOut size={15} />
              </button>
              <span className="hud-zoom">{p.view.zoom ? `${p.view.zoom}×` : "FIT"}</span>
              <button className="hud-btn" onClick={() => zoomBy(1)} aria-label="Zoom in" title="Zoom in">
                <ZoomIn size={15} />
              </button>
              <button className="hud-btn" onClick={() => p.onZoom(0)} aria-label="Fit to frame" title="Fit to frame" disabled={!p.view.zoom}>
                <Maximize2 size={13} />
              </button>
            </div>
            {p.hud && (
              <div className="hud-right">
                <div className="hud-val">{p.hud.spectral}</div>
                <div className="hud-key">{p.hud.frames}</div>
              </div>
            )}
          </div>
        )}
        {n > 0 && p.overlays.has("compass") && bar && (
          <div className="hud hud-bl" aria-hidden>
            <svg width={bar.px + 64} height={40}>
              <g transform="translate(30 32)" className="compass">
                <line x1={0} y1={0} x2={0} y2={-20} />
                <line x1={0} y1={0} x2={-20} y2={0} />
                <text x={0} y={-24} textAnchor="middle">N</text>
                <text x={-25} y={4} textAnchor="end">E</text>
              </g>
              <g transform="translate(52 28)" className="scalebar">
                <line x1={0} y1={0} x2={bar.px} y2={0} />
                <line x1={0} y1={-3} x2={0} y2={3} />
                <line x1={bar.px} y1={-3} x2={bar.px} y2={3} />
                <text x={bar.px / 2} y={-6} textAnchor="middle">{bar.label}</text>
              </g>
            </svg>
          </div>
        )}
        {n > 0 && (readout || p.hud?.target) && (
          <div className={`hud hud-br${readout ? " live" : ""}`} aria-hidden>
            {readout ? (
              <>
                <div className="hud-key">Cursor · {readout.px}</div>
                <div className="hud-val">RA {readout.ra}  DEC {readout.dec}</div>
                {readout.value && <div className="hud-val">{readout.value}</div>}
              </>
            ) : (
              p.hud?.target && (
                <>
                  <div className="hud-key">Target</div>
                  <div className="hud-val">RA {formatRA(p.hud.target[0])}  DEC {formatDec(p.hud.target[1])}</div>
                </>
              )
            )}
          </div>
        )}
        {loader && (
          <div className="hud-loader" role="status" aria-live="polite">
            <div className="hud-loader-text">
              {loader.text}
              {loader.pct != null && <span> — {loader.pct}%</span>}
            </div>
            <div className={`hud-loader-bar${loader.pct == null ? " indeterminate" : ""}`}>
              <span style={loader.pct == null ? undefined : { width: `${Math.max(2, loader.pct)}%` }} />
            </div>
            {p.progress && p.progress.total > 0 && (
              <div className="hud-key">
                {p.progress.done} / {p.progress.total}
              </div>
            )}
          </div>
        )}
        {d?.kind === "empty" && !p.progress && n > 0 && <div className="stage-note">{d.message}</div>}
        {p.error && !p.progress && (
          <div className="stage-note error" role="alert">
            <span>{p.error}</span>
            <button className="btn" onClick={p.onRetry}>
              <RotateCcw size={14} /> Try again
            </button>
          </div>
        )}
        {tip && (
          <div className="tooltip" style={{ left: Math.min(tip.sx + 12, p.size - 240), top: Math.max(tip.sy - 10, 4) }}>
            <div className="tt-title">{tip.src.id}</div>
            <div>
              {CATALOG_STYLE[tip.cat].label} · {tip.src.mag_band} {tip.src.mag != null ? tip.src.mag.toFixed(2) : "—"}
            </div>
            {totalPm(tip.src) > 0 && (
              <div>
                Proper motion {totalPm(tip.src).toFixed(0)} mas/yr ({tip.src.pmra?.toFixed(0)}, {tip.src.pmdec?.toFixed(0)})
              </div>
            )}
            <div className="muted">
              Shown for {p.year.toFixed(2)} · catalogue epoch {tip.src.epoch.toFixed(1)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
