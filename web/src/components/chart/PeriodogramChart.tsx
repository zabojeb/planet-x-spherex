/** Periodogram tab: Lomb–Scargle power against period (log axis) and the light curve folded on the best
 *  period with the model. The server computes it; this file only fetches through api.ts and draws. */
import { useEffect, useMemo, useState } from "react";
import { ApiError, fetchPeriodogram } from "../../api";
import { SERIES } from "../../colors";
import type { PeriodogramResult, Photometry } from "../../types";
import { ChartFrame, formatNumber, type ChartPoint, type Tick } from "./ChartFrame";

interface Props {
  id: string;
  /** Measured catalogue source instead of the target. */
  source?: { key: string; photometry: Photometry[] } | null;
}

const MAX_FOLDED = 3000;

function logPeriodTicks(a: number, b: number): Tick[] {
  const out: Tick[] = [];
  for (let p = Math.floor(Math.log10(a)); p <= Math.ceil(Math.log10(b)); p++)
    for (const s of [1, 2, 5]) {
      const v = s * 10 ** p;
      if (v >= a && v <= b) out.push({ value: Math.log10(v), label: formatNumber(v) });
    }
  return out;
}

const fap = (v: number | null) => (v == null ? null : v < 1e-4 ? v.toExponential(0) : v.toPrecision(2));

export function PeriodogramChart({ id, source }: Props) {
  const [state, setState] = useState<{ key: string; res?: PeriodogramResult; error?: string }>({ key: "" });
  const key = `${id}|${source?.key ?? "target"}`;
  useEffect(() => {
    let live = true;
    setState({ key });
    fetchPeriodogram(id, source ?? undefined)
      .then((res) => live && setState({ key, res }))
      .catch((e) => {
        if (!live) return;
        const msg = e instanceof ApiError && e.status === 422 ? "Too few usable measurements for a period search." : e instanceof ApiError && (e.status === 404 || e.status === 405) ? "Period search is not available on this server yet." : String(e?.message || e);
        setState({ key, error: msg });
      });
    return () => {
      live = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const res = state.key === key ? state.res : undefined;
  const error = state.key === key ? state.error : undefined;

  const power = useMemo(() => {
    if (!res) return null;
    const { period_days: P, power: Y } = res.periodogram;
    const pts: [number, number][] = P.map((p, i) => [Math.log10(p), Y[i]]).filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y)) as [number, number][];
    const x0 = Math.log10(res.min_period_days);
    const x1 = Math.log10(res.max_period_days);
    const yMax = Math.max(0.05, ...pts.map((q) => q[1])) * 1.1;
    const peaks: ChartPoint[] = res.peaks.slice(0, 5).map((pk, i) => ({
      key: `pk${i}`,
      x: Math.log10(pk.period_days),
      y: pk.power,
      color: SERIES.orange,
      emphasized: i === 0,
      tooltip: [`${i === 0 ? "Best period" : `Peak ${i + 1}`} · ${pk.period_days.toFixed(2)} d`, `power ${pk.power.toFixed(3)}`],
    }));
    return { pts, peaks, x: [x0, x1] as [number, number], y: [0, yMax] as [number, number], ticks: logPeriodTicks(res.min_period_days, res.max_period_days) };
  }, [res]);

  const folded = useMemo(() => {
    if (!res) return null;
    const step = Math.max(1, Math.ceil(res.folded.length / MAX_FOLDED));
    const pts: ChartPoint[] = [];
    const ys: number[] = [];
    res.folded.forEach((f, i) => {
      if (i % step || !Number.isFinite(f.rel)) return;
      ys.push(f.rel);
      pts.push({
        key: `f${f.index}`,
        x: f.phase,
        y: f.rel,
        yLo: f.rel_err != null ? f.rel - f.rel_err : null,
        yHi: f.rel_err != null ? f.rel + f.rel_err : null,
        color: SERIES.blue,
        hollow: f.flagged,
        tooltip: [`phase ${f.phase.toFixed(2)} · ${f.band}, ${f.wl.toFixed(3)} µm`, `${f.rel.toFixed(3)} × median`],
      });
    });
    ys.sort((a, b) => a - b);
    const q = (t: number) => ys[Math.min(ys.length - 1, Math.max(0, Math.round(t * (ys.length - 1))))] ?? 1;
    let lo = Math.min(q(0.01), 0.9);
    let hi = Math.max(q(0.99), 1.1);
    const pad = (hi - lo) * 0.1;
    lo -= pad;
    hi += pad;
    const rel = res.model.rel;
    const line = rel ? [{ key: "model", color: SERIES.orange, points: res.model.phase.map((ph, i) => [ph, rel[i]] as [number, number]) }] : [];
    return { pts, line, y: [lo, hi] as [number, number] };
  }, [res]);

  if (error) return <p className="chart-empty static">{error}</p>;
  if (!res || !power || !folded) return <div className="skeleton" style={{ height: 260 }} aria-label="Computing periodogram" />;
  const f = fap(res.best.false_alarm_probability);
  return (
    <div className="periodogram">
      <p className="period-best">
        <span className="micro">Best period</span> <span className="num">{res.best.period_days.toFixed(2)} d</span>
        <span className="muted small num">
          {" "}power {res.best.power.toFixed(2)}
          {f && ` · false-alarm ${f}`} · {res.n_points} points over {res.baseline_days.toFixed(0)} d
        </span>
      </p>
      <ChartFrame
        xDomain={power.x}
        yDomain={power.y}
        xLabel="Period (days, log scale)"
        yLabel="Power"
        xTicks={power.ticks}
        yFormat={(v) => v.toFixed(2)}
        points={power.peaks}
        lines={[{ key: "power", color: SERIES.blue, points: power.pts }]}
        legend={[
          { label: "Lomb–Scargle power", color: SERIES.blue, kind: "line" },
          { label: "peaks", color: SERIES.orange },
        ]}
        height={200}
      />
      <ChartFrame
        xDomain={[0, 1]}
        yDomain={folded.y}
        xLabel={`Phase at ${res.best.period_days.toFixed(2)} d`}
        yLabel="Relative flux"
        yFormat={(v) => v.toFixed(2)}
        points={folded.pts}
        lines={folded.line}
        legend={[
          { label: "exposures", color: SERIES.blue },
          ...(folded.line.length ? [{ label: "model", color: SERIES.orange, kind: "line" as const }] : []),
          { label: "flagged", color: "var(--text-secondary)", kind: "hollow" as const },
        ]}
        note={res.folded.length > MAX_FOLDED ? `Showing every ${Math.ceil(res.folded.length / MAX_FOLDED)}th of ${res.folded.length} points.` : null}
        height={240}
        background={(s) => <line className="ref-line" x1={s.left} x2={s.right} y1={s.y(1)} y2={s.y(1)} />}
      />
    </div>
  );
}
