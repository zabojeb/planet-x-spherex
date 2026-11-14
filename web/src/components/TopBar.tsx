/** Floating top-left cluster: serif wordmark, the search pill and the field of view. Plus the share panel. */
import { Check, Code2, Download, Link2 } from "lucide-react";
import { useState } from "react";
import type { Target } from "../types";
import type { ExportKind } from "../api";
import { CommandSearch } from "./CommandSearch";

const FOVS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 30];

export function Wordmark() {
  return (
    <a className="wordmark" href="./" aria-label="Planet X × SPHEREx home">
      Planet X <span className="wordmark-x">×</span> SPHEREx
    </a>
  );
}

export function TopLeft(p: {
  presets: Target[];
  activeId: string | null;
  busy: boolean;
  searchError: string | null;
  fov: number;
  compact: boolean;
  onSearch: (q: string) => void;
  onPreset: (id: string) => void;
  onFov: (fov: number) => void;
}) {
  const fovs = FOVS.includes(p.fov) ? FOVS : [...FOVS, p.fov].sort((a, b) => a - b);
  return (
    <div className="topleft">
      <Wordmark />
      <div className="search-row">
        <CommandSearch presets={p.presets} activeId={p.activeId} busy={p.busy} error={p.searchError} onSearch={p.onSearch} onPreset={p.onPreset} compact={false} />
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
      </div>
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
