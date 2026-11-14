/** Serif wordmark, command search, field of view, a mono status readout and the share menu. */
import { Check, Code2, Link2, Share2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { Target } from "../types";
import { CommandSearch } from "./CommandSearch";
import { Popover } from "./ui/primitives";

const FOVS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 30];

interface Props {
  presets: Target[];
  activeId: string | null;
  busy: boolean;
  searchError: string | null;
  fov: number;
  compact: boolean;
  releases: string | null;
  onSearch: (q: string) => void;
  onPreset: (id: string) => void;
  onFov: (fov: number) => void;
  link: () => string;
  embedLink: () => string;
}

export function Wordmark({ compact }: { compact?: boolean }) {
  return (
    <a className="wordmark" href="./" aria-label="Planet X × SPHEREx home">
      <span className="wordmark-text">
        Planet X <span className="wordmark-x">×</span> SPHEREx
      </span>
      {!compact && <span className="wordmark-sub">Time-domain viewer</span>}
    </a>
  );
}

function UtcClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 30000);
    return () => window.clearInterval(t);
  }, []);
  return <span>{now.toISOString().slice(0, 16).replace("T", " ")} UTC</span>;
}

function ShareMenu({ link, embedLink }: { link: () => string; embedLink: () => string }) {
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
  return (
    <Popover label={<Share2 size={16} />} title="Share this view" width={340}>
      {() => {
        const snippet = `<iframe src="${embedLink()}" width="560" height="640" style="border:0" loading="lazy" title="SPHEREx viewer"></iframe>`;
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
          </div>
        );
      }}
    </Popover>
  );
}

export function TopBar(p: Props) {
  const fovs = FOVS.includes(p.fov) ? FOVS : [...FOVS, p.fov].sort((a, b) => a - b);
  return (
    <header className="topbar">
      <Wordmark compact={p.compact} />
      <div className="topbar-center">
        <CommandSearch presets={p.presets} activeId={p.activeId} busy={p.busy} error={p.searchError} onSearch={p.onSearch} onPreset={p.onPreset} compact={p.compact} />
        <label className="fov" title="Field of view. Larger fields show faster movers but take longer to load.">
          <span className="micro">FOV</span>
          <select value={p.fov} onChange={(e) => p.onFov(Number(e.target.value))} aria-label="Field of view in arcminutes">
            {fovs.map((f) => (
              <option key={f} value={f}>
                {f}′
              </option>
            ))}
          </select>
        </label>
      </div>
      {!p.compact && (
        <div className="status-line" aria-hidden>
          <span className="status-dot" />
          <span>IRSA{p.releases ? ` · ${p.releases}` : ""}</span>
          <UtcClock />
        </div>
      )}
      <ShareMenu link={p.link} embedLink={p.embedLink} />
    </header>
  );
}
