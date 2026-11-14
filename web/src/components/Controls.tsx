/** Always-visible toolbar (mode, detector) plus the Display and Layers panels used in popovers or the mobile sheet. */
import { Compass, Crosshair, Flag, Route, RotateCw } from "lucide-react";
import { CATALOG_STYLE } from "../colors";
import { rgbLabel, wlRange } from "../display";
import type { Cmap, Derived, Stretch } from "../render";
import { CMAP_LABEL, LUTS } from "../render";
import { MODES, VIEWS_3D, type Mode, type Overlay, type View3D, type ViewState } from "../state";
import type { Meta } from "../types";
import { MicroLabel, Segmented, Switch } from "./ui/primitives";

export type CatalogStatus = Record<string, { state: "loading" | "ready" | "error"; count?: number; message?: string }>;

type Set = (patch: Partial<ViewState>) => void;

/** CSS gradient sampled from a colour map lookup table (so previews never drift from the renderer). */
function lutGradient(lut: Uint8ClampedArray): string {
  const stops = [0, 0.25, 0.5, 0.75, 1].map((t) => {
    const i = Math.round(t * 255) * 3;
    return `rgb(${lut[i]},${lut[i + 1]},${lut[i + 2]}) ${t * 100}%`;
  });
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

export function ModeControl({ view, set, size }: { view: ViewState; set: Set; size?: "sm" | "md" }) {
  return (
    <Segmented<Mode>
      label="Display mode"
      size={size}
      value={view.mode}
      onChange={(mode) => set({ mode })}
      options={MODES.map((m) => ({ value: m.id, label: m.label, title: `${m.hint} (${m.key})` }))}
    />
  );
}

/** Floating bar shown while a 3D view is open: which view, vertical exaggeration, turntable, back to 2D. */
export function View3DBar({ view, set, spin, onSpin }: { view: ViewState; set: Set; spin: boolean; onSpin: (v: boolean) => void }) {
  const hint = VIEWS_3D.find((v) => v.id === view.view3d)?.hint;
  return (
    <div className="bar3d glass" role="toolbar" aria-label="3D view" title={hint}>
      <span className="micro bar3d-tag">3D</span>
      <Segmented<View3D>
        label="3D view"
        size="sm"
        value={view.view3d}
        onChange={(view3d) => set({ view3d })}
        options={VIEWS_3D.map((v) => ({ value: v.id, label: v.label, title: v.hint }))}
      />
      <label className="bar3d-height">
        <span className="micro">Height</span>
        <input type="range" min={0.25} max={3} step={0.05} value={view.height3d} onChange={(e) => set({ height3d: Number(e.target.value) })} aria-label="Vertical exaggeration" />
      </label>
      <button className={`btn ghost icon${spin ? " active" : ""}`} onClick={() => onSpin(!spin)} aria-pressed={spin} title="Turntable">
        <RotateCw size={14} />
      </button>
      <button className="btn ghost" onClick={() => set({ view3d: "off" })} title="Back to the flat sky (v)">
        2D
      </button>
    </div>
  );
}

export function DetectorControl({ meta, derived, view, set, showWl = true }: { meta: Meta; derived: Derived; view: ViewState; set: Set; showWl?: boolean }) {
  if (view.mode === "rgb") return <span className="detector-note">{rgbLabel(derived)}</span>;
  const active = meta.bands.find((b) => b.id === view.band);
  return (
    <div className="detector">
      <Segmented<string>
        label="Detector"
        value={view.band}
        onChange={(band) => set({ band })}
        options={[
          ...meta.bands.map((b, i) => ({
            value: b.id,
            label: b.id,
            title: `${b.id}: ${wlRange(b)}, R≈${b.R} (key ${i + 1})`,
            disabled: !derived.playable(b.id).length,
          })),
          { value: "all", label: "All", title: "All detectors, each divided by its noise (key 0)", disabled: view.mode === "exp" },
        ]}
      />
      {showWl && <span className="detector-wl num">{active ? wlRange(active) : "S/N, 0.75–5.0 µm"}</span>}
    </div>
  );
}

export function DisplayPanel({ view, set }: { view: ViewState; set: Set }) {
  const cmapDisabled = view.mode === "diff" || view.mode === "rgb";
  return (
    <div className="panel-form">
      <div className="form-row">
        <MicroLabel>Stretch</MicroLabel>
        <Segmented<Stretch>
          label="Stretch"
          size="sm"
          value={view.stretch}
          onChange={(stretch) => set({ stretch })}
          options={[
            { value: "asinh", label: "asinh" },
            { value: "linear", label: "linear" },
            { value: "log", label: "log" },
          ]}
        />
      </div>
      <div className="form-row">
        <MicroLabel>Colour map{cmapDisabled ? " · fixed in this mode" : ""}</MicroLabel>
        <div className="cmaps">
          {(Object.keys(CMAP_LABEL) as Cmap[]).map((c) => (
            <button
              key={c}
              className={`cmap${view.cmap === c ? " on" : ""}`}
              disabled={cmapDisabled}
              onClick={() => set({ cmap: c })}
              aria-pressed={view.cmap === c}
            >
              <span className="cmap-swatch" style={{ background: lutGradient(LUTS[c]) }} />
              {CMAP_LABEL[c]}
            </button>
          ))}
        </div>
      </div>
      <div className="form-row">
        <MicroLabel>Black point · {view.lo.toFixed(1)}%</MicroLabel>
        <input type="range" min={0} max={20} step={0.5} value={view.lo} onChange={(e) => set({ lo: Math.min(Number(e.target.value), view.hi - 0.5) })} aria-label="Black point percentile" />
      </div>
      <div className="form-row">
        <MicroLabel>White point · {view.hi.toFixed(1)}%</MicroLabel>
        <input type="range" min={90} max={100} step={0.1} value={view.hi} onChange={(e) => set({ hi: Math.max(Number(e.target.value), view.lo + 0.5) })} aria-label="White point percentile" />
      </div>
      <div className="form-row">
        <MicroLabel>Zoom</MicroLabel>
        <Segmented<number>
          label="Zoom"
          size="sm"
          value={view.zoom}
          onChange={(zoom) => set({ zoom })}
          options={[{ value: 0, label: "Fit" }, ...[4, 6, 8, 12, 16].map((z) => ({ value: z, label: `${z}×` }))]}
        />
      </div>
      <Switch checked={view.smooth} onChange={(smooth) => set({ smooth })} label="Smooth pixels" />
      <p className="form-hint">Black and white points are percentiles over all epochs, so brightness stays constant while blinking.</p>
    </div>
  );
}

const LAYERS: { id: Overlay; label: string; hint?: string; icon?: React.ReactNode }[] = [
  { id: "target", label: "Target position", hint: "predicted from proper motion", icon: <Crosshair size={16} /> },
  { id: "track", label: "Predicted track", hint: "where the target is at each epoch", icon: <Route size={16} /> },
  { id: "gaia", label: "Gaia DR3", hint: "moved to the shown date" },
  { id: "catwise", label: "CatWISE2020", hint: "moved to the shown date" },
  { id: "2mass", label: "2MASS", hint: "positions from 1997–2001, not moved" },
  { id: "flags", label: "Contamination flags", hint: "pick flags in the Flags tab", icon: <Flag size={16} /> },
  { id: "compass", label: "Compass & scale", icon: <Compass size={16} /> },
];

export function LayersPanel({ view, set, catalogStatus }: { view: ViewState; set: Set; catalogStatus: CatalogStatus }) {
  const toggle = (o: Overlay) => set({ overlays: view.overlays.includes(o) ? view.overlays.filter((x) => x !== o) : [...view.overlays, o] });
  return (
    <div className="layers">
      {LAYERS.map((l) => {
        const style = CATALOG_STYLE[l.id];
        const st = catalogStatus[l.id];
        const on = view.overlays.includes(l.id);
        const status = st && on ? (st.state === "loading" ? "loading…" : st.state === "error" ? "unavailable, toggle to retry" : `${st.count} sources`) : null;
        return (
          <button key={l.id} className={`layer${on ? " on" : ""}`} onClick={() => toggle(l.id)} aria-pressed={on} title={st?.message}>
            <span className="layer-icon" style={style ? { color: style.color } : undefined}>
              {style ? <span className={`swatch ${style.shape}`} /> : l.icon}
            </span>
            <span className="layer-text">
              {l.label}
              <span className={`layer-hint${st?.state === "error" && on ? " err" : ""}`}>{status ?? l.hint}</span>
            </span>
            <span className="switch" aria-hidden />
          </button>
        );
      })}
    </div>
  );
}

/** Detector picker for the rail: one row per detector with its wavelength range. */
export function DetectorPanel({ meta, derived, view, set, refEpoch }: { meta: Meta; derived: Derived; view: ViewState; set: Set; refEpoch: number }) {
  const rows = [
    ...meta.bands.map((b, i) => ({ id: b.id, label: b.id, sub: `${wlRange(b)} · R≈${b.R}`, key: String(i + 1), disabled: !derived.playable(b.id).length })),
    { id: "all", label: "All", sub: "every detector, noise-weighted", key: "0", disabled: view.mode === "exp" },
  ];
  return (
    <div className="detector-panel">
      <div className="micro menu-title">Detector</div>
      {view.mode === "rgb" ? (
        <p className="form-hint">False colour uses all detectors: {rgbLabel(derived)}.</p>
      ) : (
        rows.map((r) => (
          <button key={r.id} className={`det-row${view.band === r.id ? " on" : ""}`} disabled={r.disabled} onClick={() => set({ band: r.id })}>
            <span className="det-id">{r.label}</span>
            <span className="det-sub">{r.sub}</span>
            <kbd className="kbd">{r.key}</kbd>
          </button>
        ))
      )}
      {view.mode === "diff" && (
        <label className="form-row ref-row">
          <span className="micro">Difference against</span>
          <select value={refEpoch} onChange={(e) => set({ ref: Number(e.target.value) })} aria-label="Reference epoch">
            {derived.playable(view.band).map((e) => (
              <option key={e} value={e}>
                epoch {String(e + 1).padStart(2, "0")} · {meta.epochs[e].date_min.slice(0, 7)}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}
