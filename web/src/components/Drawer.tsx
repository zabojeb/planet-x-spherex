/** Collapsible drawer (closed by default): Spectrum · Light curve · Details. Its code loads only when opened. */
import { X } from "lucide-react";
import { lazy, Suspense } from "react";
import type { BandSel } from "../render";
import type { Mode } from "../state";
import type { Meta, Target } from "../types";
import { Segmented } from "./ui/primitives";

const SpectrumChart = lazy(() => import("./chart/SpectrumChart").then((m) => ({ default: m.SpectrumChart })));
const LightCurveChart = lazy(() => import("./chart/LightCurveChart").then((m) => ({ default: m.LightCurveChart })));
const SidePanel = lazy(() => import("./SidePanel").then((m) => ({ default: m.SidePanel })));

export type DrawerTab = "spectrum" | "lightcurve" | "details";

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
            { value: "spectrum", label: "Spectrum" },
            { value: "lightcurve", label: "Light curve" },
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
        <span className="drawer-title">{p.target?.name || p.meta.target.name}</span>
        <button className="btn ghost icon" onClick={p.onClose} aria-label="Close drawer" title="Close (Esc)">
          <X size={16} />
        </button>
      </header>
      <div className="drawer-body">
        <Suspense fallback={<div className="skeleton" style={{ height: 260 }} />}>
          {p.tab === "spectrum" && <SpectrumChart meta={p.meta} epoch={p.epoch} ylog={p.ylog} />}
          {p.tab === "lightcurve" && <LightCurveChart meta={p.meta} epoch={p.epoch} />}
          {p.tab === "details" && (
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
