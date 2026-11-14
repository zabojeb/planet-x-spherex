/**
 * Reusable SVG chart: one y-axis, recessive grid, thin marks (r = 4 px dots, 2 px lines, 1 px error bars),
 * hollow marks for flagged values, a 2 px surface ring on emphasised marks, and a hover tooltip on the
 * nearest point. Legend items are rendered as HTML above the plot (identity never relies on colour alone).
 */
import { scaleLinear, scaleLog } from "d3-scale";
import { useId, useMemo, useState, type ReactNode } from "react";
import { useElementWidth, useMediaQuery } from "../../hooks";

export interface ChartPoint {
  key: string | number;
  x: number;
  y: number;
  yLo?: number | null;
  yHi?: number | null;
  color: string;
  hollow?: boolean;
  /** "diamond" marks a different measurement class (e.g. saturated) so it never relies on colour. */
  shape?: "circle" | "diamond";
  emphasized?: boolean;
  dim?: boolean;
  tooltip: string[];
}

export interface ChartLine {
  key: string;
  points: [number, number][];
  color: string;
}

export interface LegendItem {
  label: string;
  color: string;
  kind?: "dot" | "line" | "hollow" | "hollow-square";
}

export interface Tick {
  value: number;
  label: string;
}

export interface Scales {
  x: (v: number) => number;
  y: (v: number) => number;
  left: number;
  right: number;
  top: number;
  bottom: number;
}

interface Props {
  title?: string;
  subtitle?: string;
  xDomain: [number, number];
  yDomain: [number, number];
  yLog?: boolean;
  xLabel: string;
  yLabel: string;
  xTicks?: Tick[];
  yFormat?: (v: number) => string;
  points: ChartPoint[];
  lines?: ChartLine[];
  legend?: LegendItem[];
  note?: string | null;
  headerExtra?: ReactNode;
  background?: (s: Scales) => ReactNode;
  height?: number;
  empty?: string | null;
}

const M = { l: 58, r: 14, t: 40, b: 38 };

function logTicks([a, b]: [number, number]): number[] {
  const out: number[] = [];
  const lo = Math.floor(Math.log10(a));
  const hi = Math.ceil(Math.log10(b));
  const steps = hi - lo <= 2 ? [1, 2, 5] : [1];
  for (let p = lo; p <= hi; p++) for (const s of steps) {
    const v = s * 10 ** p;
    if (v >= a && v <= b) out.push(v);
  }
  return out;
}

export const formatNumber = (v: number) => {
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (a >= 10) return v.toFixed(0);
  if (a >= 1) return v.toFixed(a < 2 ? 2 : 1).replace(/\.?0+$/, "");
  return v.toPrecision(2);
};

const MIN_PHONE_WIDTH = 600;

