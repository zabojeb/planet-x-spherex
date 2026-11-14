/** Always-visible toolbar (mode, detector) plus the Display and Layers panels used in popovers or the mobile sheet. */
import { Compass, Crosshair, Flag, Grid3x3, Route, RotateCw } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
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

/** A layer and, for its hover card, what the data are: where they come from, what they contain, how they are drawn. */
interface LayerDef {
  id: Overlay;
  label: string;
  hint?: string;
  icon?: React.ReactNode;
  about: { source?: string; what: string; drawn: string };
}

const LAYERS: LayerDef[] = [
  {
    id: "target",
    label: "Target position",
    hint: "predicted from proper motion",
    icon: <Crosshair size={16} />,
    about: {
      what: "Where the target should be on the date of the frame on screen: its catalogue position carried forward by its proper motion. For a plain patch of sky it marks the field centre.",
      drawn: "Crosshair; in the cubes it lies on the current layer.",
    },
  },
  {
    id: "track",
    label: "Predicted track",
    hint: "where the target is at each epoch",
    icon: <Route size={16} />,
    about: {
      what: "The target's predicted position at every SPHEREx epoch. A source that sits on the dot in every frame is the target; one beside it is a neighbour.",
      drawn: "Orange line with a dot per epoch, the larger dot for the epoch on screen; in the time cube it slants up through the layers.",
    },
  },
  {
    id: "gaia",
    label: "Gaia DR3",
    hint: "moved to the shown date",
    about: {
      source: "ESA Gaia, Data Release 3 (2022) · optical, 0.33–1.05 µm",
      what: "Positions of about 1.8 billion sources, with parallaxes and proper motions for 1.5 billion of them, at epoch 2016.0 and down to G ≈ 21: the reference for where stars are and how fast they move.",
      drawn: "Blue circles at the shown date; stars faster than 0.1″/yr are larger and get a motion line across the SPHEREx dates.",
    },
  },
  {
    id: "catwise",
    label: "CatWISE2020",
    hint: "moved to the shown date",
    about: {
      source: "WISE + NEOWISE, 2010–2018 · infrared, 3.4 and 4.6 µm",
      what: "About 1.9 billion infrared sources with proper motions measured over eight years, at epoch 2015.4. It sees cold brown dwarfs and dusty objects that are faint or invisible to Gaia; both bands lie inside SPHEREx's 0.75–5 µm.",
      drawn: "Orange squares at the shown date.",
    },
  },
  {
    id: "2mass",
    label: "2MASS",
    hint: "positions from 1997–2001, not moved",
    about: {
      source: "Two Micron All Sky Survey, 1997–2001 · near infrared, J H Ks (1.25–2.16 µm)",
      what: "About 470 million point sources. It has no proper motions, so positions are where things were a quarter century ago: a star offset from its 2MASS diamond has moved since.",
      drawn: "Teal diamonds at their 1997–2001 positions.",
    },
  },
  {
    id: "simbad",
    label: "SIMBAD named objects",
    hint: "names and object types, moved to the shown date",
    about: {
      source: "SIMBAD, CDS Strasbourg · literature database",
      what: "Named and studied objects of every kind (stars, brown dwarfs, galaxies, nebulae, variables, young stars) with their object type, magnitudes and number of papers. Answers “what is that?”.",
      drawn: "Orchid plus signs at the shown date; the most-cited carry their names.",
    },
  },
  {
    id: "flags",
    label: "Contamination flags",
    hint: "pick flags in the Flags tab",
    icon: <Flag size={16} />,
    about: {
      source: "SPHEREx Level 2 pixel flags",
      what: "Pixels the pipeline marked in the exposures of the frame: saturation, nonlinearity, ghosts, crosstalk, bloom trails, persistence. They tell an artefact from a real source.",
      drawn: "Red tint, flat sky only. Choose which flags in the details drawer (ⓘ): Data → Flags.",
    },
  },
  {
    id: "grid",
    label: "Coordinate grid",
    hint: "RA/Dec lines with labels (G)",
    icon: <Grid3x3 size={16} />,
    about: {
      what: "Lines of constant right ascension and declination (ICRS), labelled on the left and bottom edges; the spacing follows the zoom.",
      drawn: "Faint lines, flat sky only. Key G.",
    },
  },
  {
    id: "compass",
    label: "Compass & scale",
    icon: <Compass size={16} />,
    about: {
      what: "Where north and east are and how long an arcminute is at the current zoom. North is up and east is left, as when you look up at the sky.",
      drawn: "Arrows and a scale bar at the bottom left; N and E on the floor of the 3D views.",
    },
  },
];

