/** Long-baseline controls (in the Layers panel) and the archival strip that sits left of the SPHEREx
 *  scrubber in the dock: 2MASS, AllWISE and NEOWISE visits on their own compressed time axis. */
import { TWOMASS_BANDS, WISE_BANDS, isTwoMass, type ArchBands } from "../archival";
import { useElementWidth } from "../hooks";
import type { ArchivalFrame } from "../types";
import { Segmented, Switch } from "./ui/primitives";

export function LongBaselineControls(p: {
  on: boolean;
  onToggle: (on: boolean) => void;
  bands: ArchBands;
  onBands: (b: ArchBands) => void;
  /** Why it cannot be used here (co-moving sequence), or null. */
  unavailable: string | null;
  status: string | null;
  error: string | null;
}) {
  return (
    <div className="longbase">
      <div className="micro menu-title">Long baseline</div>
      <Switch
        checked={p.on}
        disabled={!!p.unavailable}
        onChange={p.onToggle}
        label="2MASS · WISE · NEOWISE"
        hint={p.unavailable ?? "Adds 1999–2024 survey images before the SPHEREx epochs (Blink)"}
      />
      {p.on && !p.unavailable && (
        <div className="longbase-bands">
          <Segmented<string> label="2MASS band" size="sm" value={p.bands[0]} onChange={(b) => p.onBands([b, p.bands[1]])} options={TWOMASS_BANDS.map((b) => ({ value: b, label: b }))} />
          <Segmented<string> label="WISE band" size="sm" value={p.bands[1]} onChange={(b) => p.onBands([p.bands[0], b])} options={WISE_BANDS.map((b) => ({ value: b, label: b }))} />
        </div>
      )}
      {p.on && (p.error || p.status) && <p className={`form-hint${p.error ? " err" : ""}`}>{p.error ?? p.status}</p>}
    </div>
  );
}

const Y0 = 1998.5;
const Y1 = 2025.2;

/** Archival visits as square (2MASS) and diamond (WISE) ticks on a 1999–2025 axis, current one in the accent. */
export function ArchivalStrip(p: { frames: ArchivalFrame[]; current: number | null; onSelect: (index: number) => void; status?: string | null }) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const pad = 8;
  const w = Math.max(width, 80);
  const x = (y: number) => pad + ((y - Y0) / (Y1 - Y0)) * (w - 2 * pad);
  const years = [2000, 2010, 2020];
  const pick = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || !p.frames.length) return;
    const mx = clientX - box.left;
    const best = p.frames.reduce((a, b) => (Math.abs(x(b.epoch_year) - mx) < Math.abs(x(a.epoch_year) - mx) ? b : a));
    if (best.index !== p.current) p.onSelect(best.index);
  };
  return (
    <div className="arch-strip" aria-label="Archival epochs">
      <div
        className="arch-axis"
        ref={ref}
        onPointerDown={(e) => {
          (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
          pick(e.clientX);
        }}
        onPointerMove={(e) => e.buttons && pick(e.clientX)}
      >
        <div className="scrub-rail" style={{ left: pad, right: pad }} />
        {p.frames.map((f) => (
          <button
            key={f.index}
            className={`arch-tick ${isTwoMass(f) ? "square" : "diamond"}${f.index === p.current ? " cur" : ""}${f.target_in_field ? "" : " out"}`}
            style={{ left: x(f.epoch_year) }}
            title={`${f.label} · ${f.date.slice(0, 10)}${f.n_images > 1 ? ` · median of ${f.n_images} images` : ""}${f.target_in_field ? "" : " · target outside the field"}`}
            aria-label={`${f.label}, ${f.date.slice(0, 10)}`}
          />
        ))}
        {years.map((y) => (
          <span key={y} className="scrub-label middle" style={{ left: x(y) }}>
            {y}
          </span>
        ))}
        {!p.frames.length && <span className="arch-status">{p.status ?? "No archival images in these bands"}</span>}
      </div>
      <span className="arch-break" aria-hidden>
        ⫽
      </span>
    </div>
  );
}
