/** Side panel as tabs: Overview · Epoch · Flags · Data. Everything is read from meta. */
import { ExternalLink } from "lucide-react";
import { useState } from "react";
import { formatBytes, formatDate, formatDec, formatPm, formatRA, mjdToYear } from "../astro";
import { FLAG_OVERLAY } from "../colors";
import { wlRange } from "../display";
import type { BandSel } from "../render";
import { MODES, type Mode } from "../state";
import type { Meta, Target } from "../types";
import { Switch, Tabs } from "./ui/primitives";

type Tab = "overview" | "epoch" | "flags" | "data";

interface Props {
  meta: Meta;
  target: Target | null;
  epoch: number;
  band: BandSel;
  mode: Mode;
  flagBits: number[];
  onFlagBits: (bits: number[]) => void;
  flagsOn: boolean;
  onFlagsOn: (on: boolean) => void;
}

function topFlags(counts: Record<string, number>, masked: Set<string>): string {
  return Object.entries(counts)
    .filter(([k]) => k !== "SOURCE")
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([k, v]) => `${k.toLowerCase()}${masked.has(k) ? "*" : ""} ${v}`)
    .join(", ");
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="stat">
      <div className="micro">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function SidePanel(p: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div className="side-panel">
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "overview", label: "Overview" },
          { value: "epoch", label: "Epoch" },
          { value: "flags", label: "Flags" },
          { value: "data", label: "Data" },
        ]}
      />
      <div className="tab-body">
        {tab === "overview" && <Overview {...p} />}
        {tab === "epoch" && <EpochTab {...p} />}
        {tab === "flags" && <FlagsTab {...p} />}
        {tab === "data" && <DataTab {...p} />}
      </div>
    </div>
  );
}

function Overview({ meta, target, epoch, mode }: Props) {
  const ep = meta.epochs[epoch];
  const tr = meta.target.track.find((t) => t.epoch === epoch) ?? meta.target.track[0];
  const name = target?.name || meta.target.name || "Custom position";
  const pm = Math.hypot(meta.target.pmra, meta.target.pmdec);
  const first = meta.epochs[0];
  const last = meta.epochs[meta.epochs.length - 1];
  const baseline = first && last ? mjdToYear(last.mjd_mean) - mjdToYear(first.mjd_mean) : 0;
  const shift = (pm * baseline) / 1000; // arcsec over the SPHEREx baseline
  const partial = meta.epochs.filter((e) => e.partial).length;
  return (
    <div className="overview">
      <h2 className="target-name">{name}</h2>
      {target?.description && <p className="target-desc">{target.description}</p>}
      <div className="stats">
        <Stat label={`Position ${ep ? formatDate(ep.mjd_mean).slice(0, 7) : ""}`} value={<span className="num mono-ish">{formatRA(tr.ra)}<br />{formatDec(tr.dec)}</span>} />
        <Stat
          label="Proper motion"
          value={formatPm(meta.target.pmra, meta.target.pmdec)}
          sub={pm >= 1 ? `${shift.toFixed(1)}″ = ${(shift / meta.grid.pixscale_arcsec).toFixed(1)} px over the data` : "static"}
        />
        {target?.parallax ? <Stat label="Distance" value={`${(1000 / target.parallax).toFixed(2)} pc`} sub={`parallax ${target.parallax.toFixed(1)} mas`} /> : null}
        <Stat label="Epochs" value={<span className="num">{meta.epochs.length}</span>} sub={`${baseline.toFixed(1)} yr baseline${partial ? ` · ${partial} partial` : ""}`} />
        <Stat label="Exposures" value={<span className="num">{meta.stats.frames_used}</span>} sub={`of ${meta.stats.frames_found} found`} />
        <Stat label="Field" value={`${meta.grid.fov_arcmin.toFixed(1)}′`} sub={`${meta.grid.n}² px of ${meta.grid.pixscale_arcsec}″`} />
      </div>
      <div className="howto">
        <div className="micro">How to read it · {MODES.find((m) => m.id === mode)?.label}</div>
        <ul>
          <li>The crosshair is where the target should be at the shown date; the dashed orange line is its track.</li>
          <li>Difference mode turns a mover into a blue/red pair; RGB makes cold brown dwarfs glow red.</li>
          <li>The spectrum and light curve are measured at the target in every exposure.</li>
        </ul>
        <p className="keys">
          <kbd className="kbd">Space</kbd> play <kbd className="kbd">←</kbd>
          <kbd className="kbd">→</kbd> epoch <kbd className="kbd">1</kbd>–<kbd className="kbd">6</kbd> detector <kbd className="kbd">0</kbd> all{" "}
          <kbd className="kbd">B</kbd>
          <kbd className="kbd">D</kbd>
          <kbd className="kbd">S</kbd>
          <kbd className="kbd">R</kbd>
          <kbd className="kbd">E</kbd> modes <kbd className="kbd">⌘K</kbd> search
        </p>
      </div>
      {target?.source && <p className="fine">Position and motion: {target.source}</p>}
    </div>
  );
}

