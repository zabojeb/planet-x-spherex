/**
 * Full-bleed interactive sky (the Google Earth model): drag to pan, wheel or pinch to zoom around the
 * cursor, double-click to zoom in, click a source or the target to select it. Overlays are SVG in data
 * units on the same transformed layer as the canvas. Blinking repaints only the canvas.
 */
import { RotateCcw, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Progress } from "../api";
import { formatDec, formatRA, pixelToWorld, propagate, totalPm, worldToPixel } from "../astro";
import { CATALOG_STYLE, FLAG_OVERLAY } from "../colors";
import type { Display } from "../display";
import { useElementWidth, useViewportHeight } from "../hooks";
import { LUTS, paintDiverging, paintFlags, paintRGB, paintScalar } from "../render";
import type { Overlay, ViewState } from "../state";
import type { CatalogSource, Grid } from "../types";

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
  targetName: string;
  targetWorld: [number, number] | null;
  track: [number, number][];
  trackIndex: number;
  dateLabel: string;
  progress: Progress | null;
  lockKey: string;
  error: string | null;
  onRetry: () => void;
  onZoom: (zoom: number) => void;
  onInteract: () => void;
  onOpenDetails: () => void;
  /** Forced photometry of a clicked catalogue source; resolves when its curves are in the drawer. */
  onMeasure?: (cat: string, src: CatalogSource) => Promise<void>;
  shiftX?: number; // horizontal offset of the view centre (room for the landing index)
  /** Screen space taken by floating chrome (phones): the image is fitted and centred between these. */
  frame?: { top: number; bottom: number };
}

const HIGH_PM = 100; // mas/yr
const MAX_MARKERS = 160;

type Selection = { kind: "target" } | { kind: "source"; cat: string; src: CatalogSource };

function loaderText(p: Progress): { text: string; pct: number | null } {
  const pct = p.total > 0 ? Math.round((100 * p.done) / p.total) : null;
  switch (p.stage) {
    case "busy":
      return { text: `Many users right now · retrying in ${p.retryIn ?? 1}s`, pct: null };
    case "search":
      return { text: "Querying the IRSA archive", pct: null };
    case "fetch":
      return { text: `Fetching ${p.total || ""} exposures from S3`, pct };
    case "stack":
      return { text: "Aligning and co-adding epochs", pct: null };
    case "publish":
      return { text: "Publishing results", pct: null };
    case "download":
    case "done":
      return { text: "Downloading results", pct };
    default:
      return { text: "Waiting for a free worker", pct: null };
  }
}

function hexRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function scaleBar(pixscale: number, scale: number): { px: number; label: string } {
  for (const arcmin of [0.25, 0.5, 1, 2, 5, 10]) {
    const px = ((arcmin * 60) / pixscale) * scale;
    if (px >= 44) return { px, label: arcmin < 1 ? `${arcmin * 60}″` : `${arcmin}′` };
  }
  return { px: ((10 * 60) / pixscale) * scale, label: "10′" };
}

