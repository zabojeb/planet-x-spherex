import { useMemo } from "react";
import { formatDate, mjdToDate } from "../../astro";
import { SERIES } from "../../colors";
import { measurementLines, qualityNote, qualityShares } from "../../photometry";
import type { Meta } from "../../types";
import { ChartFrame, type ChartLine, type ChartPoint, type Tick } from "./ChartFrame";

interface Props {
  meta: Meta;
  epoch: number;
}

const N_BINS = 102;

/** Relative brightness: each exposure divided by the median flux of its wavelength bin over all epochs.
 *  Calibrated and relative-only (ring) measurements are normalised separately, so they never mix. */
function relativeFluxes(meta: Meta) {
  const usable = meta.photometry.filter((p) => p.flux_mjy != null && Number.isFinite(p.flux_mjy));
  if (!usable.length) return [];
  const wls = usable.map((p) => p.wl);
  const lo = Math.log(Math.min(...wls) * 0.999);
  const hi = Math.log(Math.max(...wls) * 1.001);
  const bin = (p: { wl: number; relative?: boolean }) =>
    Math.min(N_BINS - 1, Math.floor(((Math.log(p.wl) - lo) / (hi - lo)) * N_BINS)) + (p.relative ? N_BINS : 0);
  const clean: number[][] = Array.from({ length: 2 * N_BINS }, () => []);
  const any: number[][] = Array.from({ length: 2 * N_BINS }, () => []);
  for (const p of usable) {
    any[bin(p)].push(p.flux_mjy as number);
    if (!p.flagged) clean[bin(p)].push(p.flux_mjy as number);
  }
  const median = (a: number[]) => {
    if (!a.length) return NaN;
    const s = [...a].sort((x, y) => x - y);
    return s[Math.floor(s.length / 2)];
  };
  const ref = clean.map((c, i) => (c.length ? median(c) : median(any[i])));
  return usable
    .map((p) => {
      const r = ref[bin(p)];
      return { p, rel: r > 0 ? (p.flux_mjy as number) / r : NaN, relErr: r > 0 && p.err_mjy ? p.err_mjy / r : null };
    })
    .filter((x) => Number.isFinite(x.rel));
}

function monthTicks(a: number, b: number, width: number): Tick[] {
  const start = mjdToDate(a);
  const months = (b - a) / 30.44;
  const step = [1, 2, 3, 4, 6, 12].find((s) => months / s <= Math.max(2, Math.floor(width / 80))) ?? 12;
  const ticks: Tick[] = [];
  const d = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1));
  while (d.getUTCMonth() % step) d.setUTCMonth(d.getUTCMonth() + 1);
  for (let i = 0; i < 60; i++) {
    const mjd = d.getTime() / 86400000 - Date.UTC(1858, 10, 17) / 86400000;
    if (mjd > b) break;
    ticks.push({ value: mjd, label: d.toLocaleDateString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" }) });
    d.setUTCMonth(d.getUTCMonth() + step);
  }
  return ticks;
}

export function LightCurveChart({ meta, epoch }: Props) {
  const data = useMemo(() => relativeFluxes(meta), [meta]);
  const { points, lines, yDomain, xDomain, medians } = useMemo(() => {
    const pts: ChartPoint[] = data.map(({ p, rel, relErr }) => ({
      key: p.index,
      x: p.mjd,
      y: rel,
      yLo: relErr != null ? rel - relErr : null,
      yHi: relErr != null ? rel + relErr : null,
      color: SERIES.blue,
      hollow: p.flagged || p.saturated,
      shape: p.saturated ? "diamond" : "circle",
      dim: p.epoch !== epoch,
      tooltip: [
        `${formatDate(p.mjd, true)} · epoch ${p.epoch + 1}`,
        `${rel.toFixed(3)} × median (${p.band}, ${p.wl.toFixed(3)} µm)`,
        ...measurementLines(p),
      ],
    }));
    const med: [number, number][] = [];
    for (const e of meta.epochs) {
      const vals = data.filter((d) => d.p.epoch === e.index && !d.p.flagged).map((d) => d.rel);
      const use = vals.length ? vals : data.filter((d) => d.p.epoch === e.index).map((d) => d.rel);
      if (!use.length) continue;
      use.sort((a, b) => a - b);
      med.push([e.mjd_mean, use[Math.floor(use.length / 2)]]);
    }
    const medPts: ChartPoint[] = med.map(([x, y], i) => ({
      key: `m${i}`,
      x,
      y,
      color: SERIES.orange,
      emphasized: true,
      tooltip: [`Epoch median · ${formatDate(x)}`, `${y.toFixed(3)} × median spectrum`],
    }));
    // Robust y range from the clean points when there are enough of them (flagged points can be wild).
    const clean = data.filter((d) => !d.p.flagged && !d.p.saturated).map((d) => d.rel);
    const ys = (clean.length >= 10 ? clean : data.map((d) => d.rel)).sort((a, b) => a - b);
    const q = (f: number) => ys[Math.min(ys.length - 1, Math.max(0, Math.round(f * (ys.length - 1))))];
    const tail = clean.length >= 10 ? 0.01 : 0.05;
    let lo = ys.length ? Math.min(q(tail), 0.9) : 0.5;
    let hi = ys.length ? Math.max(q(1 - tail), 1.1) : 1.5;
    const pad = (hi - lo) * 0.1;
    lo -= pad;
    hi += pad;
    const t0 = Math.min(...meta.epochs.map((e) => e.mjd_min));
    const t1 = Math.max(...meta.epochs.map((e) => e.mjd_max));
    const span = Math.max(t1 - t0, 10);
    return {
      points: [...pts, ...medPts],
      lines: med.length > 1 ? ([{ key: "median", points: med, color: SERIES.orange }] as ChartLine[]) : [],
      yDomain: [lo, hi] as [number, number],
      xDomain: [t0 - span * 0.03, t1 + span * 0.03] as [number, number],
      medians: med.length,
    };
  }, [data, meta, epoch]);

  const quality = useMemo(() => qualityShares(meta.photometry), [meta]);
  const note = qualityNote(quality, "points");

  return (
    <ChartFrame
      subtitle={`${quality.saturated > 0.5 ? "Saturated source. " : ""}Each exposure divided by the source's median spectrum at its wavelength.`}
      xDomain={xDomain}
      yDomain={yDomain}
      xLabel="Date (UTC)"
      yLabel="Relative flux"
      xTicks={monthTicks(xDomain[0], xDomain[1], 600)}
      yFormat={(v) => v.toFixed(2)}
      points={points}
      lines={lines}
      legend={[
        { label: "exposures", color: SERIES.blue },
        ...(medians ? [{ label: "epoch median", color: SERIES.orange, kind: "line" as const }] : []),
        { label: "flagged", color: "var(--text-secondary)", kind: "hollow" },
        ...(quality.saturated > 0 ? [{ label: "saturated", color: "var(--text-secondary)", kind: "hollow-square" as const }] : []),
      ]}
      note={note}
      empty={data.length ? null : "No photometry for this target"}
      background={(s) => <line className="ref-line" x1={s.left} x2={s.right} y1={s.y(1)} y2={s.y(1)} />}
    />
  );
}
