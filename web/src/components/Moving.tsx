/** Transient card for a solar-system lookup: searching IRSA MOST, picking among ambiguous names, or why it
 *  failed. It disappears as soon as the co-moving sequence starts building. */
import { Loader2, Orbit, X } from "lucide-react";
import type { Progress } from "../api";
import type { MovingChoice } from "../types";

export type MovingState =
  | { state: "search"; query: string; progress: Progress | null }
  | { state: "choices"; query: string; message: string; choices: MovingChoice[] }
  | { state: "error"; query: string; message: string };

export function MovingCard(p: { st: MovingState; onChoose: (c: MovingChoice) => void; onClose: () => void }) {
  const st = p.st;
  return (
    <div className="moving-card glass" role="status" aria-live="polite">
      <button className="pick-close" onClick={p.onClose} aria-label={st.state === "search" ? "Cancel" : "Close"}>
        <X size={14} />
      </button>
      <div className="micro">
        <Orbit size={12} /> Solar system · co-moving
      </div>
      {st.state === "search" && (
        <p className="moving-line">
          <Loader2 size={14} className="spin" /> Finding “{st.query}” in SPHEREx exposures (IRSA MOST)
          {st.progress?.elapsed ? <span className="muted num"> · {Math.round(st.progress.elapsed)} s</span> : null}
        </p>
      )}
      {st.state === "choices" && (
        <>
          <p className="moving-line">Several objects are called “{st.query}”. Which one?</p>
          <div className="moving-choices">
            {st.choices.slice(0, 8).map((c) => (
              <button key={c.naif_id} className="menu-item" onClick={() => p.onChoose(c)}>
                <span>
                  {c.name}
                  <span className="menu-hint">
                    {[c.type, c.designation].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </>
      )}
      {st.state === "error" && <p className="moving-line err">{st.message}</p>}
    </div>
  );
}
