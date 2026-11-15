/** Collapsible drawer (closed by default): Spectrum · Light curve · Periodogram · Data. Its code loads only
 *  when opened; the per-exposure tables it needs are fetched by the app when it opens (tablesReady). */
import { X } from "lucide-react";
import { lazy, Suspense } from "react";
import type { BandSel } from "../render";
import type { Mode } from "../state";
import type { Meta, Photometry, Target } from "../types";
import type { VerifySubject } from "./chart/VerifyPanel";
import { Segmented } from "./ui/primitives";

const SpectralPanel = lazy(() => import("./chart/SpectralPanel").then((m) => ({ default: m.SpectralPanel })));
const LightCurveChart = lazy(() => import("./chart/LightCurveChart").then((m) => ({ default: m.LightCurveChart })));
const PeriodogramChart = lazy(() => import("./chart/PeriodogramChart").then((m) => ({ default: m.PeriodogramChart })));
const VerifyPanel = lazy(() => import("./chart/VerifyPanel").then((m) => ({ default: m.VerifyPanel })));
const SidePanel = lazy(() => import("./SidePanel").then((m) => ({ default: m.SidePanel })));

export type DrawerTab = "verify" | "spectrum" | "lightcurve" | "periodogram" | "details";

interface Props {
  tab: DrawerTab;
  onTab: (t: DrawerTab) => void;
  onClose: () => void;
  meta: Meta;
  target: Target | null;
  epoch: number;
  band: BandSel;
  mode: Mode;
  ylog: boolean;
  onYlog: (v: boolean) => void;
  flagBits: number[];
  onFlagBits: (bits: number[]) => void;
  flagsOn: boolean;
  onFlagsOn: (on: boolean) => void;
  tablesReady: boolean;
  tablesError?: string | null;
  /** A measured catalogue source shown instead of the target. */
  source?: { key: string; label: string; photometry: Photometry[] } | null;
  onClearSource?: () => void;
  /** The spectrum tab fell back to the per-exposure chart and needs the photometry table. */
  onNeedPhotometry?: () => void;
  /** Per-exposure verification of a position (Verify tab). The analysis of a source opens in its own window. */
  verify?: VerifySubject | null;
}

export function Drawer(p: Props) {
  return (
    <section className="drawer glass" aria-label="Details">
      <header className="drawer-head">
        <Segmented<DrawerTab>
          label="Drawer"
          size="sm"
          value={p.tab}
          onChange={p.onTab}
          options={[
            ...(p.verify ? [{ value: "verify" as const, label: "Verify" }] : []),
            { value: "spectrum", label: "Spectrum" },
            { value: "lightcurve", label: "Light curve" },
            { value: "periodogram", label: "Period" },
            { value: "details", label: "Data" },
          ]}
        />
        {p.tab === "spectrum" && (
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
        {p.tab === "verify" && p.verify ? (
          <span className="drawer-title source-chip">
            <span className="mono">{p.verify.label}</span>
          </span>
        ) : p.source ? (
          <span className="drawer-title source-chip">
            <span className="mono">{p.source.label}</span>
            <button className="btn ghost icon xs" onClick={p.onClearSource} aria-label="Back to the target" title="Back to the target">
              <X size={12} />
            </button>
          </span>
        ) : (
          <span className="drawer-title">{p.target?.name || p.meta.target.name}</span>
        )}
        <button className="btn ghost icon" onClick={p.onClose} aria-label="Close drawer" title="Close (Esc)">
          <X size={16} />
        </button>
      </header>
      <div className="drawer-body">
        <Suspense fallback={<div className="skeleton" style={{ height: 260 }} />}>
          {p.tab === "verify" && p.verify && <VerifyPanel seqId={p.meta.id} subject={p.verify} />}
          {p.tab === "periodogram" && <PeriodogramChart id={p.meta.id} source={p.source} />}
          {p.tab === "spectrum" && (
            <SpectralPanel meta={p.meta} epoch={p.epoch} ylog={p.ylog} target={p.target} source={p.source} photometryReady={p.tablesReady} onNeedPhotometry={p.onNeedPhotometry} />
          )}
          {(p.tab === "lightcurve" || p.tab === "details") && !p.tablesReady && (p.tablesError
            ? <p className="chart-empty static">{p.tablesError}</p>
            : <div className="skeleton" style={{ height: 260 }} aria-label="Loading measurements" />)}
          {p.tablesReady && p.tab === "lightcurve" && <LightCurveChart meta={p.meta} epoch={p.epoch} />}
          {p.tablesReady && p.tab === "details" && (
            <SidePanel
              meta={p.meta}
              target={p.target}
              epoch={p.epoch}
              band={p.band}
              mode={p.mode}
              flagBits={p.flagBits}
              onFlagBits={p.onFlagBits}
              flagsOn={p.flagsOn}
              onFlagsOn={p.onFlagsOn}
            />
          )}
        </Suspense>
      </div>
    </section>
  );
}
