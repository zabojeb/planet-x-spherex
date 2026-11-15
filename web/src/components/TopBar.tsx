/** Floating top-left cluster: serif wordmark, the search pill, the field of view and Random. Plus the share panel. */
import { Check, ChevronDown, Code2, Dices, Download, Link2 } from "lucide-react";
import { useCallback, useRef, useState, type ReactNode } from "react";
import type { ExportKind } from "../api";
import { useDismiss } from "../hooks";
import { FACETS, type Facet, type Pick, type Recent, type SkyObject } from "../search";
import { CommandSearch } from "./CommandSearch";

const FOVS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 30];

export function Wordmark() {
  return (
    <a className="wordmark" href="./" aria-label="Seek In Space home">
      Seek <span className="wordmark-x">In</span> Space
    </a>
  );
}

export function TopLeft(p: {
  space: SkyObject[];
  indexReady: boolean;
  current: string | null;
  recents: Recent[];
  fov: number;
  compact: boolean;
  onPick: (pick: Pick, recent: Recent) => void;
  onRandom: (what: Facet | "field" | null) => void;
  onWarm: () => void;
  onMoving?: (name: string) => void;
  onFov: (fov: number) => void;
  /** The editable field-centre coordinates, under the search row. */
  coords?: ReactNode;
}) {
  const fovs = FOVS.includes(p.fov) ? FOVS : [...FOVS, p.fov].sort((a, b) => a - b);
  return (
    <div className="topleft">
      <Wordmark />
      <div className="search-row">
        <CommandSearch space={p.space} indexReady={p.indexReady} current={p.current} recents={p.recents} onPick={p.onPick} onRandom={p.onRandom} onWarm={p.onWarm} onMoving={p.onMoving} />
        <label className="fov glass" title="Field of view. Larger fields show faster movers but load more data.">
          <span className="micro">FOV</span>
          <select value={p.fov} onChange={(e) => p.onFov(Number(e.target.value))} aria-label="Field of view in arcminutes">
            {fovs.map((f) => (
              <option key={f} value={f}>
                {f}′
              </option>
            ))}
          </select>
        </label>
        <RandomButton compact={p.compact} onRandom={p.onRandom} onWarm={p.onWarm} />
      </div>
      {p.coords}
    </div>
  );
}

// What the Random menu offers besides "anything" and "a patch of sky".
const RANDOM_FACETS = ["brown-dwarf", "high-pm", "variable", "young", "galaxy", "nebula", "cluster"]
  .map((id) => FACETS.find((f) => f.id === id))
  .filter((f): f is Facet => !!f);

/** Split button: Random picks an object or a patch of sky; the caret picks what kind. */
export function RandomButton({ compact, onRandom, onWarm }: { compact: boolean; onRandom: (what: Facet | "field" | null) => void; onWarm: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  const go = (what: Facet | "field" | null) => {
    setOpen(false);
    onRandom(what);
  };
  return (
    <div className="random glass" ref={ref} onPointerEnter={onWarm}>
      <button className="random-main" onClick={() => go(null)} title="Explore something at random: an object from the index or a patch of sky">
        <Dices size={16} aria-hidden />
        {!compact && <span>Random</span>}
        {compact && <span className="sr-only">Random</span>}
      </button>
      <button className="random-more" aria-label="Choose what to pick at random" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <ChevronDown size={14} aria-hidden />
      </button>
      {open && (
        <div className="popover left random-menu" role="menu" aria-label="Random">
          <button role="menuitem" className="menu-item" onClick={() => go(null)}>
            <span>
              Anything
              <span className="menu-hint">An object from the index, sometimes a patch of sky</span>
            </span>
          </button>
          <button role="menuitem" className="menu-item" onClick={() => go("field")}>
            <span>
              Patch of sky
              <span className="menu-hint">A uniformly random position</span>
            </span>
          </button>
          <div className="micro menu-title">Random object of a kind</div>
          <div className="random-kinds">
            {RANDOM_FACETS.map((f) => (
              <button key={f.id} role="menuitem" className="chip" onClick={() => go(f)}>
                {f.one}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const EXPORTS: { kind: ExportKind; label: string; hint: string }[] = [
  { kind: "coadds.fits", label: "Co-adds", hint: "FITS" },
  { kind: "photometry.csv", label: "Photometry", hint: "CSV" },
  { kind: "photometry.vot", label: "Photometry", hint: "VOT" },
  { kind: "download.sh", label: "Raw files", hint: "curl" },
];

export function SharePanel({ link, embedLink, exportUrl }: { link: () => string; embedLink: () => string; exportUrl?: (k: ExportKind) => string }) {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(what);
    } catch {
      setCopied("blocked");
    }
    setTimeout(() => setCopied(null), 1600);
  };
  const snippet = `<iframe src="${embedLink()}" width="560" height="640" style="border:0" loading="lazy" title="Seek In Space"></iframe>`;
  return (
    <div className="menu">
      <div className="micro menu-title">Share</div>
      <button className="menu-item" onClick={() => copy(link(), "link")}>
        {copied === "link" ? <Check size={16} /> : <Link2 size={16} />}
        <span>
          Copy link
          <span className="menu-hint">The URL reproduces this exact view</span>
        </span>
      </button>
      <button className="menu-item" onClick={() => copy(snippet, "embed")}>
        {copied === "embed" ? <Check size={16} /> : <Code2 size={16} />}
        <span>
          Copy embed code
          <span className="menu-hint">An iframe with only the viewer</span>
        </span>
      </button>
      <textarea className="snippet" readOnly rows={3} value={snippet} onFocus={(e) => e.currentTarget.select()} aria-label="Embed code" />
      {copied === "blocked" && <p className="menu-hint">Clipboard blocked: select the text and copy it.</p>}
      {exportUrl && (
        <>
          <div className="micro menu-title" style={{ marginTop: 10 }}>Download</div>
          <div className="exports">
            {EXPORTS.map((x) => (
              <a key={x.kind} className="btn ghost" href={exportUrl(x.kind)} download>
                <Download size={14} /> {x.label} <small>{x.hint}</small>
              </a>
            ))}
          </div>
          <p className="menu-hint">The script fetches the original Level 2 exposures (one curl per file).</p>
        </>
      )}
    </div>
  );
}
