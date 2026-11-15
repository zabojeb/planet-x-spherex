/** The field centre as an always-visible pair of coordinates under the search box, on the flat sky and in 3D.
 *  Click it (or press C), type or paste a position in any format the Go to panel takes (h m s / ° ′ ″, colons,
 *  degrees, a J-name, Galactic l b), Enter goes there with the same field of view, Escape gives up. */
import { ArrowRight, LocateFixed, Pencil } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { formatDec, formatDeg, formatRA, parseCoordinates, toGalactic, type CoordFrame } from "../astro";
import { useDismiss } from "../hooks";
import type { Place } from "./GoTo";
import { Segmented } from "./ui/primitives";

const signed = (x: number, d: number) => `${x >= 0 ? "+" : ""}${x.toFixed(d)}`;

function asText(ra: number, dec: number, frame: CoordFrame): string {
  if (frame === "icrs") return `${formatRA(ra)} ${formatDec(dec)}`;
  const [l, b] = toGalactic(ra, dec);
  return `${l.toFixed(4)} ${signed(b, 4)}`;
}

export function CoordField(p: { centre: [number, number] | null; onGo: (place: Place) => void }) {
  const [editing, setEditing] = useState(false);
  const [frame, setFrame] = useState<CoordFrame>("icrs");
  const [text, setText] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  const open = useCallback(() => {
    if (!p.centre) return;
    setText(asText(...p.centre, frame));
    setEditing(true);
  }, [p.centre, frame]);
  const cancel = useCallback(() => setEditing(false), []);
  useDismiss(box, editing, cancel);
  useLayoutEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);

  // C opens the field from anywhere but a text box
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(el?.tagName ?? "") || el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey || e.key.toLowerCase() !== "c") return;
      e.preventDefault();
      open();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const parsed = useMemo(
    () => (text.trim() ? parseCoordinates(text, frame) ?? { ok: false as const, error: "Not coordinates: type a name in the search box instead" } : null),
    [text, frame],
  );
  const go = () => {
    if (!parsed?.ok) return;
    const [l, b] = toGalactic(parsed.ra, parsed.dec);
    const name = parsed.frame === "galactic" ? `l=${+l.toFixed(4)} b=${+b.toFixed(4)}` : null;
    p.onGo({ name, ra: parsed.ra, dec: parsed.dec, pmra: 0, pmdec: 0, ref_epoch: 2000 });
    setEditing(false);
  };
  const switchFrame = (f: CoordFrame) => {
    if (parsed?.ok) setText(asText(parsed.ra, parsed.dec, f)); // same position in the other frame
    setFrame(f);
    input.current?.focus();
  };

  if (!editing) {
    const gal = p.centre ? toGalactic(...p.centre) : null;
    return (
      <button className="coordfield glass" onClick={open} disabled={!p.centre} title="Field centre: click or press C to type new coordinates">
        <LocateFixed size={14} className="cf-icon" aria-hidden />
        {p.centre && frame === "icrs" && (
          <>
            <span><i>α</i>{formatRA(p.centre[0])}</span>
            <span><i>δ</i>{formatDec(p.centre[1])}</span>
          </>
        )}
        {gal && frame === "galactic" && (
          <>
            <span><i>l</i>{gal[0].toFixed(4)}°</span>
            <span><i>b</i>{signed(gal[1], 4)}°</span>
          </>
        )}
        {!p.centre && <span className="muted">Coordinates</span>}
        {/* edit hint: outside the pill, to its right, shown on hover/focus; takes no room in the pill */}
        <span className="cf-hint" aria-hidden>
          <Pencil size={12} className="cf-edit" />
          <kbd className="kbd cf-kbd">C</kbd>
        </span>
      </button>
    );
  }

  const gal = parsed?.ok ? toGalactic(parsed.ra, parsed.dec) : null;
  return (
    <div className="coordfield-wrap" ref={box}>
      <form
        className="coordfield editing glass"
        onSubmit={(e) => {
          e.preventDefault();
          go();
        }}
      >
        <LocateFixed size={14} className="cf-icon" aria-hidden />
        <input
          ref={input}
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          autoComplete="off"
          aria-label={frame === "icrs" ? "Field centre, RA and Dec" : "Field centre, Galactic l and b"}
          aria-invalid={parsed ? !parsed.ok : undefined}
          placeholder={frame === "icrs" ? "17:57:48.5 +04:41:36 or 269.452 4.693" : "31.0087 +14.0827"}
        />
        <button type="submit" className="cf-go" disabled={!parsed?.ok} title="Go there (Enter)">
          <ArrowRight size={14} />
        </button>
      </form>
      <div className="coordfield-pop glass" role="group" aria-label="Coordinate frame and preview">
        <Segmented<CoordFrame>
          label="Coordinate frame"
          size="sm"
          value={frame}
          onChange={switchFrame}
          options={[
            { value: "icrs", label: "RA / Dec" },
            { value: "galactic", label: "Galactic l / b" },
          ]}
        />
        <div className={`cf-preview mono${parsed && !parsed.ok ? " err" : ""}`} aria-live="polite">
          {!parsed ? (
            <span className="muted">h m s, ° ′ ″, colons or degrees; a J-name works too</span>
          ) : parsed.ok ? (
            <>
              <span>{formatRA(parsed.ra)} {formatDec(parsed.dec)}</span>
              <span className="muted">{formatDeg(parsed.ra, 5)} {formatDeg(parsed.dec, 5, true)} · l {gal && formatDeg(gal[0], 2)} b {gal && formatDeg(gal[1], 2, true)}</span>
            </>
          ) : (
            <span>{parsed.error}</span>
          )}
        </div>
        <div className="cf-keys muted">
          <span><kbd className="kbd">↵</kbd> go, same field of view</span>
          <span><kbd className="kbd">Esc</kbd> cancel</span>
        </div>
      </div>
    </div>
  );
}
