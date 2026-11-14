/** Slim vertical tool rail on the right edge; each button opens a compact panel beside it. */
import { Info, Layers, Share2, SlidersHorizontal, Telescope } from "lucide-react";
import type { ReactNode } from "react";
import { Popover } from "./ui/primitives";

export function Rail(p: { detector: ReactNode; display: ReactNode; layers: ReactNode; share: ReactNode; detailsOpen: boolean; onDetails: () => void; bandLabel: string }) {
  return (
    <nav className="rail glass" aria-label="Tools">
      <Popover label={<><Telescope size={16} /><span className="rail-tag">{p.bandLabel}</span></>} title="Detector" align="side" width={280}>
        {p.detector}
      </Popover>
      <Popover label={<SlidersHorizontal size={16} />} title="Display" align="side" width={300}>
        {p.display}
      </Popover>
      <Popover label={<Layers size={16} />} title="Layers" align="side" width={300}>
        {p.layers}
      </Popover>
      <button className={`btn ghost icon rail-btn${p.detailsOpen ? " active" : ""}`} onClick={p.onDetails} title="Spectrum, light curve and data" aria-pressed={p.detailsOpen}>
        <Info size={16} />
      </button>
      <Popover label={<Share2 size={16} />} title="Share" align="side" width={330}>
        {p.share}
      </Popover>
    </nav>
  );
}