/** Hover card for a layer, beside the panel (a portal: the panel scrolls and would clip it). */
function LayerTip({ def, anchor, status }: { def: LayerDef; anchor: { top: number; right: number }; status: string | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(anchor.top);
  useLayoutEffect(() => {
    const h = ref.current?.offsetHeight ?? 0;
    setTop(Math.max(12, Math.min(anchor.top, window.innerHeight - h - 12)));
  }, [anchor.top, def.id]);
  const style = CATALOG_STYLE[def.id];
  return createPortal(
    <div ref={ref} className="layer-tip glass" style={{ top, right: anchor.right }} role="tooltip" id={`layer-tip-${def.id}`}>
      <div className="layer-tip-title">
        {style && <span className={`swatch ${style.shape}`} style={{ color: style.color }} />}
        {def.label}
      </div>
      {def.about.source && <div className="layer-tip-source">{def.about.source}</div>}
      <p>{def.about.what}</p>
      <p className="layer-tip-drawn">{def.about.drawn}</p>
      {status && <div className="layer-tip-status">{status}</div>}
    </div>,
    document.body,
  );
}

export function LayersPanel({ view, set, catalogStatus }: { view: ViewState; set: Set; catalogStatus: CatalogStatus }) {
  const toggle = (o: Overlay) => set({ overlays: view.overlays.includes(o) ? view.overlays.filter((x) => x !== o) : [...view.overlays, o] });
  const [tip, setTip] = useState<{ id: Overlay; top: number; right: number } | null>(null);
  const show = (id: Overlay, el: HTMLElement) => {
    const row = el.getBoundingClientRect();
    const panel = (el.closest(".popover") ?? el).getBoundingClientRect();
    setTip({ id, top: row.top - 6, right: window.innerWidth - panel.left + 10 });
  };
  const statusOf = (id: Overlay) => {
    const st = catalogStatus[id];
    if (!st || !view.overlays.includes(id)) return null;
    return st.state === "loading" ? "loading…" : st.state === "error" ? "unavailable, toggle to retry" : `${st.count} sources`;
  };
  const tipDef = tip ? LAYERS.find((l) => l.id === tip.id) : null;
  return (
    <div className="layers" onPointerLeave={() => setTip(null)}>
      {LAYERS.map((l) => {
        const style = CATALOG_STYLE[l.id];
        const st = catalogStatus[l.id];
        const on = view.overlays.includes(l.id);
        const status = statusOf(l.id);
        return (
          <button
            key={l.id}
            className={`layer${on ? " on" : ""}`}
            onClick={() => toggle(l.id)}
            aria-pressed={on}
            aria-describedby={tip?.id === l.id ? `layer-tip-${l.id}` : undefined}
            onPointerEnter={(e) => e.pointerType === "mouse" && show(l.id, e.currentTarget)}
            onFocus={(e) => show(l.id, e.currentTarget)}
            onBlur={() => setTip(null)}
          >
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
      {tip && tipDef && <LayerTip def={tipDef} anchor={tip} status={catalogStatus[tip.id]?.state === "error" && view.overlays.includes(tip.id) ? catalogStatus[tip.id].message ?? null : null} />}
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
