/** Top-level wiring: target + field of view -> server build -> derived images -> stage, controls, panels and charts. */
import { ExternalLink } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchCatalog, fetchExposures, getTargets, loadSequence, resolveQuery, type Progress } from "./api";
import { formatDate, formatRange, mjdToYear, propagate } from "./astro";
import { ChartSections, ChartSkeletons } from "./components/chart/ChartsCard";
import { DetectorControl, DisplayPanel, LayersPanel, ModeControl, ModeHint, Toolbar, ViewerActions, type CatalogStatus } from "./components/Controls";
import { Hero, ProgressBar, SectionHead } from "./components/Progress";
import { SidePanel } from "./components/SidePanel";
import { Stage, type StageHud } from "./components/Stage";
import { TopBar, Wordmark } from "./components/TopBar";
import { Transport } from "./components/Transport";
import { BottomSheet } from "./components/ui/BottomSheet";
import { MicroLabel } from "./components/ui/primitives";
import { bandLabel, computeDisplay, effectiveRef, isPartial, playableEpochs, rgbLabel, stackEpochs, type ExposureView } from "./display";
import { safeStorage, useElementWidth, useMediaQuery, useViewportHeight } from "./hooks";
import { Derived, type BandSel } from "./render";
import { DEFAULT_VIEW, MODES, defaultFlagBits, parseUrl, viewLink, writeUrl, type TargetSpec, type ViewState } from "./state";
import type { CatalogSource, ExposureStack, Meta, SequenceData, Target } from "./types";

const INTRO_KEY = "spherex-intro-dismissed";
const CATALOG_IDS = ["gaia", "catwise", "2mass"] as const;

/** Default detector from the data: a single band only when the target is detected in just a few detectors. */
function bestBand(meta: Meta): BandSel {
  const snr: Record<string, number[]> = {};
  for (const p of meta.photometry) {
    if (p.flagged || p.flux_mjy == null || !p.err_mjy) continue;
    (snr[p.band] ??= []).push(p.flux_mjy / p.err_mjy);
  }
  const med = meta.bands.map((b) => {
    const a = (snr[b.id] ?? []).sort((x, y) => x - y);
    return { id: b.id, s: a.length ? a[Math.floor(a.length / 2)] : 0 };
  });
  const best = med.reduce((a, b) => (b.s > a.s ? b : a), med[0]);
  const faint = med.filter((m) => m.s < 3).length;
  return best && best.s > 5 && faint >= meta.bands.length / 2 ? best.id : "all";
}

/** Fit the view to a freshly loaded sequence. Fields given in the URL are kept on the first load;
 *  everything else falls back to the target's display hints or to what the data suggests. */
function normalizeView(v: ViewState, data: SequenceData, keep: boolean, target: Target | null, fromUrl: Partial<ViewState>): ViewState {
  const d = new Derived(data);
  let band = v.band;
  let mode = v.mode;
  if (!keep || fromUrl.band === undefined) band = target?.default_band ?? bestBand(data.meta);
  if (!keep || fromUrl.mode === undefined) {
    const hinted = MODES.find((m) => m.id === target?.default_mode);
    if (hinted) mode = hinted.id;
  }
  if (band !== "all" && !d.playable(band).length) band = "all";
  const playable = d.playable(band);
  const full = playable.filter((e) => !data.meta.epochs[e]?.partial);
  // Difference mode opens on the last visit (the first is the reference, which would show nothing).
  const start = mode === "diff" ? full[full.length - 1] : full[0];
  const keepEpoch = keep && fromUrl.epoch !== undefined && playable.includes(v.epoch);
  const epoch = keepEpoch ? v.epoch : start ?? playable[0] ?? 0;
  return { ...v, band, mode, epoch, ref: keep ? v.ref : -1, stack: keep ? v.stack : null };
}

