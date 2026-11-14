/** Thin top progress bar, numbered section headers and the cinematic landing hero with a target index. */
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