export function ChartFrame(p: Props) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const [hover, setHover] = useState<ChartPoint | null>(null);
  const clipId = `clip${useId().replace(/:/g, "")}`;
  const height = p.height ?? 280;
  // phones get a wider plot than the screen, scrolled sideways in .chart-body, so bands and epochs stay readable
  const narrow = useMediaQuery("(max-width: 760px)");
  const w = Math.max(width, narrow ? MIN_PHONE_WIDTH : 280);

  const s = useMemo(() => {
    const x = scaleLinear().domain(p.xDomain).range([M.l, w - M.r]);
    const y = p.yLog
      ? scaleLog().domain(p.yDomain).range([height - M.b, M.t]).clamp(true)
      : scaleLinear().domain(p.yDomain).range([height - M.b, M.t]);
    return { x, y };
  }, [p.xDomain, p.yDomain, p.yLog, w, height]);

  const scales: Scales = { x: s.x, y: s.y, left: M.l, right: w - M.r, top: M.t, bottom: height - M.b };
  const xTicks = p.xTicks ?? s.x.ticks(Math.max(3, Math.floor(w / 90))).map((v) => ({ value: v, label: formatNumber(v) }));
  const yTickValues = p.yLog ? logTicks(p.yDomain) : (s.y as ReturnType<typeof scaleLinear<number>>).ticks(5);
  const yFmt = p.yFormat ?? formatNumber;

  const ordered = useMemo(() => {
    const dim = p.points.filter((q) => q.dim);
    const rest = p.points.filter((q) => !q.dim && !q.emphasized);
    const top = p.points.filter((q) => q.emphasized);
    return [...dim, ...rest, ...top];
  }, [p.points]);

  function onMove(e: React.MouseEvent<SVGRectElement>) {
    // client -> SVG user coordinates (the phone layout shifts the viewBox by the axis width)
    const svg = e.currentTarget.ownerSVGElement as SVGSVGElement;
    const ctm = svg.getScreenCTM();
    if (!ctm) return;
    const pt = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
    const mx = pt.x;
    const my = pt.y;
    let best: ChartPoint | null = null;
    let bd = 14 * 14;
    for (const q of p.points) {
      const dx = s.x(q.x) - mx;
      const dy = s.y(q.y) - my;
      const d = dx * dx + dy * dy;
      if (d < bd) {
        bd = d;
        best = q;
      }
    }
    setHover(best);
  }

  // phones: the y axis stays put in its own SVG and only the plot scrolls; the plot SVG shows x >= M.l
  const split = narrow;
  const shift = split ? M.l : 0;
  const tipLeft = hover ? Math.min(s.x(hover.x) + 12, w - 210) - shift : 0;
  const tipTop = hover ? Math.max(s.y(hover.y) - 12, 4) : 0;

  const yTickLabel = (v: number) => (
    <text key={`yl${v}`} className="tick" x={M.l - 6} y={s.y(v)} dy="0.32em" textAnchor="end">
      {yFmt(v)}
    </text>
  );
  const yAxisLabel = (
    <text className="axis-label" transform={`translate(13 ${(M.t + height - M.b) / 2}) rotate(-90)`} textAnchor="middle">
      {p.yLabel}
    </text>
  );

  return (
    <div className="chart" ref={ref}>
      {(p.title || p.subtitle || p.headerExtra) && (
        <div className="chart-head">
          <div>
            {p.title && <h3>{p.title}</h3>}
            {p.subtitle && <p className="chart-sub">{p.subtitle}</p>}
          </div>
          {p.headerExtra}
        </div>
      )}
      {p.legend && p.legend.length > 0 && (
        <ul className="legend">
          {p.legend.map((l) => (
            <li key={l.label}>
              <span className={`swatch ${l.kind ?? "dot"}`} style={{ color: l.color }} />
              {l.label}
            </li>
          ))}
        </ul>
      )}
      <div className={`chart-body${split ? " split" : ""}`}>
        {/* phones: the empty-state message is a line above the plot instead of an overlay over axis labels */}
        {split && p.empty && <p className="chart-empty inline">{p.empty}</p>}
        <div className="chart-row">
        {split && (
          <svg className="chart-axis" width={M.l} height={height} aria-hidden>
            {yTickValues.map(yTickLabel)}
            {yAxisLabel}
          </svg>
        )}
        <div className="chart-scroll">
        <svg
          width={w - shift}
          height={height}
          viewBox={`${shift} 0 ${w - shift} ${height}`}
          role="img"
          aria-label={p.title ?? p.subtitle ?? "chart"}
        >
          {p.background?.(scales)}
          {yTickValues.map((v) => (
            <g key={`y${v}`}>
              <line className="grid" x1={M.l} x2={w - M.r} y1={s.y(v)} y2={s.y(v)} />
              {!split && yTickLabel(v)}
            </g>
          ))}
          {xTicks.map((t) => (
            <text key={`x${t.value}`} className="tick" x={s.x(t.value)} y={height - M.b + 15} textAnchor="middle">
              {t.label}
            </text>
          ))}
          <line className="axis" x1={M.l} x2={w - M.r} y1={height - M.b} y2={height - M.b} />
          <text className="axis-label" x={(M.l + w - M.r) / 2} y={height - 4} textAnchor="middle">
            {p.xLabel}
          </text>
          {!split && yAxisLabel}
          <clipPath id={clipId}>
            <rect x={M.l - 6} y={M.t - 6} width={Math.max(0, w - M.l - M.r + 12)} height={Math.max(0, height - M.t - M.b + 12)} />
          </clipPath>
          <g clipPath={`url(#${clipId})`}>
          {(p.lines ?? []).map((l) => (
            <path
              key={l.key}
              d={l.points.map(([x, y], i) => `${i ? "L" : "M"}${s.x(x).toFixed(1)},${s.y(y).toFixed(1)}`).join("")}
              fill="none"
              stroke={l.color}
              strokeWidth={2}
              strokeLinejoin="round"
            />
          ))}
          {ordered.map((q) => {
            const cx = s.x(q.x);
            const cy = s.y(q.y);
            const r = q.emphasized ? 5 : 4;
            const op = q.dim ? 0.45 : 1;
            return (
              <g key={q.key} opacity={op}>
                {q.yLo != null && q.yHi != null && (
                  <line x1={cx} x2={cx} y1={s.y(q.yLo)} y2={s.y(q.yHi)} stroke={q.color} strokeWidth={1} />
                )}
                {q.emphasized && !q.hollow && <circle cx={cx} cy={cy} r={r + 2} fill="var(--surface-1)" />}
                {q.shape === "diamond" ? (
                  <path
                    d={`M${cx},${cy - r - 1} L${cx + r + 1},${cy} L${cx},${cy + r + 1} L${cx - r - 1},${cy} Z`}
                    fill={q.hollow ? "var(--surface-1)" : q.color}
                    stroke={q.color}
                    strokeWidth={1.5}
                  />
                ) : (
                  <circle
                    cx={cx}
                    cy={cy}
                    r={r}
                    fill={q.hollow ? "var(--surface-1)" : q.color}
                    stroke={q.color}
                    strokeWidth={q.hollow ? 1.5 : 0}
                  />
                )}
              </g>
            );
          })}
          </g>
          {hover && <circle className="hover-ring" cx={s.x(hover.x)} cy={s.y(hover.y)} r={8} />}
          <rect
            x={M.l}
            y={M.t}
            width={Math.max(0, w - M.l - M.r)}
            height={Math.max(0, height - M.t - M.b)}
            fill="transparent"
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
          />
        </svg>
        {hover && (
          <div className="tooltip" style={{ left: tipLeft, top: tipTop }}>
            {hover.tooltip.map((line, i) => (
              <div key={i} className={i === 0 ? "tt-title" : undefined}>
                {line}
              </div>
            ))}
          </div>
        )}
        </div>
        </div>
        {p.note && <p className="chart-note">{p.note}</p>}
        {!split && p.empty && <p className="chart-empty">{p.empty}</p>}
      </div>
    </div>
  );
}