export default function App() {
  const initial = useMemo(() => parseUrl(), []);
  const embed = initial.embed;
  const mobile = useMediaQuery("(max-width: 760px)");
  const vh = useViewportHeight();
  const [presets, setPresets] = useState<Target[]>([]);
  const [presetsError, setPresetsError] = useState<string | null>(null);
  const [spec, setSpec] = useState<TargetSpec>(initial.target ?? { kind: "preset", id: "barnard" });
  const [fovOverride, setFovOverride] = useState<number | null>(initial.fov);
  const [view, setView] = useState<ViewState>({ ...DEFAULT_VIEW, ...initial.view });
  const [seq, setSeq] = useState<SequenceData | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [playing, setPlaying] = useState(!initial.hasParams);
  const [hero, setHero] = useState(!initial.hasParams && !embed && safeStorage(INTRO_KEY) !== "1");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [catalogs, setCatalogs] = useState<Record<string, CatalogSource[]>>({});
  const [catalogStatus, setCatalogStatus] = useState<CatalogStatus>({});
  const [exp, setExp] = useState<{ key: string; stack: ExposureStack | null; error: string | null }>({ key: "", stack: null, error: null });
  const [expIndex, setExpIndex] = useState(0);
  const keepView = useRef(initial.hasParams);
  const metaId = useRef<string | null>(null);
  const [gridRef, gridWidth] = useElementWidth<HTMLDivElement>();

  const set = useCallback((patch: Partial<ViewState>) => setView((v) => ({ ...v, ...patch })), []);

  useEffect(() => {
    getTargets().then(setPresets).catch((e) => setPresetsError(`Cannot reach the API: ${e.message || e}`));
  }, []);

  const target: Target | null = spec.kind === "coords" ? spec.target : presets.find((t) => t.id === spec.id) ?? null;
  const fov = fovOverride ?? target?.fov_arcmin ?? 6;
  const unknownPreset = spec.kind === "preset" && presets.length > 0 && !target;

  // ---- server build + arrays
  useEffect(() => {
    if (!target) return;
    const ctrl = new AbortController();
    setError(null);
    setProgress({ stage: "queued", done: 0, total: 0, elapsed: 0 });
    loadSequence(
      { ra: target.ra, dec: target.dec, pmra: target.pmra, pmdec: target.pmdec, ref_epoch: target.ref_epoch, name: target.name ?? null, fov_arcmin: fov },
      setProgress,
      ctrl.signal,
    )
      .then((data) => {
        metaId.current = data.meta.id;
        setCatalogs({});
        setCatalogStatus({});
        setExp({ key: "", stack: null, error: null });
        const keep = keepView.current; // read now: the updater below runs later
        keepView.current = false;
        setView((v) => normalizeView(v, data, keep, target, initial.view));
        setSeq(data);
        setProgress(null);
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setProgress(null);
        setError(String(e?.message || e));
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.ra, target?.dec, target?.pmra, target?.pmdec, target?.ref_epoch, fov, reloadKey]);

  const derived = useMemo(() => (seq ? new Derived(seq) : null), [seq]);
  const meta = seq?.meta ?? null;
  const playable = useMemo(() => (derived ? playableEpochs(derived, view) : []), [derived, view.mode, view.band, view.ends, view.partial]); // eslint-disable-line react-hooks/exhaustive-deps
  const refEpoch = derived ? effectiveRef(derived, view) : -1;

  // ---- single exposures (Exposures mode)
  const expKey = meta && view.mode === "exp" && view.band !== "all" ? `${meta.id}|${view.epoch}|${view.band}` : "";
  useEffect(() => {
    if (!expKey || !meta || view.band === "all") return;
    if (!meta.arrays.exposures) {
      setExp({ key: expKey, stack: null, error: "Single exposures are not stored for this field size; choose a smaller field." });
      return;
    }
    const ctrl = new AbortController();
    setExp({ key: expKey, stack: null, error: null });
    setExpIndex(0);
    fetchExposures(meta.id, view.epoch, view.band, ctrl.signal)
      .then((stack) => setExp({ key: expKey, stack, error: null }))
      .catch((e) => !ctrl.signal.aborted && setExp({ key: expKey, stack: null, error: String(e.message || e) }));
    return () => ctrl.abort();
  }, [expKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const expStack = exp.key === expKey ? exp.stack : null;
  const expView: ExposureView = { key: expKey, stack: expStack, index: expIndex, error: exp.key === expKey ? exp.error : null };
  const display = useMemo(
    () => (derived ? computeDisplay(derived, view, expView) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [derived, view.mode, view.band, view.epoch, view.ref, view.lo, view.hi, view.stack, expStack, expIndex, expView.error],
  );

  // ---- catalogs for overlays
  const wanted = view.overlays.filter((o): o is (typeof CATALOG_IDS)[number] => (CATALOG_IDS as readonly string[]).includes(o));
  useEffect(() => {
    if (!meta) return;
    for (const cat of wanted) {
      if (catalogStatus[cat]) continue;
      const id = meta.id;
      setCatalogStatus((s) => ({ ...s, [cat]: { state: "loading" } }));
      fetchCatalog(cat, meta.grid.ra, meta.grid.dec, Math.min(30, meta.grid.fov_arcmin * 0.75))
        .then((rows) => {
          if (metaId.current !== id) return;
          setCatalogs((c) => ({ ...c, [cat]: rows }));
          setCatalogStatus((s) => ({ ...s, [cat]: { state: "ready", count: rows.length } }));
        })
        .catch((e) => metaId.current === id && setCatalogStatus((s) => ({ ...s, [cat]: { state: "error", message: String(e.message || e) } })));
    }
  }, [meta?.id, wanted.join(","), catalogStatus]); // eslint-disable-line react-hooks/exhaustive-deps

  // Turning a failed catalog off clears its error, so turning it on again retries.
  useEffect(() => {
    const stale = Object.keys(catalogStatus).filter((c) => catalogStatus[c].state === "error" && !wanted.includes(c as never));
    if (stale.length) setCatalogStatus((s) => Object.fromEntries(Object.entries(s).filter(([c]) => !stale.includes(c))));
  }, [wanted.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- playback
  const nExp = expStack?.indices.length ?? 0;
  useEffect(() => {
    if (!playing || !derived) return;
    const t = window.setInterval(() => {
      if (view.mode === "exp") {
        if (nExp > 1) setExpIndex((i) => (i + 1) % nExp);
      } else if (view.mode !== "stack" && playable.length > 1) {
        setView((v) => ({ ...v, epoch: playable[(playable.indexOf(v.epoch) + 1) % playable.length] }));
      }
    }, view.speed);
    return () => window.clearInterval(t);
  }, [playing, derived, view.mode, view.speed, playable, nExp]);

  const step = (dir: 1 | -1) => {
    if (view.mode === "exp") {
      if (nExp) setExpIndex((i) => (i + dir + nExp) % nExp);
      return;
    }
    if (!playable.length) return;
    const i = Math.max(0, playable.indexOf(view.epoch));
    set({ epoch: playable[(i + dir + playable.length) % playable.length] });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName ?? "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === " ") {
        if (tag === "BUTTON") return;
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        step(1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        step(-1);
      } else if (/^[1-9]$/.test(e.key) && meta) {
        const b = meta.bands[Number(e.key) - 1];
        if (b && derived?.playable(b.id).length) set({ band: b.id });
      } else if (e.key === "0") set({ band: "all" });
      else {
        const m = MODES.find((x) => x.key === e.key.toLowerCase());
        if (m) set({ mode: m.id });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ---- URL
  useEffect(() => {
    if (spec.kind === "preset" && !presets.length) return;
    writeUrl(spec, fov, view, embed);
  }, [spec, fov, view, embed, presets.length]);

  // ---- actions
  const resetForNewTarget = () => {
    keepView.current = false;
    setView((v) => ({ ...v, epoch: 0, ref: -1, stack: null, mode: v.mode === "exp" ? "blink" : v.mode }));
    setPlaying(true);
    setSearchError(null);
    setHero(false);
  };
  const choosePreset = (id: string) => {
    setSpec({ kind: "preset", id });
    setFovOverride(null);
    resetForNewTarget();
  };
  const search = async (q: string) => {
    setSearching(true);
    setSearchError(null);
    try {
      const t = await resolveQuery(q);
      if (t.id && presets.some((p) => p.id === t.id)) choosePreset(t.id);
      else {
        setSpec({ kind: "coords", target: { ...t, name: t.name || q } });
        resetForNewTarget();
      }
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  };
  const dismissHero = () => {
    setHero(false);
    safeStorage(INTRO_KEY, "1");
  };

  // ---- presentation
  const epochInfo = meta?.epochs[view.epoch];
  const year = (() => {
    if (meta && view.mode === "exp" && expStack?.indices.length) {
      const ex = meta.exposures[expStack.indices[Math.min(expIndex, expStack.indices.length - 1)]];
      if (ex) return mjdToYear(ex.mjd);
    }
    return epochInfo ? mjdToYear(epochInfo.mjd_mean) : 2026;
  })();
  const span: [number, number] | null =
    meta && meta.epochs.length > 1 ? [mjdToYear(meta.epochs[0].mjd_mean), mjdToYear(meta.epochs[meta.epochs.length - 1].mjd_mean)] : null;
  const track: [number, number][] = meta ? meta.target.track.map((t) => [t.x, t.y]) : [];
  const trackPoint = meta ? meta.target.track.find((t) => t.epoch === view.epoch) ?? meta.target.track[0] : undefined;
  let targetPx: [number, number] | null = trackPoint ? [trackPoint.x, trackPoint.y] : null;
  const pad2 = (k: number) => String(k).padStart(2, "0");
  let hud: StageHud | null = null;
  if (meta && derived && epochInfo) {
    const bl = view.mode === "rgb" ? rgbLabel(derived) : bandLabel(view.band, meta.bands);
    const nb = view.band === "all" || view.mode === "rgb" ? epochInfo.n : epochInfo.bands[view.band]?.n ?? 0;
    const at = (mjd: number): [number, number] =>
      propagate({ ra: meta.target.ra, dec: meta.target.dec, epoch: meta.target.ref_epoch, pmra: meta.target.pmra, pmdec: meta.target.pmdec }, mjdToYear(mjd));
    if (view.mode === "stack") {
      const eps = stackEpochs(derived, view);
      const frames = eps.reduce((sum, e) => sum + (view.band === "all" ? meta.epochs[e].n : meta.epochs[e].bands[view.band]?.n ?? 0), 0);
      hud = { epoch: "Stack", dates: `${eps.length} epoch${eps.length === 1 ? "" : "s"}`, spectral: bl, frames: `${frames} frames`, target: trackPoint ? [trackPoint.ra, trackPoint.dec] : null };
    } else if (view.mode === "exp" && expStack?.indices.length) {
      const i = Math.min(expIndex, expStack.indices.length - 1);
      const ex = meta.exposures[expStack.indices[i]];
      hud = { epoch: `Exposure ${pad2(i + 1)} / ${pad2(expStack.indices.length)}`, dates: `${formatDate(ex.mjd, true)} UTC`, spectral: `${ex.band} · λ ${ex.wl.toFixed(3)} µm`, frames: `Epoch ${pad2(view.epoch + 1)}`, target: at(ex.mjd) };
      targetPx = ex.target_px;
    } else {
      hud = {
        epoch: `Epoch ${pad2(view.epoch + 1)} / ${pad2(meta.epochs.length)}${isPartial(derived, view.epoch) ? " · partial" : ""}`,
        dates: formatRange(epochInfo.mjd_min, epochInfo.mjd_max),
        spectral: bl,
        frames: `${nb} frames`,
        target: trackPoint ? [trackPoint.ra, trackPoint.dec] : null,
      };
      if (view.mode === "diff") hud.extra = `minus epoch ${pad2(refEpoch + 1)} · ${formatDate(meta.epochs[refEpoch]?.mjd_mean ?? 0).slice(0, 7)}`;
    }
  }
  const flagBits = meta ? view.flagBits ?? defaultFlagBits(meta.flag_bits) : [];
  const flagMask = flagBits.reduce((m, b) => m | (1 << b), 0);
  const flags = derived && view.overlays.includes("flags") ? derived.flagsOr(view.epoch, view.mode === "rgb" ? "rgb" : view.band) : null;
  const overlays = useMemo(() => new Set(view.overlays), [view.overlays]);
  const canPlay = view.mode === "exp" ? nExp > 1 : view.mode !== "stack" && playable.length > 1;
  const errorText = error ?? presetsError ?? (unknownPreset ? `Unknown target "${spec.kind === "preset" ? spec.id : ""}"` : null);

  // Stage size: as large as the width allows while header, toolbar, stage and transport fit on one screen.
  // The landing hero sits above and scrolls away; the stage never shrinks for it.
  const narrow = useMediaQuery("(max-width: 1000px)");
  const sideMin = 340;
  const cellPad = mobile ? 0 : 48;
  const byWidth = (mobile || narrow || embed ? gridWidth : gridWidth - sideMin) - cellPad;
  // top bar 56 + section head 44 + toolbar 40 + gaps 48 + hint 20 + transport 74 + cell padding 40
  const heightBudget = mobile ? Infinity : embed ? vh - 150 : Math.max(460, vh - (56 + 44 + 40 + 48 + 20 + 74 + 40));
  const stageSize = Math.max(240, Math.floor(Math.min(byWidth || 600, heightBudget)));
  const stackWidth = mobile ? stageSize : Math.min(byWidth || stageSize, Math.max(stageSize, 600));
  const releases = meta
    ? [...new Set(meta.sources.collections.map((c) => c.replace("spherex_", "").replace("_deep", "").toUpperCase()))].join(" + ")
    : null;

  const stage = (
    <Stage
      grid={meta?.grid ?? null}
      display={display}
      view={view}
      overlays={overlays}
      flags={flags}
      flagMask={flagMask}
      catalogs={catalogs}
      year={year}
      span={span}
      targetPx={targetPx}
      track={track}
      trackIndex={view.epoch}
      hud={hud}
      progress={progress}
      lockKey={meta?.id ?? "none"}
      error={errorText}
      onRetry={() => setReloadKey((k) => k + 1)}
      size={stageSize}
      onZoom={(zoom) => set({ zoom })}
      compact={mobile}
    />
  );

  const transport =
    meta && derived ? (
      <Transport
        meta={meta}
        playing={playing && canPlay}
        canPlay={canPlay}
        onToggle={() => setPlaying((p) => !p)}
        onStep={step}
        speed={view.speed}
        onSpeed={(speed) => set({ speed })}
        ends={view.ends}
        onEnds={(ends) => set({ ends })}
        partial={view.partial}
        onPartial={(partial) => set({ partial })}
        mode={view.mode}
        current={view.epoch}
        available={derived.playable(view.mode === "rgb" ? "rgb" : view.band)}
        refEpoch={view.mode === "diff" ? refEpoch : null}
        stackSel={view.stack}
        onSelect={(e) => set({ epoch: e })}
        onToggleStack={(e) => {
          const cur = stackEpochs(derived, view);
          const next = cur.includes(e) ? cur.filter((x) => x !== e) : [...cur, e].sort((a, b) => a - b);
          set({ stack: next.length ? next : null });
        }}
        exposures={view.mode === "exp" && nExp ? { count: nExp, index: Math.min(expIndex, nExp - 1), onIndex: setExpIndex } : null}
      />
    ) : (
      <div className="transport skeleton-row" aria-hidden>
        <div className="skeleton" style={{ width: 120, height: 32 }} />
        <div className="skeleton" style={{ flex: 1, height: 32 }} />
      </div>
    );

  if (embed) {
    return (
      <div className="app embed">
        <ProgressBar progress={progress} />
        <div className="embed-main" ref={gridRef}>
          {stage}
          <div className="embed-controls" style={{ width: stageSize }}>
            {transport}
            {meta && derived && (
              <div className="embed-row">
                <ModeControl view={view} set={set} size="sm" />
                <DetectorControl meta={meta} derived={derived} view={view} set={set} />
                <a className="btn ghost" href={viewLink(spec, fov, view, false)} target="_blank" rel="noreferrer">
                  Open full viewer <ExternalLink size={14} />
                </a>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`app${mobile ? " is-mobile" : ""}`}>
      <TopBar
        presets={presets}
        activeId={spec.kind === "preset" ? spec.id : null}
        busy={searching}
        searchError={searchError}
        fov={fov}
        compact={mobile}
        releases={releases}
        onSearch={search}
        onPreset={choosePreset}
        onFov={(f) => setFovOverride(f)}
        link={() => viewLink(spec, fov, view, false)}
        embedLink={() => viewLink(spec, fov, view, true)}
      />
      <ProgressBar progress={progress} />
      {hero && presets.length > 0 && <Hero presets={presets} onPick={choosePreset} onDismiss={dismissHero} />}
      <main className="workspace">
        <div
          className="editorial"
          ref={gridRef}
          style={mobile || narrow ? undefined : { gridTemplateColumns: `${stackWidth + cellPad}px minmax(${sideMin}px, 460px)` }}
        >
          <section className="cell cell-viewer" aria-label="Viewer">
            <SectionHead n="01" title="Viewer">
              {meta && !mobile && (
                <ViewerActions meta={meta} view={view} set={set} refEpoch={refEpoch} refs={derived ? derived.playable(view.band) : []} catalogStatus={catalogStatus} />
              )}
            </SectionHead>
            <div className="cell-body viewer-body">
              <div className="stage-stack" style={{ width: mobile ? "100%" : stackWidth }}>
                {!mobile && (meta && derived ? <Toolbar meta={meta} derived={derived} view={view} set={set} /> : <div className="toolbar skeleton-row" aria-hidden><div className="skeleton" style={{ width: 360, height: 32 }} /></div>)}
                {stage}
                {!mobile && meta && <ModeHint view={view} />}
                {transport}
              </div>
            </div>
          </section>
          <section className="cell cell-data" aria-label="Data">
            <SectionHead n="04" title="Data" />
            <div className="cell-body">
              {meta ? (
                <SidePanel
                  meta={meta}
                  target={target}
                  epoch={view.epoch}
                  band={view.band}
                  mode={view.mode}
                  flagBits={flagBits}
                  onFlagBits={(b) => set({ flagBits: b })}
                  flagsOn={view.overlays.includes("flags")}
                  onFlagsOn={(on) => set({ overlays: on ? [...view.overlays.filter((o) => o !== "flags"), "flags"] : view.overlays.filter((o) => o !== "flags") })}
                />
              ) : (
                <div aria-hidden>
                  <div className="skeleton" style={{ height: 34 }} />
                  <div className="skeleton" style={{ height: 22, width: "60%", marginTop: 20 }} />
                  <div className="skeleton" style={{ height: 160, marginTop: 12 }} />
                </div>
              )}
            </div>
          </section>
          {meta ? <ChartSections meta={meta} epoch={view.epoch} ylog={view.ylog} onYlog={(ylog) => set({ ylog })} /> : <ChartSkeletons />}
        </div>
        <footer className="footer">
          <Wordmark compact />
          <span>
            Data: NASA SPHEREx (DR1, QR3) via NASA/IPAC IRSA · Gaia DR3 (ESA) · CatWISE2020, 2MASS (IRSA). Built for the NASA Space Apps
            Challenge 2026.
          </span>
        </footer>
      </main>
      {mobile && meta && derived && (
        <BottomSheet summary={<ModeControl view={view} set={set} size="sm" />}>
          <div className="sheet-section">
            <MicroLabel>Detector</MicroLabel>
            <DetectorControl meta={meta} derived={derived} view={view} set={set} />
          </div>
          {view.mode === "diff" && (
            <div className="sheet-section">
              <MicroLabel>Reference epoch</MicroLabel>
              <select value={refEpoch} onChange={(e) => set({ ref: Number(e.target.value) })} aria-label="Reference epoch">
                {derived.playable(view.band).map((e) => (
                  <option key={e} value={e}>
                    epoch {e + 1} · {meta.epochs[e].date_min.slice(0, 7)}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="sheet-section">
            <MicroLabel>Display</MicroLabel>
            <DisplayPanel view={view} set={set} />
          </div>
          <div className="sheet-section">
            <MicroLabel>Layers</MicroLabel>
            <LayersPanel view={view} set={set} catalogStatus={catalogStatus} />
          </div>
        </BottomSheet>
      )}
    </div>
  );
}
