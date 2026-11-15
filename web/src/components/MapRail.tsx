/** The tool rail of the zoomed-out sky map (ADR-0059). It replaces the field rail while the map has the view: the
 *  field's tools (detector, display, analysis ...) do nothing there, the map needs its own — where to go, what to
 *  draw over the sky, picking a point, and the way back to the field. */
import { Globe2, Layers, LocateFixed, Maximize, Minimize, MousePointerClick, Navigation, Telescope } from "lucide-react";
import type { ReactNode } from "react";
import type { MapCatalog } from "../skymap";
import { Popover, Segmented, Switch } from "./ui/primitives";

export interface MapLayers {
  footprints: boolean;
  grid: boolean;
  galactic: boolean;
  ecliptic: boolean;
  deep: boolean;
  places: boolean;
  catalogs: MapCatalog[];
}

export const DEFAULT_MAP_LAYERS: MapLayers = { footprints: false, grid: false, galactic: false, ecliptic: false, deep: true, places: true, catalogs: [] };

const BANDS = ["D1", "D2", "D3", "D4", "D5", "D6"] as const;
const BAND_UM: Record<(typeof BANDS)[number], string> = { D1: "0.75–1.1 µm", D2: "1.1–1.6 µm", D3: "1.6–2.4 µm", D4: "2.4–3.8 µm", D5: "3.8–4.4 µm", D6: "4.4–5.0 µm" };

export function MapRail(p: {
  goTo: ReactNode | ((close: () => void) => ReactNode);
  band: string;
  onBand: (band: (typeof BANDS)[number]) => void;
  layers: MapLayers;
  onLayers: (l: MapLayers) => void;
  /** Footprints: what the layer shows now (count, or why nothing). */
  footprintNote?: string | null;
  picking: boolean;
  onPicking: (on: boolean) => void;
  /** Omitted without a field to go back to. */
  onBackToField?: () => void;
  onWholeSky: () => void;
  fullscreen?: { on: boolean; toggle: () => void };
}) {
  const L = p.layers;
  const set = (patch: Partial<MapLayers>) => p.onLayers({ ...L, ...patch });
  const cat = (c: MapCatalog, on: boolean) => set({ catalogs: on ? [...L.catalogs.filter((x) => x !== c), c] : L.catalogs.filter((x) => x !== c) });
  const band = (BANDS as readonly string[]).includes(p.band) ? p.band : "D1";
  return (
    <nav className="rail glass map-rail" aria-label="Map tools">
      <Popover label={<Navigation size={16} />} title="Go to a place on the map" align="side" width={330}>
        {p.goTo}
      </Popover>
      <Popover label={<><Telescope size={16} /><span className="rail-tag">{band}</span></>} title="SPHEREx map channel" align="side" width={290}>
        <div className="map-panel">
          <div className="micro menu-title">SPHEREx all-sky map</div>
          <Segmented label="SPHEREx detector" size="sm" value={band} onChange={(b) => p.onBand(b as (typeof BANDS)[number])} options={BANDS.map((b) => ({ value: b, label: b, title: BAND_UM[b] }))} />
          <p className="small muted">
            {BAND_UM[band as (typeof BANDS)[number]]}: the middle channel of detector {band} in the DR1 all-sky cube (IRSA HiPS, cached on our server).
          </p>
        </div>
      </Popover>
      <Popover label={<Layers size={16} />} title="Map layers" align="side" width={310}>
        <div className="map-panel">
          <div className="micro menu-title">SPHEREx</div>
          <Switch checked={L.footprints} onChange={(footprints) => set({ footprints })} label="Exposure footprints" hint={p.footprintNote ?? "Outlines of SPHEREx images, from our file catalogue"} />
          <Switch checked={L.deep} onChange={(deep) => set({ deep })} label="Deep fields" hint="Ecliptic poles, observed every orbit" />
          <div className="micro menu-title">Sky</div>
          <Switch checked={L.grid} onChange={(grid) => set({ grid })} label="RA / Dec grid" />
          <Switch checked={L.galactic} onChange={(galactic) => set({ galactic })} label="Galactic plane" />
          <Switch checked={L.ecliptic} onChange={(ecliptic) => set({ ecliptic })} label="Ecliptic" />
          <Switch checked={L.places} onChange={(places) => set({ places })} label="Featured and recent places" />
          <div className="micro menu-title">Catalogues at map scale</div>
          <Switch checked={L.catalogs.includes("gaia")} onChange={(on) => cat("gaia", on)} label="Gaia DR3 stars" hint="Brightest first at every zoom (CDS)" />
          <Switch checked={L.catalogs.includes("simbad")} onChange={(on) => cat("simbad", on)} label="SIMBAD objects" hint="Galaxies, nebulae, named stars (CDS)" />
        </div>
      </Popover>
      <button className={`btn ghost icon rail-btn${p.picking ? " active" : ""}`} onClick={() => p.onPicking(!p.picking)} title={p.picking ? "Pick a point: on (a click on the map shows what SPHEREx has there)" : "Pick a point: off (clicks only pan the map)"} aria-pressed={p.picking}>
        <MousePointerClick size={16} />
        <span className="rail-tag">Pick</span>
      </button>
      {p.onBackToField && (
        <button className="btn ghost icon rail-btn" onClick={p.onBackToField} title="Back to the field">
          <LocateFixed size={16} />
          <span className="rail-tag">Field</span>
        </button>
      )}
      <button className="btn ghost icon rail-btn" onClick={p.onWholeSky} title="Whole sky">
        <Globe2 size={16} />
      </button>
      {p.fullscreen && (
        <button className={`btn ghost icon rail-btn rail-fs${p.fullscreen.on ? " active" : ""}`} onClick={p.fullscreen.toggle} title={p.fullscreen.on ? "Exit full screen (F, or hold Esc)" : "Full screen (F)"} aria-pressed={p.fullscreen.on}>
          {p.fullscreen.on ? <Minimize size={16} /> : <Maximize size={16} />}
        </button>
      )}
    </nav>
  );
}
