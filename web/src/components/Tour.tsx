/** Explore mode: a short guided tour of the five challenge targets, each with what to look for and a
 *  "find it yourself" challenge (markers hidden; click where you think the object is, then reveal). */
import { ArrowLeft, ArrowRight, Eye, Target as TargetIcon, X } from "lucide-react";
import type { Target } from "../types";

export const TOUR: { id: string; look: string; challenge: string }[] = [
  {
    id: "barnard",
    look: "The fastest-moving star in our sky: 10.4″ a year. In Difference mode red is where it is now, blue where it was the year before.",
    challenge: "Find the star that moved and click on it.",
  },
  {
    id: "eps-indi-b",
    look: "A pair of brown dwarfs 12 light-years away, orbiting the bright star ε Indi A 6.7′ to the west. Blink and watch them drift together.",
    challenge: "Click the brown-dwarf pair: the faint dot that moves along with the bright star.",
  },
  {
    id: "wise-0855",
    look: "The coldest brown dwarf known, about room temperature. Invisible below 3 µm; in false colour it is the reddest dot near the centre.",
    challenge: "Click the reddest dot.",
  },
  {
    id: "rw-aur",
    look: "A young star still wrapped in dust. At 4–5 µm it faded by half within a year: blink and watch it dim.",
    challenge: "Click the star that changes brightness.",
  },
  {
    id: "r-dra",
    look: "A pulsating red giant (a Mira variable) that swells and dims over about 245 days.",
    challenge: "Click the star that pulses.",
  },
];

export interface ChallengeState {
  guess: [number, number] | null;
  revealed: boolean;
}

export function TourCard(p: {
  presets: Target[];
  current: string | null;
  onGo: (id: string) => void;
  challenge: ChallengeState | null;
  onChallenge: (c: ChallengeState | null) => void;
  /** Distance of the guess from the target, arcsec (after reveal). */
  result: number | null;
  onClose: () => void;
}) {
  const i = Math.max(0, TOUR.findIndex((s) => s.id === p.current));
  const step = TOUR[i];
  const preset = p.presets.find((t) => t.id === step.id);
  const onTour = TOUR.some((s) => s.id === p.current);
  const verdict =
    p.result == null ? null : p.result <= 15 ? `Spot on: ${p.result.toFixed(0)}″ away.` : p.result <= 45 ? `Close: ${p.result.toFixed(0)}″ away.` : `It was ${p.result >= 120 ? `${(p.result / 60).toFixed(1)}′` : `${p.result.toFixed(0)}″`} from your click: see the crosshair.`;
  return (
    <section className="tour glass" aria-label="Guided tour">
      <button className="pick-close" onClick={p.onClose} aria-label="Close the tour">
        <X size={14} />
      </button>
      <div className="micro tour-kicker">
        Tour · {i + 1} / {TOUR.length}
      </div>
      <div className="tour-title">{preset?.name ?? step.id}</div>
      {onTour ? (
        <>
          {!p.challenge && <p className="tour-text">{step.look}</p>}
          {p.challenge && !p.challenge.revealed && (
            <p className="tour-text">
              <TargetIcon size={13} className="inline-icon" /> {step.challenge} {p.challenge.guess ? <span className="muted">Clicked. Reveal when ready.</span> : <span className="muted">Markers are hidden.</span>}
            </p>
          )}
          {p.challenge?.revealed && <p className="tour-text">{verdict ?? "Revealed."}</p>}
        </>
      ) : (
        <p className="tour-text">Five objects from the challenge, one after another. Start with the first.</p>
      )}
      <div className="tour-actions">
        <button className="btn ghost icon xs" disabled={i === 0 && onTour} onClick={() => p.onGo(TOUR[Math.max(0, i - 1)].id)} aria-label="Previous object">
          <ArrowLeft size={13} />
        </button>
        {onTour &&
          (!p.challenge ? (
            <button className="btn small-btn" onClick={() => p.onChallenge({ guess: null, revealed: false })}>
              <TargetIcon size={13} /> Find it yourself
            </button>
          ) : !p.challenge.revealed ? (
            <button className="btn small-btn" disabled={!p.challenge.guess} onClick={() => p.onChallenge({ ...p.challenge!, revealed: true })}>
              <Eye size={13} /> Reveal
            </button>
          ) : (
            <button className="btn ghost small-btn" onClick={() => p.onChallenge(null)}>
              Done
            </button>
          ))}
        <button className="btn primary small-btn" onClick={() => p.onGo(onTour ? TOUR[(i + 1) % TOUR.length].id : TOUR[0].id)}>
          {onTour ? (i + 1 < TOUR.length ? "Next" : "Again") : "Start"} <ArrowRight size={13} />
        </button>
      </div>
    </section>
  );
}