export function SkyView(p: Props) {
  const [boxRef, W] = useElementWidth<HTMLDivElement>();
  const H = useViewportHeight();
  const imgRef = useRef<HTMLCanvasElement>(null);
  const flagRef = useRef<HTMLCanvasElement>(null);
  const n = p.grid?.n ?? 0;
  const frameTop = p.frame?.top ?? 0;
  const frameH = H - frameTop - (p.frame?.bottom ?? 0);
  const cy = frameTop + frameH / 2;
  const fit = n && W ? (p.frame ? Math.min(0.94 * W, frameH) : 0.8 * Math.min(W, H)) / n : 1;
  const [scale, setScale] = useState(0); // 0 = follow fit
  const [center, setCenter] = useState<[number, number] | null>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [sel, setSel] = useState<Selection | null>(null);
  const [measure, setMeasure] = useState<{ state: "idle" | "busy" } | { state: "error"; message: string }>({ state: "idle" });
  useEffect(() => setMeasure({ state: "idle" }), [sel]);
  const s = scale || (p.view.zoom ? p.view.zoom : fit);
  const c = center ?? [n / 2, n / 2];
  const tx = W / 2 + (p.shiftX ?? 0) - c[0] * s;
  const ty = cy - c[1] * s;
  const u = 1 / s;

  // new sequence: recentre, keep the URL zoom
  useLayoutEffect(() => {
    setCenter(null);
    setScale(0);
    setSel(null);
  }, [p.lockKey]);
  // external zoom changes (Display panel, URL)
  useEffect(() => setScale(0), [p.view.zoom]);

  useEffect(() => {
    const cv = imgRef.current;
    const d = p.display;
    if (!cv || !n || !d || d.kind === "empty") return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const out = ctx.createImageData(n, n);
    if (d.kind === "scalar") paintScalar(d.img, n, d.lo, d.hi, p.view.stretch, LUTS[p.view.cmap], out);
    else if (d.kind === "diverging") paintDiverging(d.img, n, d.lim, p.view.stretch, out);
    else paintRGB(d.ch, n, d.lims, p.view.stretch, out);
    ctx.putImageData(out, 0, 0);
  }, [p.display, p.view.stretch, p.view.cmap, n]);

  const showFlags = p.overlays.has("flags") && !!p.flags && p.flagMask !== 0;
  useEffect(() => {
    const cv = flagRef.current;
    if (!cv || !n || !showFlags || !p.flags) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const out = ctx.createImageData(n, n);
    paintFlags(p.flags, n, p.flagMask, out, hexRgb(FLAG_OVERLAY));
    ctx.putImageData(out, 0, 0);
  }, [p.flags, p.flagMask, n, showFlags]);

  // grid pixel (x right, y up) -> layer coordinates (y down), pixel centres
  const toL = (x: number, y: number): [number, number] => [x + 0.5, n - y - 0.5];
  const toScreen = (x: number, y: number): [number, number] => {
    const [lx, ly] = toL(x, y);
    return [tx + lx * s, ty + ly * s];
  };

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

  // ---------------------------------------------------------------- interaction
  const drag = useRef<{ x: number; y: number; c: [number, number]; moved: boolean } | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ d: number; s: number } | null>(null);
  const zoomTimer = useRef<number>();
  const clampS = (v: number) => Math.min(Math.max(v, fit * 0.6), 48);

  function zoomAt(newS: number, mx: number, my: number) {
    const ns = clampS(newS);
    const px = (mx - tx) / s; // layer coords under the cursor
    const py = (my - ty) / s;
    setScale(ns);
    setCenter([(W / 2 + (p.shiftX ?? 0) - (mx - px * ns)) / ns, (cy - (my - py * ns)) / ns]);
    window.clearTimeout(zoomTimer.current);
    zoomTimer.current = window.setTimeout(() => p.onZoom(Math.abs(ns - fit) < 0.05 ? 0 : Math.round(ns * 10) / 10), 300);
    p.onInteract();
  }

  function local(e: { clientX: number; clientY: number }): [number, number] {
    const b = boxRef.current!.getBoundingClientRect();
    return [e.clientX - b.left, e.clientY - b.top];
  }

  function onWheel(e: React.WheelEvent) {
    if (!n) return;
    const [mx, my] = local(e);
    zoomAt(s * Math.exp(-e.deltaY * 0.0016), mx, my);
  }

  function onPointerDown(e: React.PointerEvent) {
    if (!n) return;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const [x, y] = local(e);
    pointers.current.set(e.pointerId, { x, y });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { d: Math.hypot(a.x - b.x, a.y - b.y), s };
      drag.current = null;
    } else drag.current = { x, y, c, moved: false };
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!n) return;
    const [x, y] = local(e);
    if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, { x, y });
    if (pinch.current && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      zoomAt(pinch.current.s * (d / pinch.current.d), (a.x + b.x) / 2, (a.y + b.y) / 2);
      return;
    }
    const dr = drag.current;
    if (dr) {
      const dx = x - dr.x;
      const dy = y - dr.y;
      if (Math.hypot(dx, dy) > 4) dr.moved = true;
      if (dr.moved) {
        setCenter([dr.c[0] - dx / s, dr.c[1] - dy / s]);
        p.onInteract();
      }
    }
    // cursor in grid coordinates
    setCursor({ x: (x - tx) / s - 0.5, y: n - (y - ty) / s - 0.5 });
  }

  function onPointerUp(e: React.PointerEvent) {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    const dr = drag.current;
    drag.current = null;
    if (!dr || dr.moved || !n) return;
    // click: select the nearest marker or the target within 12 px
    const [mx, my] = local(e);
    let best: Selection | null = null;
    let bd = 12 * 12;
    if (p.targetPx) {
      const [sx, sy] = toScreen(...p.targetPx);
      const d = (sx - mx) ** 2 + (sy - my) ** 2;
      if (d < 18 * 18) {
        best = { kind: "target" };
        bd = d;
      }
    }
    for (const m of markers) {
      const [sx, sy] = toScreen(m.x, m.y);
      const d = (sx - mx) ** 2 + (sy - my) ** 2;
      if (d < bd) {
        bd = d;
        best = { kind: "source", cat: m.cat, src: m.src };
      }
    }
    setSel(best);
  }

  function onDoubleClick(e: React.MouseEvent) {
    const [mx, my] = local(e);
    zoomAt(s * 2, mx, my);
  }

  // ---------------------------------------------------------------- render
  const d = p.display;
  const hasImage = n > 0 && d && d.kind !== "empty";
  const loader = p.progress ? loaderText(p.progress) : null;
  const bar = p.grid ? scaleBar(p.grid.pixscale_arcsec, s) : null;
  const cw = cursor && p.grid && cursor.x >= -0.5 && cursor.y >= -0.5 && cursor.x < n - 0.5 && cursor.y < n - 0.5 ? cursor : null;
  const world = cw && p.grid ? pixelToWorld(p.grid, cw.x, cw.y) : null;
  const value = (() => {
    if (!cw || !d) return null;
    const i = Math.round(cw.y) * n + Math.round(cw.x);
    if (d.kind === "scalar" || d.kind === "diverging") return Number.isFinite(d.img[i]) ? `${d.img[i].toPrecision(3)} ${d.unit}` : "no data";
    return null;
  })();
  const L = 14 * u; // reticle arm length in layer units
  const selPos: [number, number] | null =
    sel?.kind === "target" && p.targetPx ? toScreen(...p.targetPx)
      : sel?.kind === "source" && p.grid ? toScreen(...worldToPixel(p.grid, ...propagate(sel.src, p.year)))
        : null;

  return (
    <div
      className="sky"
      ref={boxRef}
      onWheel={onWheel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onPointerLeave={() => setCursor(null)}
      onDoubleClick={onDoubleClick}
    >
      {n > 0 && (
        <div className={`sky-layer${p.progress ? " dim" : ""}`} style={{ width: n, height: n, transform: `translate(${tx}px, ${ty}px) scale(${s})` }}>
          <canvas ref={imgRef} width={n} height={n} className={p.view.smooth ? "smooth" : "crisp"} style={{ visibility: hasImage ? "visible" : "hidden" }} aria-label="SPHEREx image" />
          {showFlags && <canvas ref={flagRef} width={n} height={n} className="crisp flags-layer" />}
        </div>
      )}
      {n > 0 && W > 0 && (
        // Overlays live in screen space with an SVG transform so strokes stay hairline at any zoom.
        <svg className={`overlay${p.progress ? " dim" : ""}`} width={W} height={H}>
          <g transform={`translate(${tx} ${ty}) scale(${s})`}>
            <g className="reticle" key={`r${p.lockKey}`}>
              {[[0, 0, 1, 1], [n, 0, -1, 1], [0, n, 1, -1], [n, n, -1, -1]].map(([x, y, sx, sy], i) => (
                <path key={i} d={`M${x + sx * L},${y} L${x},${y} L${x},${y + sy * L}`} vectorEffect="non-scaling-stroke" />
              ))}
            </g>
            {p.overlays.has("track") && p.track.length > 1 && (
              <g className="track">
                <polyline points={p.track.map(([x, y]) => toL(x, y).join(",")).join(" ")} vectorEffect="non-scaling-stroke" />
                {p.track.map(([x, y], i) => {
                  const [lx, ly] = toL(x, y);
                  return <circle key={i} cx={lx} cy={ly} r={(i === p.trackIndex ? 2.5 : 1.5) * u} className={i === p.trackIndex ? "on" : ""} />;
                })}
              </g>
            )}
            {markers.map((m, i) => {
              const st = CATALOG_STYLE[m.cat];
              const [lx, ly] = toL(m.x, m.y);
              const r = (m.pm > HIGH_PM ? 7 : 5) * u;
              const common = { fill: "none", stroke: st.color, strokeWidth: m.pm > HIGH_PM ? 2 : 1.2, vectorEffect: "non-scaling-stroke" as const };
              const shape =
                st.shape === "circle" ? <circle cx={lx} cy={ly} r={r} {...common} />
                  : st.shape === "square" ? <rect x={lx - r * 0.85} y={ly - r * 0.85} width={r * 1.7} height={r * 1.7} {...common} />
                    : <path d={`M${lx},${ly - r} L${lx + r},${ly} L${lx},${ly + r} L${lx - r},${ly} Z`} {...common} />;
              let vector = null;
              if (m.from && m.to) {
                const [fx, fy] = toL(...m.from);
                const [tX, tY] = toL(...m.to);
                const ang = Math.atan2(tY - fy, tX - fx);
                const hx = lx + Math.cos(ang) * (r + 16 * u);
                const hy = ly + Math.sin(ang) * (r + 16 * u);
                vector = (
                  <g className="pm-vector" stroke={st.color}>
                    <line x1={fx} y1={fy} x2={tX} y2={tY} vectorEffect="non-scaling-stroke" strokeWidth={2} />
                    <line x1={lx + Math.cos(ang) * r} y1={ly + Math.sin(ang) * r} x2={hx} y2={hy} vectorEffect="non-scaling-stroke" strokeWidth={1.5} />
                    <text x={lx + r + 3 * u} y={ly - r - 2 * u} fontSize={11 * u} strokeWidth={2.5 * u} className="pm-label">
                      {(m.pm / 1000).toFixed(m.pm >= 1000 ? 1 : 2)}″/yr
                    </text>
                  </g>
                );
              }
              return <g key={`${m.cat}${i}`}>{vector}{shape}</g>;
            })}
            {p.overlays.has("target") && p.targetPx && (() => {
              const [lx, ly] = toL(...p.targetPx);
              const a = 7 * u;
              const b = 16 * u;
              const lines = [[lx - b, ly, lx - a, ly], [lx + a, ly, lx + b, ly], [lx, ly - b, lx, ly - a], [lx, ly + a, lx, ly + b]];
              return (
                <g className={`target-mark${sel?.kind === "target" ? " sel" : ""}`} key={`t${p.lockKey}`} style={{ transformOrigin: `${lx}px ${ly}px` }}>
                  {lines.map((l, i) => <line key={`h${i}`} x1={l[0]} y1={l[1]} x2={l[2]} y2={l[3]} className="halo" vectorEffect="non-scaling-stroke" />)}
                  {lines.map((l, i) => <line key={`l${i}`} x1={l[0]} y1={l[1]} x2={l[2]} y2={l[3]} vectorEffect="non-scaling-stroke" />)}
                </g>
              );
            })()}
          </g>
        </svg>
      )}

      {/* minimal HUD: date + cursor coordinates, compass and scale */}
      {n > 0 && (
        <div className="hud-min" aria-hidden>
          {p.overlays.has("compass") && bar && (
            <svg width={bar.px + 50} height={30} className="hud-compass">
              <g transform="translate(18 24)" className="compass">
                <line x1={0} y1={0} x2={0} y2={-14} />
                <line x1={0} y1={0} x2={-14} y2={0} />
                <text x={0} y={-17} textAnchor="middle">N</text>
              </g>
              <g transform="translate(36 22)" className="scalebar">
                <line x1={0} y1={0} x2={bar.px} y2={0} />
                <text x={bar.px / 2} y={-5} textAnchor="middle">{bar.label}</text>
              </g>
            </svg>
          )}
          <div className="hud-line">
            <span>{p.dateLabel}</span>
            {world ? (
              <span className="hud-cursor">
                {formatRA(world[0])} {formatDec(world[1])}
                {value && <span className="muted"> · {value}</span>}
              </span>
            ) : (
              p.targetWorld && <span className="muted">{formatRA(p.targetWorld[0])} {formatDec(p.targetWorld[1])}</span>
            )}
          </div>
        </div>
      )}

      {sel && selPos && (
        <div className="pick glass" style={{ left: Math.min(selPos[0] + 16, W - 260), top: Math.max(selPos[1] - 20, 70) }} onPointerDown={(e) => e.stopPropagation()}>
          <button className="pick-close" onClick={() => setSel(null)} aria-label="Close">
            <X size={14} />
          </button>
          {sel.kind === "target" ? (
            <>
              <div className="micro">Target</div>
              <div className="pick-title">{p.targetName}</div>
              {p.targetWorld && <div className="mono small">{formatRA(p.targetWorld[0])} {formatDec(p.targetWorld[1])}</div>}
              <button className="btn pick-cta" onClick={() => { setSel(null); p.onOpenDetails(); }}>
                Spectrum &amp; light curve
              </button>
            </>
          ) : (
            <>
              <div className="micro">{CATALOG_STYLE[sel.cat].label}</div>
              <div className="pick-title mono">{sel.src.id}</div>
              <div className="mono small">
                {sel.src.mag_band} {sel.src.mag != null ? sel.src.mag.toFixed(2) : "—"}
                {totalPm(sel.src) > 0 && ` · μ ${totalPm(sel.src).toFixed(0)} mas/yr`}
              </div>
              <div className="muted small">Shown for {p.year.toFixed(2)}; catalogue epoch {sel.src.epoch.toFixed(1)}</div>
              {p.onMeasure && (
                <button
                  className="btn pick-cta"
                  disabled={measure.state === "busy"}
                  onClick={() => {
                    const src = sel.src;
                    setMeasure({ state: "busy" });
                    p.onMeasure!(sel.cat, src)
                      .then(() => {
                        setMeasure({ state: "idle" });
                        setSel(null);
                      })
                      .catch((e) => setMeasure({ state: "error", message: String(e?.message || e) }));
                  }}
                >
                  {measure.state === "busy" ? "Measuring every exposure…" : "Measure spectrum & light curve"}
                </button>
              )}
              {measure.state === "error" && <div className="small" style={{ color: "var(--err)" }}>{measure.message}</div>}
            </>
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
        </div>
      )}
      {d?.kind === "empty" && !p.progress && n > 0 && <div className="sky-note glass">{d.message}</div>}
      {p.error && !p.progress && (
        <div className="sky-note glass error" role="alert" onPointerDown={(e) => e.stopPropagation()}>
          <span>{p.error}</span>
          <button className="btn" onClick={p.onRetry}>
            <RotateCcw size={14} /> Try again
          </button>
        </div>
      )}
    </div>
  );
}
