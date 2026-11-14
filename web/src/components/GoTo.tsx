/** "Go to" panel: type a position in ICRS or Galactic coordinates (sexagesimal or degrees, optionally with a
 *  proper motion), or pick it on an all-sky map; recent places are one click away. */
import { ArrowRight, ChevronDown, Clock, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { formatDec, formatDeg, formatRA, fromEcliptic, fromGalactic, normRa, parseCoordinates, toGalactic, type CoordFrame } from "../astro";
import { safeStorage } from "../hooks";
import type { Target } from "../types";
import { Segmented } from "./ui/primitives";

export interface Place {
  name: string | null;
  ra: number;
  dec: number;
  pmra: number;
  pmdec: number;
  ref_epoch: number;
  preset?: string | null;
}

const RECENT_KEY = "spherex-recent-places";

export function readRecent(): Place[] {
  try {
    const v = JSON.parse(safeStorage(RECENT_KEY) || "[]");
    return Array.isArray(v) ? v.filter((p) => Number.isFinite(p?.ra) && Number.isFinite(p?.dec)) : [];
  } catch {
    return [];
  }
}

/** Remember a place (most recent first, the same position only once). */
export function rememberPlace(place: Place): void {
  const same = (a: Place) => Math.abs(a.ra - place.ra) < 1e-4 && Math.abs(a.dec - place.dec) < 1e-4;
  safeStorage(RECENT_KEY, JSON.stringify([place, ...readRecent().filter((p) => !same(p))].slice(0, 8)));
}

// ---------------------------------------------------------------- all-sky map (Hammer–Aitoff, RA grows to the left)

const MW = 288;
const MH = 144;
const SQ2 = Math.SQRT2;

function hammer(ra: number, dec: number): [number, number] {
  const lon = ((((180 - normRa(ra)) % 360) + 360) % 360) - 180; // = −RA in (−180°, 180°]: 0h in the middle, RA grows to the left
  const l = (lon * Math.PI) / 180;
  const b = (dec * Math.PI) / 180;
  const z = Math.sqrt(1 + Math.cos(b) * Math.cos(l / 2));
  const x = (2 * SQ2 * Math.cos(b) * Math.sin(l / 2)) / z;
  const y = (SQ2 * Math.sin(b)) / z;
  return [MW / 2 + (x / (2 * SQ2)) * (MW / 2 - 2), MH / 2 - (y / SQ2) * (MH / 2 - 2)];
}

function unhammer(px: number, py: number): [number, number] | null {
  const x = ((px - MW / 2) / (MW / 2 - 2)) * 2 * SQ2;
  const y = (-(py - MH / 2) / (MH / 2 - 2)) * SQ2;
  if ((x / (2 * SQ2)) ** 2 + (y / SQ2) ** 2 > 1) return null;
  const z = Math.sqrt(1 - (x / 4) ** 2 - (y / 2) ** 2);
  const lon = (2 * Math.atan2(z * x, 2 * (2 * z * z - 1)) * 180) / Math.PI;
  const dec = (Math.asin(Math.max(-1, Math.min(1, z * y))) * 180) / Math.PI;
  return [normRa(-lon), dec];
}

/** A curve as an SVG path, broken where it wraps around the map edge. */
function curve(pts: [number, number][]): string {
  let d = "";
  let prev: [number, number] | null = null;
  for (const [ra, dec] of pts) {
    const [x, y] = hammer(ra, dec);
    d += prev && Math.abs(x - prev[0]) < MW / 3 ? ` L${x.toFixed(1)},${y.toFixed(1)}` : ` M${x.toFixed(1)},${y.toFixed(1)}`;
    prev = [x, y];
  }
  return d;
}

const range = (n: number, f: (i: number) => [number, number]) => Array.from({ length: n + 1 }, (_, i) => f(i));
const GRATICULE = [
  ...[-60, -30, 0, 30, 60].map((dec) => curve(range(72, (i) => [i * 5 - 0.001, dec]))),
  ...[0, 3, 6, 9, 12, 15, 18, 21].map((h) => curve(range(36, (i) => [h * 15 + 0.001, -90 + i * 5]))),
].join("");
const GAL_PLANE = curve(range(180, (i) => fromGalactic(i * 2, 0)));
const ECLIPTIC = curve(range(180, (i) => fromEcliptic(i * 2, 0)));
const GAL_CENTRE = fromGalactic(0, 0);
const AXIS = [0, 6, 18].map((h) => {
  const [x, y] = hammer(h * 15, 0);
  return { t: `${h}h`, x, y: y + 9 };
});
const DEEP_FIELDS: { name: string; at: [number, number] }[] = [
  { name: "SPHEREx deep field (north ecliptic pole)", at: fromEcliptic(0, 90) },
  { name: "SPHEREx deep field (south ecliptic pole)", at: fromEcliptic(0, -90) },
];

function SkyMap(p: { current: [number, number] | null; picked: [number, number] | null; presets: Target[]; recent: Place[]; onPick: (ra: number, dec: number) => void }) {
  const [hover, setHover] = useState<[number, number] | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const at = (e: React.PointerEvent | React.MouseEvent) => {
    const b = svg.current!.getBoundingClientRect();
    return unhammer(((e.clientX - b.left) / b.width) * MW, ((e.clientY - b.top) / b.height) * MH);
  };
  const dot = (ra: number, dec: number) => hammer(ra, dec);
  const gc = dot(...GAL_CENTRE);
  return (
    <div className="skymap">
      <svg
        ref={svg}
        viewBox={`0 0 ${MW} ${MH}`}
        width="100%"
        role="img"
        aria-label="All-sky map in equatorial coordinates; click to choose a position"
        onPointerMove={(e) => setHover(at(e))}
        onPointerLeave={() => setHover(null)}
        onClick={(e) => {
          const w = at(e);
          if (w) p.onPick(Number(w[0].toFixed(4)), Number(w[1].toFixed(4)));
        }}
      >
        <ellipse cx={MW / 2} cy={MH / 2} rx={MW / 2 - 2} ry={MH / 2 - 2} className="map-sky" />
        <path d={GRATICULE} className="map-grat" />
        <path d={GAL_PLANE} className="map-gal" />
        <path d={ECLIPTIC} className="map-ecl" />
        <circle cx={gc[0]} cy={gc[1]} r={2.5} className="map-gc"><title>Galactic centre</title></circle>
        {DEEP_FIELDS.map((f) => {
          const [x, y] = dot(...f.at);
          return <rect key={f.name} x={x - 2.5} y={y - 2.5} width={5} height={5} className="map-deep"><title>{f.name}</title></rect>;
        })}
        {p.recent.map((r, i) => {
          const [x, y] = dot(r.ra, r.dec);
          return <circle key={`r${i}`} cx={x} cy={y} r={1.6} className="map-recent"><title>{r.name || `${formatRA(r.ra)} ${formatDec(r.dec)}`}</title></circle>;
        })}
        {p.presets.map((t) => {
          const [x, y] = dot(t.ra, t.dec);
          return (
            <circle key={t.id ?? t.name} cx={x} cy={y} r={2.4} className="map-preset" onClick={(e) => { e.stopPropagation(); p.onPick(t.ra, t.dec); }}>
              <title>{t.name}</title>
            </circle>
          );
        })}
        {p.picked && (() => {
          const [x, y] = dot(...p.picked);
          return <g className="map-picked" transform={`translate(${x} ${y})`}><line x1={-6} x2={6} /><line y1={-6} y2={6} /></g>;
        })()}
        {p.current && (() => {
          const [x, y] = dot(...p.current);
          return <circle cx={x} cy={y} r={4.5} className="map-current"><title>Current field</title></circle>;
        })()}
        {AXIS.map((a) => (
          <text key={a.t} x={a.x} y={a.y} textAnchor="middle" className="map-axis">{a.t}</text>
        ))}
      </svg>
      <div className="skymap-foot mono">
        {hover ? (
          <span>{formatRA(hover[0]).slice(0, 8)} {formatDec(hover[1]).slice(0, 6)} · l {formatDeg(toGalactic(...hover)[0], 1)} b {formatDeg(toGalactic(...hover)[1], 1, true)}</span>
        ) : (
          <span className="map-legend">
            <i className="lg-gal" /> Milky Way <i className="lg-ecl" /> ecliptic <i className="lg-deep" /> deep fields <i className="lg-cur" /> here
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- the panel

const NOT_COORDS = { ok: false as const, error: "Not coordinates: numbers only (h m s, ° ′ ″ or degrees)" };
/** "RA Dec" in a single box: a signed Dec after the RA, a J-designation, or two decimal numbers. */
const PAIR = /\S[\s,]+[+-]\d|^J\d|^[+-]?\d+\.\d+[\s,]+[+-]?\d+\.\d+$/i;

export function GoToPanel(p: {
  current: [number, number] | null;
  currentName: string;
  presets: Target[];
  fov: number;
  onGo: (place: Place) => void;
  onPreset: (id: string) => void;
  close?: () => void;
}) {
  const [frame, setFrame] = useState<CoordFrame>("icrs");
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [pmOpen, setPmOpen] = useState(false);
  const [pmra, setPmra] = useState("");
  const [pmdec, setPmdec] = useState("");
  const [epoch, setEpoch] = useState("2000");
  const [recent, setRecent] = useState<Place[]>(() => readRecent());

  const parsed = useMemo(() => {
    const ta = a.trim();
    const tb = b.trim();
    if (!ta && !tb) return null;
    if (!ta || !tb) {
      // the whole pair typed into one box; anything less clear is still a half-typed RA or Dec
      const one = ta || tb;
      return PAIR.test(one) ? parseCoordinates(one, frame) ?? NOT_COORDS : null;
    }
    const bb = /^[+-]/.test(tb) ? tb : `+${tb}`; // an explicit sign tells the parser where Dec starts
    return parseCoordinates(`${ta} ${bb}`, frame) ?? NOT_COORDS;
  }, [a, b, frame]);
  const pm = { pmra: Number(pmra) || 0, pmdec: Number(pmdec) || 0, ref_epoch: Number(epoch) || 2000 };
  const pmBad = pmOpen && ([pmra, pmdec].some((v) => v.trim() !== "" && !Number.isFinite(Number(v))) || !Number.isFinite(Number(epoch)));

  const fill = (ra: number, dec: number) => {
    if (frame === "galactic") {
      const [l, bb] = toGalactic(ra, dec);
      setA(l.toFixed(4));
      setB(`${bb >= 0 ? "+" : ""}${bb.toFixed(4)}`);
    } else {
      setA(formatRA(ra));
      setB(formatDec(dec));
    }
  };

  // Pasting "RA Dec" into either box fills both.
  const onPaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData("text");
    const r = parseCoordinates(text, frame);
    if (r?.ok && text.trim().split(/[\s,]+/).length >= 2) {
      e.preventDefault();
      if (frame === "galactic" || !r.sexagesimal) {
        const [x, y] = frame === "galactic" ? toGalactic(r.ra, r.dec) : [r.ra, r.dec];
        setA(String(Number(x.toFixed(6))));
        setB(`${y >= 0 ? "+" : ""}${Number(y.toFixed(6))}`);
      } else fill(r.ra, r.dec);
    }
  };

  const go = () => {
    if (!parsed?.ok || pmBad) return;
    const gl = toGalactic(parsed.ra, parsed.dec);
    const name = frame === "galactic" ? `l=${+gl[0].toFixed(4)} b=${+gl[1].toFixed(4)}` : null;
    p.onGo({ name, ra: parsed.ra, dec: parsed.dec, ...(pmOpen ? pm : { pmra: 0, pmdec: 0, ref_epoch: 2000 }) });
    p.close?.();
  };

  const gal = parsed?.ok ? toGalactic(parsed.ra, parsed.dec) : null;
  return (
    <div className="goto">
      <div className="micro menu-title">Go to coordinates</div>
      <form
        className="goto-form"
        onSubmit={(e) => {
          e.preventDefault();
          go();
        }}
      >
        <Segmented<CoordFrame>
          label="Coordinate frame"
          size="sm"
          value={frame}
          onChange={(f) => {
            if (parsed?.ok) {
              // keep the same position when switching frames
              const pos: [number, number] = [parsed.ra, parsed.dec];
              setFrame(f);
              if (f === "galactic") {
                const [l, bb] = toGalactic(...pos);
                setA(l.toFixed(4));
                setB(`${bb >= 0 ? "+" : ""}${bb.toFixed(4)}`);
              } else {
                setA(formatRA(pos[0]));
                setB(formatDec(pos[1]));
              }
            } else setFrame(f);
          }}
          options={[
            { value: "icrs", label: "RA / Dec (ICRS)" },
            { value: "galactic", label: "Galactic l / b" },
          ]}
        />
        <div className="goto-inputs">
          <label>
            <span className="micro">{frame === "icrs" ? "RA" : "l"}</span>
            <input value={a} onChange={(e) => setA(e.target.value)} onPaste={onPaste} placeholder={frame === "icrs" ? "17:57:48.5 or 269.452" : "31.0087"} spellCheck={false} autoComplete="off" aria-label={frame === "icrs" ? "Right ascension" : "Galactic longitude"} />
          </label>
          <label>
            <span className="micro">{frame === "icrs" ? "Dec" : "b"}</span>
            <input value={b} onChange={(e) => setB(e.target.value)} onPaste={onPaste} placeholder={frame === "icrs" ? "+04:41:36 or 4.693" : "+14.0827"} spellCheck={false} autoComplete="off" aria-label={frame === "icrs" ? "Declination" : "Galactic latitude"} />
          </label>
        </div>
        <div className={`goto-preview mono${parsed && !parsed.ok ? " err" : ""}`} aria-live="polite">
          {!parsed ? (
            <span className="muted">h m s / ° ′ ″, colons or decimal degrees; or the whole “RA Dec” in one box</span>
          ) : parsed.ok ? (
            <>
              <span>{formatRA(parsed.ra)} {formatDec(parsed.dec)}</span>
              <span className="muted">
                {formatDeg(parsed.ra, 5)} {formatDeg(parsed.dec, 5, true)} · l {gal && formatDeg(gal[0], 2)} b {gal && formatDeg(gal[1], 2, true)}
              </span>
            </>
          ) : (
            <span>{parsed.error}</span>
          )}
        </div>
        <button type="button" className={`goto-more${pmOpen ? " open" : ""}`} onClick={() => setPmOpen((o) => !o)} aria-expanded={pmOpen}>
          <ChevronDown size={13} /> Proper motion {pmOpen ? "" : "(optional, for moving objects)"}
        </button>
        {pmOpen && (
          <div className="goto-pm">
            <label><span className="micro">μα* mas/yr</span><input value={pmra} onChange={(e) => setPmra(e.target.value)} placeholder="0" inputMode="decimal" /></label>
            <label><span className="micro">μδ mas/yr</span><input value={pmdec} onChange={(e) => setPmdec(e.target.value)} placeholder="0" inputMode="decimal" /></label>
            <label><span className="micro">epoch</span><input value={epoch} onChange={(e) => setEpoch(e.target.value)} placeholder="2000" inputMode="decimal" /></label>
          </div>
        )}
        <div className="goto-go">
          <span className="muted small">{p.fov}′ field</span>
          <button type="submit" className="btn primary" disabled={!parsed?.ok || pmBad}>
            Go <ArrowRight size={14} />
          </button>
        </div>
      </form>

      <div className="micro menu-title goto-section">Sky map · click to choose</div>
      <SkyMap current={p.current} picked={parsed?.ok ? [parsed.ra, parsed.dec] : null} presets={p.presets} recent={recent} onPick={fill} />

      {recent.length > 0 && (
        <>
          <div className="micro menu-title goto-section goto-recent-head">
            <span><Clock size={11} /> Recent</span>
            <button className="btn ghost xs-text" onClick={() => { safeStorage(RECENT_KEY, "[]"); setRecent([]); }} title="Forget recent places">
              <X size={11} /> clear
            </button>
          </div>
          <div className="goto-recent">
            {recent.map((r, i) => (
              <button
                key={i}
                className="recent-row"
                onClick={() => {
                  if (r.preset) p.onPreset(r.preset);
                  else p.onGo(r);
                  p.close?.();
                }}
              >
                <span className="recent-name">{r.name || "Field"}</span>
                <span className="recent-pos mono">{formatRA(r.ra).slice(0, 8)} {formatDec(r.dec).slice(0, 6)}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