function EpochTab({ meta, epoch, band }: Props) {
  const ep = meta.epochs[epoch];
  const masked = new Set(meta.flag_bits.filter((f) => f.masked).map((f) => f.name));
  const frames = meta.exposures.filter((x) => x.epoch === epoch && (band === "all" || x.band === band));
  if (!ep) return null;
  return (
    <div>
      <div className="epoch-head">
        <h2>
          Epoch {epoch + 1} <span className="muted">of {meta.epochs.length}</span>
        </h2>
        {ep.partial && <span className="badge">partial visit</span>}
      </div>
      <p className="sub num">
        {formatDate(ep.mjd_min)} → {formatDate(ep.mjd_max)} · {ep.n} exposures
      </p>
      <table className="tbl">
        <thead>
          <tr>
            <th>Detector</th>
            <th className="num">Frames</th>
            <th>Covered</th>
          </tr>
        </thead>
        <tbody>
          {meta.bands.map((b) => {
            const eb = ep.bands[b.id];
            return (
              <tr key={b.id} className={band === b.id ? "hl" : undefined}>
                <td>
                  {b.id} <span className="muted">{wlRange(b)}</span>
                </td>
                <td className="num">{eb?.n ?? "—"}</td>
                <td className="num muted">{eb ? `${eb.wl_min.toFixed(2)}–${eb.wl_max.toFixed(2)} µm` : "no data"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="micro section-label">Exposures {band === "all" ? "" : `· ${band}`}</div>
      <div className="scroll">
        <table className="tbl compact">
          <thead>
            <tr>
              <th>Date (UTC)</th>
              <th>Det</th>
              <th className="num">λ µm</th>
              <th>Release</th>
              <th title="Pixels with each flag; * = removed from images">Flags</th>
            </tr>
          </thead>
          <tbody>
            {frames.map((f) => (
              <tr key={f.index} title={f.file}>
                <td className="num nowrap">{f.date.slice(0, 16)}</td>
                <td>{f.band}</td>
                <td className="num">{f.wl.toFixed(3)}</td>
                <td className="muted">{f.collection.replace("spherex_", "")}</td>
                <td className="muted">{topFlags(f.flag_counts, masked)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FlagsTab({ meta, flagBits, onFlagBits, flagsOn, onFlagsOn }: Props) {
  const toggle = (b: number) => onFlagBits(flagBits.includes(b) ? flagBits.filter((x) => x !== b) : [...flagBits, b].sort((a, c) => a - c));
  return (
    <div>
      <Switch
        checked={flagsOn}
        onChange={onFlagsOn}
        label="Show flags on the image"
        hint={
          <>
            Checked flags are painted <span style={{ color: FLAG_OVERLAY }}>■</span> red, combined over the epoch.
          </>
        }
      />
      <p className="fine">"Removed" flags are already masked out of the images; "photometry" flags make a measurement unreliable.</p>
      <ul className="flag-list">
        {meta.flag_bits.map((f) => (
          <li key={f.bit}>
            <label className="flag-row" title={f.description}>
              <input type="checkbox" checked={flagBits.includes(f.bit)} onChange={() => toggle(f.bit)} />
              <span className="flag-name">{f.name}</span>
              <span className="flag-tags">
                {f.masked && <span className="tag">removed</span>}
                {f.phot_bad && <span className="tag warn">photometry</span>}
              </span>
            </label>
            <div className="flag-desc">{f.description}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DataTab({ meta }: Props) {
  const secs = meta.stats.seconds ?? {};
  return (
    <div className="data-tab">
      <dl className="kv">
        <dt>Archive</dt>
        <dd>
          <a href="https://irsa.ipac.caltech.edu/Missions/spherex.html" target="_blank" rel="noreferrer">
            {meta.sources.archive} <ExternalLink size={12} />
          </a>
        </dd>
        <dt>Releases</dt>
        <dd>{meta.sources.collections.map((c) => c.replace("spherex_", "")).join(", ")}</dd>
        <dt>Exposures</dt>
        <dd className="num">
          {meta.stats.frames_used} used of {meta.stats.frames_found}
          {meta.stats.errors.length ? <span className="err"> · {meta.stats.errors.length} failed</span> : null}
        </dd>
        <dt>Grid</dt>
        <dd className="num">
          {meta.grid.n}×{meta.grid.n} px · {meta.grid.pixscale_arcsec}″/px · TAN, north up
        </dd>
        <dt>Built</dt>
        <dd className="num">
          {meta.created.replace("T", " ").slice(0, 16)} UTC
          {secs.total != null && <span className="muted"> · {secs.total.toFixed(0)} s</span>}
          {meta.stats.bytes ? <span className="muted"> · {formatBytes(meta.stats.bytes)}</span> : null}
        </dd>
        <dt>Pipeline</dt>
        <dd className="num">v{meta.version} · sequence {meta.id.slice(0, 8)}</dd>
      </dl>
      <p className="fine">
        Images: NASA SPHEREx Level 2 spectral images via NASA/IPAC IRSA, read straight from the public S3 archive. Overlays: Gaia DR3
        (ESA), CatWISE2020 and 2MASS (IRSA). This publication makes use of data products from SPHEREx, a joint project of JPL and
        Caltech funded by NASA.
      </p>
    </div>
  );
}
