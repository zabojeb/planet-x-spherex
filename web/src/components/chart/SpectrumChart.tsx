import { useMemo } from "react";
import { formatDate } from "../../astro";
import { epochColor } from "../../colors";
import { absolute, measurementLines, qualityNote, qualityShares } from "../../photometry";
import type { Meta } from "../../types";
import { ChartFrame, formatNumber, type ChartPoint, type LegendItem } from "./ChartFrame";

/** Molecular bands that shape brown-dwarf and stellar near-infrared spectra (microns). */
const FEATURES: { wl: number; label: string }[] = [
  { wl: 1.4, label: "H₂O" },
  { wl: 1.65, label: "CH₄" },
  { wl: 1.9, label: "H₂O" },
  { wl: 2.2, label: "CH₄" },
  { wl: 2.3, label: "CO" },
  { wl: 2.7, label: "H₂O" },
  { wl: 3.3, label: "CH₄" },
  { wl: 4.26, label: "CO₂" },
  { wl: 4.6, label: "CO" },
];

interface Props {
  meta: Meta;
  epoch: number;
  ylog: boolean;
}

export function SpectrumChart({ meta, epoch, ylog }: Props) {
  const nE = meta.epochs.length;
  const { points, yDomain } = useMemo(() => {
    const pts: ChartPoint[] = [];
    const ys: number[] = [];
    for (const ph of absolute(meta.photometry)) {
      if (ph.flux_mjy == null || !Number.isFinite(ph.flux_mjy)) continue;
      if (ylog && ph.flux_mjy <= 0) continue;
      const err = ph.err_mjy ?? 0;
      const ep = meta.epochs[ph.epoch];
      pts.push({
        key: ph.index,
        x: ph.wl,
        y: ph.flux_mjy,
        yLo: ylog ? Math.max(ph.flux_mjy - err, ph.flux_mjy * 0.05) : ph.flux_mjy - err,
        yHi: ph.flux_mjy + err,
        color: epochColor(ph.epoch, nE),
        hollow: ph.flagged || ph.saturated,
        shape: ph.saturated ? "diamond" : "circle",
        emphasized: ph.epoch === epoch,
        dim: ph.epoch !== epoch,
        tooltip: [
          `${ph.band} · ${ph.wl.toFixed(3)} µm`,
          `${formatNumber(ph.flux_mjy)} ± ${formatNumber(err)} mJy${ph.ab_mag != null ? ` · AB ${ph.ab_mag.toFixed(2)}` : ""}`,
          `Epoch ${ph.epoch + 1}: ${ep ? formatDate(ep.mjd_mean) : ""} (${formatDate(ph.mjd, true)})`,
          ...measurementLines(ph),
        ],
      });
      ys.push(ph.flux_mjy);
    }
    ys.sort((a, b) => a - b);
    const q = (p: number) => ys[Math.min(ys.length - 1, Math.max(0, Math.round(p * (ys.length - 1))))];
    let lo = ys.length ? q(0.01) : 0.01;
    let hi = ys.length ? q(0.995) : 1;
    if (ylog) {
      lo = Math.max(lo / 1.6, hi / 1e5);
      hi *= 1.6;
    } else {
      const pad = (hi - lo) * 0.08 || 1;
      lo = Math.min(0, lo - pad);
      hi += pad;
    }
    return { points: pts, yDomain: [lo, hi] as [number, number] };
  }, [meta, epoch, ylog]);
  const quality = useMemo(() => qualityShares(absolute(meta.photometry)), [meta]);
  const nRelative = meta.photometry.length - absolute(meta.photometry).length;

  const legend: LegendItem[] = meta.epochs.map((e) => ({
    label: `${e.index + 1}: ${formatDate(e.mjd_mean).slice(0, 7)}`,
    color: epochColor(e.index, nE),
  }));
  legend.push({ label: "flagged", color: "var(--text-secondary)", kind: "hollow" });
  if (quality.saturated > 0) legend.push({ label: "saturated", color: "var(--text-secondary)", kind: "hollow-square" });

  const note =
    [
      qualityNote(quality, "measurements"),
      nRelative
        ? `${nRelative} saturated exposures have relative fluxes only (ring photometry); they are left out here and used in the light curve.`
        : null,
    ]
      .filter(Boolean)
      .join(" ") || null;

  return (
    <ChartFrame
      subtitle={`${quality.saturated > 0.5 ? "Saturated source. " : ""}Forced photometry at the target in every exposure; each frame samples one narrow band.`}
      xDomain={[0.7, 5.05]}
      yDomain={yDomain}
      yLog={ylog}
      xLabel="Wavelength (µm)"
      yLabel="Flux density (mJy)"
      xTicks={[0.75, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5].map((v) => ({ value: v, label: String(v) }))}
      points={points}
      legend={legend}
      note={note}
      empty={points.length ? null : nRelative ? "Only relative fluxes for this saturated source: see the light curve" : "No photometry for this target"}
      background={(s) => {
        // Feature labels in two rows; a label is skipped when it would collide with its neighbour.
        const lastInRow = [-1e9, -1e9];
        const labels = FEATURES.map((f, i) => {
          const x = s.x(f.wl);
          const row = i % 2;
          const show = x - lastInRow[row] > 24;
          if (show) lastInRow[row] = x;
          return { ...f, x, row, show };
        });
        return (
          <g>
            {meta.bands.map((b, i) => (
              <g key={b.id}>
                <rect
                  x={s.x(b.wl_min)}
                  y={s.top}
                  width={Math.max(0, s.x(b.wl_max) - s.x(b.wl_min))}
                  height={s.bottom - s.top}
                  fill={i % 2 ? "rgba(255,255,255,0.025)" : "rgba(255,255,255,0.05)"}
                />
                <text className="band-label" x={(s.x(b.wl_min) + s.x(b.wl_max)) / 2} y={s.top - 27} textAnchor="middle">
                  {b.id}
                </text>
              </g>
            ))}
            {labels.map((f) => (
              <g key={`${f.label}${f.wl}`}>
                <line className="feature" x1={f.x} x2={f.x} y1={s.top} y2={s.bottom} />
                {f.show && (
                  <text className="feature-label" x={f.x} y={s.top - (f.row ? 4 : 15)} textAnchor="middle">
                    {f.label}
                  </text>
                )}
              </g>
            ))}
          </g>
        );
      }}
    />
  );
}
