/** Slim vertical tool rail on the right edge; each button opens a compact panel beside it. "Go to" is there
 *  even before a field has loaded; the rest need a sequence. */
import { Box, Info, Layers, ListTree, Maximize, Minimize, Navigation, ScanSearch, Share2, SlidersHorizontal, Telescope } from "lucide-react";
import type { ReactNode } from "react";
import { Popover } from "./ui/primitives";

type Panel = ReactNode | ((close: () => void) => ReactNode);

export function Rail(p: {
  goTo: Panel;
  objects?: Panel;
  detector?: ReactNode;
  display?: ReactNode;
  layers?: ReactNode;
  /** Field analysis, known objects, Track & Stack. */
  field?: ReactNode;
  share?: ReactNode;
  detailsOpen: boolean;
  onDetails?: () => void;
  bandLabel: string;
  is3d?: boolean;
  on3d?: () => void;
  /** Omitted where the browser can't go fullscreen (iPhone Safari). */
  fullscreen?: { on: boolean; toggle: () => void };
}) {
  return (
    <nav className="rail glass" aria-label="Tools">
      <Popover label={<Navigation size={16} />} title="Go to coordinates" align="side" width={330}>
        {p.goTo}
      </Popover>
      {p.objects && (
        <Popover label={<ListTree size={16} />} title="Objects in view" align="side" width={350}>
          {p.objects}
        </Popover>
      )}
      {p.detector && (
        <Popover label={<><Telescope size={16} /><span className="rail-tag">{p.bandLabel}</span></>} title="Detector" align="side" width={280}>
          {p.detector}
        </Popover>
      )}
      {p.display && (
        <Popover label={<SlidersHorizontal size={16} />} title="Display" align="side" width={300}>
          {p.display}
        </Popover>
      )}
      {p.layers && (
        <Popover label={<Layers size={16} />} title="Layers" align="side" width={300}>
          {p.layers}
        </Popover>
      )}
      {p.field && (
        <Popover label={<ScanSearch size={16} />} title="Field analysis" align="side" width={340}>
          {p.field}
        </Popover>
      )}
      {p.on3d && (
        <button className={`btn ghost icon rail-btn${p.is3d ? " active" : ""}`} onClick={p.on3d} title="3D view: relief, time cube, λ cube (v)" aria-pressed={!!p.is3d}>
          <Box size={16} />
          <span className="rail-tag">3D</span>
        </button>
      )}
      {p.onDetails && (
        <button className={`btn ghost icon rail-btn${p.detailsOpen ? " active" : ""}`} onClick={p.onDetails} title="Spectrum, light curve and data" aria-pressed={p.detailsOpen}>
          <Info size={16} />
        </button>
      )}
      {p.share && (
        <Popover label={<Share2 size={16} />} title="Share" align="side" width={330}>
          {p.share}
        </Popover>
      )}
      {p.fullscreen && (
        <button
          className={`btn ghost icon rail-btn${p.fullscreen.on ? " active" : ""}`}
          onClick={p.fullscreen.toggle}
          title={p.fullscreen.on ? "Exit full screen (F, or hold Esc)" : "Full screen (F)"}
          aria-pressed={p.fullscreen.on}
        >
          {p.fullscreen.on ? <Minimize size={16} /> : <Maximize size={16} />}
        </button>
      )}
    </nav>
  );
}
