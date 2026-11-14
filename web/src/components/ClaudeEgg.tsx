/** Easter egg: type "claude" in the search and press Enter. Stars fly in and gather into an asterism,
 *  and the survey finally finds Planet X. Click anywhere or press Esc to return to the sky. */
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

/** Small seeded PRNG (mulberry32) so the asterism has the same hand-drawn shape every time. */
function rng(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const RAYS = 11;
const GATHER_MS = 1500;

interface Star { x: number; y: number; r: number; fx: number; fy: number; delay: number; tip: boolean }

function buildAsterism() {
  const r = rng(1337);
  const stars: Star[] = [];
  const lines: { d: string; delay: number }[] = [];
  const fly = () => (r() - 0.5) * 520;
  for (let i = 0; i < RAYS; i++) {
    const a = (((i / RAYS) * 360 + (r() - 0.5) * 16 - 90) * Math.PI) / 180;
    const len = 56 + r() * 32;
    const mid = len * (0.42 + r() * 0.16);
    const bend = (r() - 0.5) * 0.14; // a slight kink so the rays read hand-drawn
    const mx = Math.cos(a + bend) * mid;
    const my = Math.sin(a + bend) * mid;
    const tx = Math.cos(a) * len;
    const ty = Math.sin(a) * len;
    const delay = i * 55;
    stars.push({ x: mx, y: my, r: 1.1 + r() * 0.6, fx: fly(), fy: fly(), delay: delay + 120, tip: false });
    stars.push({ x: tx, y: ty, r: 2 + r() * 1.5, fx: fly(), fy: fly(), delay, tip: true });
    lines.push({ d: `M0,0 L${mx.toFixed(2)},${my.toFixed(2)} L${tx.toFixed(2)},${ty.toFixed(2)}`, delay: GATHER_MS + i * 70 });
  }
  return { stars, lines };
}

function buildDust(n: number) {
  const r = rng(42);
  return Array.from({ length: n }, () => ({
    left: `${(r() * 100).toFixed(2)}%`,
    top: `${(r() * 100).toFixed(2)}%`,
    size: r() < 0.85 ? 1 : 2,
    d: `${(1.6 + r() * 2.8).toFixed(2)}s`,
    w: `${(r() * 3).toFixed(2)}s`,
  }));
}

const METEORS = [
  { left: "12%", top: "18%", delay: "2.6s" },
  { left: "58%", top: "8%", delay: "4.4s" },
  { left: "30%", top: "62%", delay: "6.1s" },
];

export function ClaudeEgg({ onDone }: { onDone: () => void }) {
  const { stars, lines } = useMemo(buildAsterism, []);
  const dust = useMemo(() => buildDust(110), []);
  const [leaving, setLeaving] = useState(false);

  const leave = () => setLeaving(true);
  useEffect(() => {
    if (!leaving) return;
    const t = window.setTimeout(onDone, 420);
    return () => window.clearTimeout(t);
  }, [leaving, onDone]);
  useEffect(() => {
    // Esc only: the Enter that summoned the egg is still bubbling when this listener is attached.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLeaving(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return createPortal(
    <div className={`egg${leaving ? " leaving" : ""}`} role="dialog" aria-label="Planet X found" onClick={leave}>
      {dust.map((s, i) => (
        <i
          key={i}
          className="egg-dust"
          style={{ left: s.left, top: s.top, width: s.size, height: s.size, ["--d" as string]: s.d, ["--w" as string]: s.w }}
        />
      ))}
      {METEORS.map((m, i) => (
        <i key={i} className="egg-meteor" style={{ left: m.left, top: m.top, ["--delay" as string]: m.delay }} />
      ))}

      <svg className="egg-sky" viewBox="-100 -100 200 200" aria-hidden>
        <defs>
          <radialGradient id="egg-core">
            <stop offset="0%" stopColor="#fff3ea" stopOpacity="0.95" />
            <stop offset="22%" stopColor="#d97757" stopOpacity="0.7" />
            <stop offset="100%" stopColor="#d97757" stopOpacity="0" />
          </radialGradient>
          <filter id="egg-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="1.6" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <g className="egg-spin">
          <circle className="egg-core" r={26} fill="url(#egg-core)" />
          <g className="egg-lines">
            {lines.map((l, i) => (
              <path key={i} d={l.d} pathLength={1} className="egg-line" style={{ ["--delay" as string]: `${l.delay}ms` }} />
            ))}
          </g>
          <g filter="url(#egg-glow)">
            {stars.map((s, i) => (
              <circle
                key={i}
                cx={s.x}
                cy={s.y}
                r={s.r}
                className={`egg-star${s.tip ? " tip" : ""}`}
                style={{ ["--fx" as string]: `${s.fx}px`, ["--fy" as string]: `${s.fy}px`, ["--delay" as string]: `${s.delay}ms` }}
              />
            ))}
            <circle className="egg-star egg-centre" r={4.2} style={{ ["--fx" as string]: "0px", ["--fy" as string]: "-240px", ["--delay" as string]: "700ms" }} />
          </g>
        </g>
      </svg>

      <div className="egg-caption">
        <div className="micro egg-kicker">Survey alert · new object in the field</div>
        <h1 className="egg-title">Planet X found.</h1>
        <p className="egg-sub">It was Claude all along.</p>
        <div className="egg-data mono">α Claudii · RA 13ʰ37ᵐ · Dec +42° · mag −∞ · spectral class: helpful</div>
        <div className="micro egg-hint">click anywhere or press Esc to return to the sky</div>
      </div>
    </div>,
    document.body,
  );
}
