/** Thin top progress bar, the loading chip at the edge of the sky, and the cinematic landing hero with a target
 *  index. */
import { ArrowUpRight, X } from "lucide-react";
import type { Progress } from "../api";
import { formatPm } from "../astro";
import type { Target } from "../types";

export function ProgressBar({ progress }: { progress: Progress | null }) {
  if (!progress) return <div className="progress idle" aria-hidden />;
  const pct = progress.total > 0 ? (100 * progress.done) / progress.total : null;
  return (
    <div className="progress" aria-hidden>
      <div className={`progress-bar${pct == null ? " indeterminate" : ""}`}>
        <span style={pct == null ? undefined : { width: `${Math.max(2, pct)}%` }} />
      </div>
    </div>
  );
}

/** What a build or download stage is doing, in words, with its percentage when it has one. */
export function loaderText(p: Progress): { text: string; pct: number | null } {
  const pct = p.total > 0 ? Math.round((100 * p.done) / p.total) : null;
  switch (p.stage) {
    case "busy":
      return { text: `Many users right now · retrying in ${p.retryIn ?? 1}s`, pct: null };
    case "search":
      return { text: "Querying the IRSA archive", pct: null };
    case "fetch":
      return { text: `Reading ${p.total || ""} exposures`, pct };
    case "stack":
      return { text: "Aligning and co-adding epochs", pct: null };
    case "publish":
      return { text: "Publishing results", pct: null };
    case "download":
    case "done":
      return { text: "Downloading images", pct };
    default:
      return { text: p.ahead ? `Waiting for a free worker · ${p.ahead} ahead` : "Waiting for a free worker", pct: null };
  }
}

/** A small status chip at the edge of the sky while something loads: it never covers the image, which shows up
 *  underneath as soon as there is one (a preview while the build runs). `note` says what is on screen meanwhile. */
export function LoadChip({ progress, note }: { progress: Progress; note?: string | null }) {
  const { text, pct } = loaderText(progress);
  return (
    <div className="load-chip glass" role="status" aria-live="polite">
      <span className="load-chip-dot" aria-hidden />
      <span className="load-chip-body">
        <span className="load-chip-text">
          {text}
          {pct != null && <span className="load-chip-pct"> {pct}%</span>}
        </span>
        {note && <span className="load-chip-note">{note}</span>}
        <span className={`load-chip-bar${pct == null ? " indeterminate" : ""}`} aria-hidden>
          <span style={pct == null ? undefined : { width: `${Math.max(2, pct)}%` }} />
        </span>
      </span>
    </div>
  );
}

function motionLabel(t: Target): string {
  const pm = Math.hypot(t.pmra, t.pmdec);
  return pm >= 1 ? formatPm(t.pmra, t.pmdec) : "—";
}

export function Hero({ presets, onPick, onDismiss, onPrefetch }: { presets: Target[]; onPick: (id: string) => void; onDismiss: () => void; onPrefetch?: (id: string) => void }) {
  return (
    <section className="hero" aria-label="Introduction">
      <div className="hero-grid" aria-hidden />
      <button className="btn ghost icon hero-close" onClick={onDismiss} aria-label="Hide introduction">
        <X size={16} />
      </button>
      <div className="hero-inner">
        <div className="hero-kicker">NASA SPHEREx · 0.75–5 µm · 102 bands · whole sky every 6 months</div>
        <h1 className="hero-title">
          Watch the infrared sky <em>move.</em>
        </h1>
        <p className="hero-sub">
          Every SPHEREx exposure of a patch of sky, aligned and stacked by visit, played like a film. Catch nearby stars, brown dwarfs
          and variable stars in the act, or search any position.
        </p>
        <ol className="index" aria-label="Challenge targets">
          {presets.map((t, i) => (
            <li key={t.id}>
              <button
                className="index-row"
                onClick={() => t.id && onPick(t.id)}
                onPointerEnter={() => t.id && onPrefetch?.(t.id)}
                onFocus={() => t.id && onPrefetch?.(t.id)}
                title={t.description ?? undefined}
              >
                <span className="index-n">{String(i + 1).padStart(2, "0")}</span>
                <span className="index-name">{t.name}</span>
                <span className="index-motion">{motionLabel(t)}</span>
                <span className="index-desc">{t.description}</span>
                <ArrowUpRight size={16} className="index-arrow" aria-hidden />
              </button>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
