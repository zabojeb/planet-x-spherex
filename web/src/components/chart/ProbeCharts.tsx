/** Two tiny charts for the instant probe: a coarse spectrum (flux per detector, one epoch) and a light curve
 *  (flux per epoch, one detector). One series each, so no legend; thin marks, error bars, the shown epoch /
 *  detector emphasised, native tooltips per point. */
import { SERIES } from "../../colors";
import type { ProbePoint } from "../../probe";

const W = 124;
const H = 64;
const PAD = { l: 4, r: 4, t: 6, b: 14 };

function scale(lo: number, hi: number, a: number, b: number) {
  const d = hi - lo || 1;
  return (v: number) => a + ((v - lo) / d) * (b - a);
}

function yDomain(pts: ProbePoint[]): [number, number] {
  const lo = Math.min(0, ...pts.map((p) => p.flux - (Number.isFinite(p.err) ? p.err : 0)));
  const hi = Math.max(...pts.map((p) => p.flux + (Number.isFinite(p.err) ? p.err : 0)), lo + 1e-6);
  return [lo, hi + (hi - lo) * 0.08];
}

const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toPrecision(2));

function Mini(p: {
  title: string;
  pts: ProbePoint[];
  x: (pt: ProbePoint) => number;
  xLo: number;
  xHi: number;
  xLabels: [string, string];
  active: (pt: ProbePoint) => boolean;
  tip: (pt: ProbePoint) => string;
}) {
  const [ylo, yhi] = yDomain(p.pts);
  const sx = scale(p.xLo, p.xHi, PAD.l + 4, W - PAD.r - 4);
  const sy = scale(ylo, yhi, H - PAD.b, PAD.t);
  return (
    <figure className="probe-mini">
      <figcaption className="micro">{p.title}</figcaption>
      <svg width={W} height={H} role="img" aria-label={p.title}>
        <line x1={PAD.l} x2={W - PAD.r} y1={sy(0)} y2={sy(0)} className="probe-zero" />
        {p.pts.map((pt, i) => {
          const cx = sx(p.x(pt));
          const cy = sy(pt.flux);
          const e = Number.isFinite(pt.err) ? pt.err : 0;
          const on = p.active(pt);
          return (
            <g key={i}>
              {e > 0 && <line x1={cx} x2={cx} y1={sy(pt.flux - e)} y2={sy(pt.flux + e)} stroke={SERIES.blue} strokeWidth={1.5} opacity={0.7} />}
              <circle cx={cx} cy={cy} r={on ? 4.5 : 3.5} fill={on ? SERIES.blue : "var(--bg)"} stroke={SERIES.blue} strokeWidth={2}>
                <title>{p.tip(pt)}</title>
              </circle>
            </g>
          );
        })}
        <text x={PAD.l} y={H - 2} className="probe-axis">{p.xLabels[0]}</text>
        <text x={W - PAD.r} y={H - 2} textAnchor="end" className="probe-axis">{p.xLabels[1]}</text>
        <text x={PAD.l} y={PAD.t + 4} className="probe-axis">{fmt(yhi)}</text>
      </svg>
    </figure>
  );
}

export function ProbeCharts({ spectrum, lightCurve, epoch, band }: { spectrum: ProbePoint[]; lightCurve: ProbePoint[]; epoch: number; band: string | null }) {
  const tip = (pt: ProbePoint) =>
    `${pt.band} ${pt.wlLo.toFixed(2)}–${pt.wlHi.toFixed(2)} µm · ${pt.date}\n${fmt(pt.flux)} ± ${Number.isFinite(pt.err) ? fmt(pt.err) : "?"} mJy · S/N ${Number.isFinite(pt.snr) ? pt.snr.toFixed(1) : "?"}`;
  return (
    <div className="probe-charts">
      {spectrum.length >= 2 && (
        <Mini
          title="Spectrum · this visit"
          pts={spectrum}
          x={(pt) => pt.wl}
          xLo={0.7}
          xHi={5.05}
          xLabels={["0.75 µm", "5 µm"]}
          active={(pt) => pt.band === band}
          tip={tip}
        />
      )}
      {lightCurve.length >= 2 && (
        <Mini
          title={`Light curve · ${band ?? ""}`}
          pts={lightCurve}
          x={(pt) => pt.mjd}
          xLo={Math.min(...lightCurve.map((p) => p.mjd)) - 15}
          xHi={Math.max(...lightCurve.map((p) => p.mjd)) + 15}
          xLabels={[lightCurve[0].date, lightCurve[lightCurve.length - 1].date]}
          active={(pt) => pt.epoch === epoch}
          tip={tip}
        />
      )}
    </div>
  );
}
