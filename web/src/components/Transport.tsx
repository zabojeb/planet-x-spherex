/** Transport: previous / play / next, an epoch scrubber on a true time axis, speed and playback options. */
import { ChevronLeft, ChevronRight, Pause, Play } from "lucide-react";
import { useRef } from "react";
import { formatDate, formatMonth } from "../astro";
import { useElementWidth } from "../hooks";
import type { Mode } from "../state";
import type { Meta } from "../types";

const SPEEDS = [2000, 1000, 600, 400, 250, 125];

interface Props {
  meta: Meta;
  playing: boolean;
  canPlay: boolean;
  onToggle: () => void;
  onStep: (dir: -1 | 1) => void;
  speed: number;
  onSpeed: (ms: number) => void;
  ends: boolean;
  onEnds: (v: boolean) => void;
  partial: boolean;
  onPartial: (v: boolean) => void;
  mode: Mode;
  current: number;
  available: number[];
  refEpoch: number | null;
  stackSel: number[] | null;
  onSelect: (e: number) => void;
  onToggleStack: (e: number) => void;
  exposures: { count: number; index: number; onIndex: (i: number) => void } | null;
}

export function Transport(p: Props) {
  const showEnds = p.mode === "blink" || p.mode === "diff" || p.mode === "rgb";
  const hasPartial = p.meta.epochs.some((e) => e.partial);
  const speeds = SPEEDS.includes(p.speed) ? SPEEDS : [...SPEEDS, p.speed].sort((a, b) => b - a);
  return (
    <div className="transport">
      <div className="transport-buttons">
        <button className="btn ghost icon" onClick={() => p.onStep(-1)} disabled={!p.canPlay} aria-label="Previous (←)" title="Previous (←)">
          <ChevronLeft size={16} />
        </button>
        <button className="btn play" onClick={p.onToggle} disabled={!p.canPlay} aria-label={p.playing ? "Pause (space)" : "Play (space)"} title="Play / pause (space)">
          {p.playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <button className="btn ghost icon" onClick={() => p.onStep(1)} disabled={!p.canPlay} aria-label="Next (→)" title="Next (→)">
          <ChevronRight size={16} />
        </button>
      </div>
      <div className="transport-track">
        <Scrubber {...p} />
        {p.exposures && p.exposures.count > 0 && (
          <div className="exp-strip" role="group" aria-label="Exposures">
            <span className="micro num">
              Exposure {p.exposures.index + 1} / {p.exposures.count}
            </span>
            <div className="exp-dots">
              {Array.from({ length: p.exposures.count }, (_, i) => (
                <button key={i} className={i === p.exposures!.index ? "on" : ""} onClick={() => p.exposures!.onIndex(i)} aria-label={`Exposure ${i + 1}`} />
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="transport-options">
        <label className="speed" title="Playback speed">
          <span className="micro">Speed</span>
          <select value={p.speed} onChange={(e) => p.onSpeed(Number(e.target.value))} aria-label="Playback speed">
            {speeds.map((s) => (
              <option key={s} value={s}>
                {Number.isInteger(1000 / s) ? 1000 / s : (1000 / s).toFixed(1)} fps
              </option>
            ))}
          </select>
        </label>
        {showEnds && (
          <button className={`chip${p.ends ? " on" : ""}`} aria-pressed={p.ends} onClick={() => p.onEnds(!p.ends)} title="Blink only the first and last epoch: the longest baseline">
            First ↔ last
          </button>
        )}
        {hasPartial && p.mode !== "exp" && p.mode !== "stack" && (
          <button className={`chip${p.partial ? " on" : ""}`} aria-pressed={p.partial} onClick={() => p.onPartial(!p.partial)} title="Short partial visits have few exposures and are skipped by default">
            Partial visits
          </button>
        )}
      </div>
    </div>
  );
}

function Scrubber(p: Props) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const dragging = useRef(false);
  const eps = p.meta.epochs;
  const pad = 14;
  const w = Math.max(width, 120);
  const t0 = Math.min(...eps.map((e) => e.mjd_mean));
  const t1 = Math.max(...eps.map((e) => e.mjd_mean));
  const x = (t: number) => (t1 === t0 ? w / 2 : pad + ((t - t0) / (t1 - t0)) * (w - 2 * pad));
  const selectable = eps.filter((e) => p.available.includes(e.index));

  // Labels: current epoch first, then nearest neighbours, skipping collisions. Labels near the ends are
  // anchored inwards, so collisions are tested on where each label actually sits.
  const anchor = (xe: number) => (xe < 34 ? "start" : xe > w - 34 ? "end" : "middle");
  const centre = (xe: number) => (anchor(xe) === "start" ? xe + 28 : anchor(xe) === "end" ? xe - 28 : xe);
  const labelled = new Set<number>();
  const taken: number[] = [];
  [...eps]
    .sort((a, b) => Math.abs(a.index - p.current) - Math.abs(b.index - p.current) || a.index - b.index)
    .forEach((e) => {
      const c = centre(x(e.mjd_mean));
      if (taken.every((t) => Math.abs(t - c) > 66)) {
        labelled.add(e.index);
        taken.push(c);
      }
    });

  const pick = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || !selectable.length) return;
    const mx = clientX - box.left;
    const best = selectable.reduce((a, b) => (Math.abs(x(b.mjd_mean) - mx) < Math.abs(x(a.mjd_mean) - mx) ? b : a));
    if (best.index !== p.current) p.onSelect(best.index);
  };

  const cur = eps[p.current];
  return (
    <div
      className="scrubber"
      ref={ref}
      role="slider"
      aria-label="Epoch"
      aria-valuemin={1}
      aria-valuemax={eps.length}
      aria-valuenow={p.current + 1}
      aria-valuetext={cur ? `Epoch ${p.current + 1}, ${formatDate(cur.mjd_mean)}` : undefined}
      onPointerDown={(e) => {
        if (p.mode === "stack") return;
        dragging.current = true;
        (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
        pick(e.clientX);
      }}
      onPointerMove={(e) => dragging.current && pick(e.clientX)}
      onPointerUp={() => (dragging.current = false)}
    >
      <div className="scrub-rail" style={{ left: pad, right: pad }} />
      {cur && p.mode !== "stack" && <div className="scrub-fill" style={{ left: pad, width: Math.max(0, x(cur.mjd_mean) - pad) }} />}
      {eps.map((e) => {
        const has = p.available.includes(e.index);
        const isCur = e.index === p.current;
        const inStack = p.mode === "stack" && has && (!p.stackSel || p.stackSel.includes(e.index));
        const cls = ["scrub-tick", isCur && p.mode !== "stack" ? "cur" : "", has ? "" : "nodata", e.partial ? "partial" : "", p.mode === "stack" ? (inStack ? "box on" : "box") : ""]
          .filter(Boolean)
          .join(" ");
        return (
          <button
            key={e.index}
            className={cls}
            style={{ left: x(e.mjd_mean) }}
            disabled={!has}
            title={`Epoch ${e.index + 1}: ${formatDate(e.mjd_min)} → ${formatDate(e.mjd_max)} · ${e.n} exposures${e.partial ? " · partial visit" : ""}`}
            aria-label={`Epoch ${e.index + 1}`}
            onPointerDown={(ev) => {
              if (p.mode === "stack") {
                ev.stopPropagation();
                if (has) p.onToggleStack(e.index);
              }
            }}
          >
            {p.refEpoch === e.index && p.mode === "diff" && <span className="scrub-ref">ref</span>}
          </button>
        );
      })}
      {eps.map((e) =>
        labelled.has(e.index) ? (
          <span key={`l${e.index}`} className={`scrub-label ${anchor(x(e.mjd_mean))}${e.index === p.current ? " cur" : ""}`} style={{ left: x(e.mjd_mean) }}>
            {formatMonth(e.mjd_mean)}
          </span>
        ) : null,
      )}
    </div>
  );
}
