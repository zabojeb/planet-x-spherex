/** Top-level wiring: target + field of view -> server build -> derived images -> stage, controls, panels and charts. */
import { ExternalLink } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, cachedTable, ensurePlanes, exportUrl, fetchCatalog, fetchExposures, fetchTable, getTargets, loadSequence, measureSource, prefetchSequence, resolveQuery, type Progress } from "./api";
import { formatDate, formatRange, mjdToYear, propagate } from "./astro";
import { DetectorControl, DetectorPanel, DisplayPanel, LayersPanel, ModeControl, View3DBar, type CatalogStatus } from "./components/Controls";
import type { Layer, TrackPoint3D } from "./components/Sky3D";
import { Drawer, type DrawerTab } from "./components/Drawer";
import { Rail } from "./components/Rail";
import { SkyView } from "./components/SkyView";
import { Hero, ProgressBar } from "./components/Progress";
import { SharePanel, TopLeft } from "./components/TopBar";
import { Dock, PlaybackOptions } from "./components/Transport";
import { bandLabel, computeDisplay, effectiveRef, isPartial, planesNeeded, planesNeeded3d, playableEpochs, rgbLabel, slices3d, stackEpochs, type Display, type ExposureView } from "./display";
import { safeStorage, useMediaQuery } from "./hooks";
import { Derived, type BandSel } from "./render";
import { DEFAULT_VIEW, MODES, defaultFlagBits, parseUrl, viewLink, writeUrl, type TargetSpec, type View3D, type ViewState } from "./state";
import type { CatalogSource, ExposureStack, Meta, Photometry, SequenceData, Target } from "./types";

// three.js is a separate chunk, fetched the first time a 3D view opens.
const Sky3D = lazy(() => import("./components/Sky3D"));

interface StageHud {
  epoch: string;
  dates: string;
  spectral: string;
  frames: string;
  extra?: string;
  target?: [number, number] | null;
}

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
  const [drawer, setDrawer] = useState<DrawerTab | null>(null);
  const [planesTick, setPlanesTick] = useState(0);
  const [planesBusy, setPlanesBusy] = useState(false);
  const [tablesTick, setTablesTick] = useState(0);
  const [tablesError, setTablesError] = useState<string | null>(null);
  const [source, setSource] = useState<{ key: string; label: string; photometry: Photometry[] } | null>(null);
  const [spin, setSpin] = useState(false);
  const last3d = useRef<Exclude<View3D, "off">>(initial.view.view3d && initial.view.view3d !== "off" ? initial.view.view3d : "relief");

  const set = useCallback((patch: Partial<ViewState>) => setView((v) => ({ ...v, ...patch })), []);
  if (view.view3d !== "off") last3d.current = view.view3d;
  const is3d = view.view3d !== "off";
  const isCube = view.view3d === "time" || view.view3d === "bands";
  const toggle3d = () => set({ view3d: is3d ? "off" : last3d.current });

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
        setSource(null);
        setTablesError(null);
        const keep = keepView.current; // read now: the updater below runs later
        keepView.current = false;
        setView((v) => normalizeView(v, data, keep, target, initial.view));
        setSeq(data);
        // Stays on "download" until the planes of the first frame are in (see the planes effect).
        setProgress({ stage: "download", done: 0, total: 0, elapsed: 0 });
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

  // ---- image planes: fetch only what the view shows now, then what it will likely show next
  const flagsOn = view.overlays.includes("flags");
  const willPlay = playing && view.mode !== "stack" && view.mode !== "exp" && playable.length > 1;
  const need = useMemo(
    () => (derived ? (isCube ? planesNeeded3d(derived, view, willPlay) : planesNeeded(derived, view, willPlay, flagsOn && !is3d)) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [derived, view.mode, view.band, view.epoch, view.ref, view.stack, view.ends, view.partial, view.view3d, willPlay, flagsOn],
  );
  const needKey = need ? [...need.now, ...need.soon].map((k) => `${k.array}${k.e}${k.b}`).join(",") : "";
  useEffect(() => {
    if (!seq || !need) return;
    const ctrl = new AbortController();
    setPlanesBusy(true);
    ensurePlanes(seq, need.now, ctrl.signal, (done, total) => setProgress((p) => (p?.stage === "download" ? { ...p, done, total } : p)))
      .then(() => {
        if (ctrl.signal.aborted) return;
        setPlanesBusy(false);
        setProgress((p) => (p?.stage === "download" ? null : p));
        setPlanesTick((t) => t + 1);
        // Look-ahead is best effort: a failure here only means the next frame downloads when shown.
        ensurePlanes(seq, need.soon, ctrl.signal).then(() => !ctrl.signal.aborted && setPlanesTick((t) => t + 1), () => undefined);
      })
      .catch((e) => {
        if (ctrl.signal.aborted || e?.name === "AbortError") return;
        setPlanesBusy(false);
        setProgress(null);
        setError(`Could not download the images: ${e?.message || e}`);
      });
    return () => {
      ctrl.abort();
      setPlanesBusy(false);
    };
  }, [seq, needKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const expStack = exp.key === expKey ? exp.stack : null;
  const expView: ExposureView = { key: expKey, stack: expStack, index: expIndex, error: exp.key === expKey ? exp.error : null };
  const computed = useMemo(
    () => (derived ? computeDisplay(derived, view, expView) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [derived, view.mode, view.band, view.epoch, view.ref, view.lo, view.hi, view.stack, expStack, expIndex, expView.error, planesTick],
  );
  // While the next frame downloads, keep showing the last one of the same sequence.
  const lastDisplay = useRef<{ id: string; d: Display } | null>(null);
  if (computed && meta) lastDisplay.current = { id: meta.id, d: computed };
  const display = computed ?? (lastDisplay.current && lastDisplay.current.id === meta?.id ? lastDisplay.current.d : null);

  // ---- per-exposure tables (columns), only when something on screen needs them
  const wantExposures = !!seq && (view.mode === "exp" || drawer === "details");
  const wantPhotometry = !!seq && !!drawer && drawer !== "periodogram";
  const exposures = meta ? cachedTable(meta.id, "exposures") : undefined;
  const photometry = meta ? cachedTable(meta.id, "photometry") : undefined;
  useEffect(() => {
    if (!seq) return;
    const ctrl = new AbortController();
    const jobs: Promise<unknown>[] = [];
    if (wantExposures && !cachedTable(seq.meta.id, "exposures")) jobs.push(fetchTable(seq, "exposures", ctrl.signal));
    if (wantPhotometry && !cachedTable(seq.meta.id, "photometry")) jobs.push(fetchTable(seq, "photometry", ctrl.signal));
    if (!jobs.length) return;
    setTablesError(null);
    Promise.all(jobs)
      .then(() => !ctrl.signal.aborted && setTablesTick((t) => t + 1))
      .catch((e) => !ctrl.signal.aborted && setTablesError(`Could not load the measurements: ${e?.message || e}`));
    return () => ctrl.abort();
  }, [seq, wantExposures, wantPhotometry]);
  const fullMeta = useMemo(
    () => (meta ? { ...meta, exposures: exposures ?? meta.exposures, photometry: photometry ?? meta.photometry } : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [meta, exposures, photometry, tablesTick],
  );

  // ---- 3D cubes: one layer per epoch (time) or detector (λ), each computed like the flat frame
  const slices = useMemo(
    () => (derived && isCube ? slices3d(derived, view) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [derived, isCube, view.view3d, view.mode, view.band, view.epoch, view.ref, view.stack, view.lo, view.hi],
  );
  // In Exposures mode the time cube stacks the single exposures of the epoch at their times instead: an
  // asteroid crossing the field during a visit becomes a diagonal line through the layers.
  const expCube = view.view3d === "time" && view.mode === "exp" && view.band !== "all";
  const expRows = useMemo(
    () => (expCube && expStack && exposures ? expStack.indices.map((i) => exposures[i]).filter(Boolean) : []),
    [expCube, expStack, exposures],
  );
  const layers: Layer[] = useMemo(() => {
    if (!derived) return [];
    if (expCube) {
      if (!expRows.length || expRows.length !== expStack?.indices.length) return [];
      const t0 = expRows[0].mjd;
      const span = expRows[expRows.length - 1].mjd - t0;
      return expRows.map((ex, i) => ({
        key: `x${i}`,
        label: formatDate(ex.mjd, true).slice(5),
        z: span > 0 ? (ex.mjd - t0) / span : 0.5,
        current: i === expIndex,
        display: computeDisplay(derived, view, { ...expView, index: i }),
      }));
    }
    return slices.map((s) => ({ key: s.key, label: s.label, z: s.z, current: s.current, display: computeDisplay(derived, s.view, expView) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [derived, slices, planesTick, expCube, expRows, expIndex, view.lo, view.hi]);

  // ---- catalogs for overlays
  const wanted = view.overlays.filter((o): o is (typeof CATALOG_IDS)[number] => (CATALOG_IDS as readonly string[]).includes(o));
  const catalogCtrl = useRef<AbortController | null>(null);
  useEffect(() => {
    catalogCtrl.current?.abort(); // a new sequence cancels catalog queries for the old one
    catalogCtrl.current = new AbortController();
    return () => catalogCtrl.current?.abort();
  }, [meta?.id]);
  useEffect(() => {
    if (!meta) return;
    for (const cat of wanted) {
      if (catalogStatus[cat]) continue;
      const id = meta.id;
      setCatalogStatus((s) => ({ ...s, [cat]: { state: "loading" } }));
      fetchCatalog(cat, meta.grid.ra, meta.grid.dec, Math.min(30, meta.grid.fov_arcmin * 0.75), catalogCtrl.current?.signal)
        .then((rows) => {
          if (metaId.current !== id) return;
          setCatalogs((c) => ({ ...c, [cat]: rows }));
          setCatalogStatus((s) => ({ ...s, [cat]: { state: "ready", count: rows.length } }));
        })
        .catch((e) => metaId.current === id && e?.name !== "AbortError" && setCatalogStatus((s) => ({ ...s, [cat]: { state: "error", message: String(e.message || e) } })));
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
      if (e.key === "Escape") setDrawer(null);
      else if (e.key === " ") {
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
      else if (e.key.toLowerCase() === "v") toggle3d();
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
  const prefetchPreset = (id: string) => {
    const t = presets.find((p) => p.id === id);
    if (t) prefetchSequence({ ra: t.ra, dec: t.dec, pmra: t.pmra, pmdec: t.pmdec, ref_epoch: t.ref_epoch, name: t.name ?? null, fov_arcmin: t.fov_arcmin ?? 6 });
  };
  /** Forced photometry of a clicked catalogue source; its curves replace the target's in the drawer. */
  const measure = async (cat: string, src: CatalogSource) => {
    if (!meta) return;
    const res = await measureSource(meta.id, {
      ra: src.ra,
      dec: src.dec,
      pmra: src.pmra ?? 0,
      pmdec: src.pmdec ?? 0,
      ref_epoch: src.epoch,
      name: src.id,
    }).catch((e) => {
      throw e instanceof ApiError && (e.status === 404 || e.status === 405) ? new Error("Source photometry is not available on this server yet.") : e;
    });
    setSource({ key: `${cat}:${src.id}`, label: src.id, photometry: res.photometry });
    setHero(false);
    setDrawer((d) => (d && d !== "details" ? d : "spectrum"));
  };
  const dismissHero = () => {
    setHero(false);
    safeStorage(INTRO_KEY, "1");
  };

  // ---- presentation
  const epochInfo = meta?.epochs[view.epoch];
  const exposureRows = fullMeta?.exposures ?? [];
  const year = (() => {
    if (meta && view.mode === "exp" && expStack?.indices.length) {
      const ex = exposureRows[expStack.indices[Math.min(expIndex, expStack.indices.length - 1)]];
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
    } else if (view.mode === "exp" && expStack?.indices.length && exposureRows.length) {
      const i = Math.min(expIndex, expStack.indices.length - 1);
      const ex = exposureRows[expStack.indices[i]];
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

  const dateLabel = hud ? `${hud.epoch}${hud.extra ? ` · ${hud.extra}` : ""}` : "";
  const bandShort = view.mode === "rgb" ? "RGB" : view.band === "all" ? "All" : view.band;
  const openDetails = (tab: DrawerTab = "spectrum") => {
    setHero(false);
    setDrawer((d) => (d ? null : tab));
  };
  const setFlagsOn = (on: boolean) => set({ overlays: on ? [...view.overlays.filter((o) => o !== "flags"), "flags"] : view.overlays.filter((o) => o !== "flags") });

  const zOf = new Map(slices.map((s) => [s.view.epoch, s.z]));
  const track3d: TrackPoint3D[] = !meta
    ? []
    : expCube
      ? expRows.map((ex, i) => ({ x: ex.target_px[0], y: ex.target_px[1], z: layers[i]?.z ?? 0, current: i === expIndex }))
      : meta.target.track
          .filter((t) => view.view3d !== "time" || zOf.has(t.epoch))
          .map((t) => ({ x: t.x, y: t.y, z: view.view3d === "time" ? zOf.get(t.epoch)! : null, current: t.epoch === view.epoch }));
  const cur3d = track3d.find((t) => t.current);
  const currentTrack3d: [number, number] | null = cur3d ? [cur3d.x, cur3d.y] : null;
  const sliceMode = MODES.find((m) => m.id === slices[0]?.view.mode)?.label ?? "";
  const label3d = (() => {
    const first = slices[0];
    const last = slices[slices.length - 1];
    if (expCube && layers.length)
      return { date: `Time cube · ${layers.length} exposures · ${layers[0].label} → ${layers[layers.length - 1].label} UTC`, sub: `Exposures · ${bandLabel(view.band, meta?.bands ?? [])} · ${hud?.epoch ?? ""}` };
    if (expCube) return { date: "Loading exposures…", sub: undefined };
    if (view.view3d === "time" && first)
      return { date: `Time cube · ${slices.length} epochs · ${first.label} → ${last.label}`, sub: `${sliceMode} · ${view.mode === "rgb" ? "false colour" : bandLabel(view.band, meta?.bands ?? [])} · ${dateLabel}` };
    if (view.view3d === "bands" && first && meta) {
      const wl = (id: string) => meta.bands.find((b) => b.id === id)!;
      const range = `${wl(first.view.band).wl_min.toFixed(2)}–${wl(last.view.band).wl_max.toFixed(2)} µm`;
      return { date: `λ cube · ${slices.length} detectors · ${range}`, sub: `${sliceMode} · ${view.mode === "stack" ? hud?.dates ?? "" : dateLabel}` };
    }
    return { date: dateLabel, sub: hud?.spectral };
  })();

  const sky = is3d ? (
    <Suspense fallback={<div className="sky" />}>
      <Sky3D
        kind={view.view3d as Exclude<View3D, "off">}
        n={meta?.grid.n ?? 0}
        display={display}
        layers={layers}
        stretch={view.stretch}
        cmap={view.cmap}
        smooth={view.smooth}
        height={view.height3d}
        spin={spin}
        track={track3d}
        targetPx={view.view3d === "time" ? currentTrack3d : targetPx}
        showTarget={overlays.has("target")}
        showTrack={overlays.has("track")}
        showCompass={overlays.has("compass")}
        axisLabel={view.view3d === "time" ? "time" : "λ"}
        dateLabel={label3d.date}
        subLabel={label3d.sub}
        progress={progress}
        error={errorText}
        onRetry={() => setReloadKey((k) => k + 1)}
        lockKey={meta?.id ?? "none"}
        onPick={(key) => {
          if (expCube) return setExpIndex(Number(key.slice(1)));
          const s = slices.find((x) => x.key === key);
          if (!s) return;
          if (view.view3d === "time") set({ epoch: s.view.epoch });
          else set({ band: s.view.band });
        }}
        onInteract={() => hero && setHero(false)}
        shiftX={hero && !mobile && presets.length ? 180 : 0}
      />
    </Suspense>
  ) : (
    <SkyView
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
      targetName={target?.name || meta?.target.name || "Target"}
      targetWorld={hud?.target ?? null}
      track={track}
      trackIndex={view.epoch}
      dateLabel={dateLabel}
      progress={progress}
      lockKey={meta?.id ?? "none"}
      error={errorText}
      onRetry={() => setReloadKey((k) => k + 1)}
      onZoom={(zoom) => set({ zoom })}
      onInteract={() => hero && setHero(false)}
      onOpenDetails={() => {
        setSource(null);
        openDetails("spectrum");
      }}
      onMeasure={seq ? measure : undefined}
      shiftX={hero && !mobile && presets.length ? 180 : 0}
    />
  );

  const dock =
    meta && derived ? (
      <Dock
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
        date={hud?.dates ?? ""}
      >
        <ModeControl view={view} set={set} size="sm" />
      </Dock>
    ) : null;

  if (embed) {
    return (
      <div className="app earth embed">
        <ProgressBar progress={progress} />
        {sky}
        <div className="floating-bottom">
          {dock}
          {meta && derived && (
            <div className="embed-row glass">
              <DetectorControl meta={meta} derived={derived} view={view} set={set} />
              <a className="btn ghost" href={viewLink(spec, fov, view, false)} target="_blank" rel="noreferrer">
                Full viewer <ExternalLink size={14} />
              </a>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`app earth${mobile ? " is-mobile" : ""}${drawer ? " drawer-open" : ""}${hero ? " hero-on" : ""}`}>
      <ProgressBar progress={progress ?? (planesBusy ? { stage: "download", done: 0, total: 0, elapsed: 0 } : null)} />
      {sky}
      <TopLeft
        presets={presets}
        activeId={spec.kind === "preset" ? spec.id : null}
        busy={searching}
        searchError={searchError}
        fov={fov}
        compact={mobile}
        onSearch={search}
        onPreset={choosePreset}
        onFov={(f) => setFovOverride(f)}
      />
      {hero && presets.length > 0 && <Hero presets={presets} onPick={choosePreset} onDismiss={dismissHero} onPrefetch={prefetchPreset} />}
      {meta && derived && (
        <Rail
          bandLabel={bandShort}
          detector={<DetectorPanel meta={meta} derived={derived} view={view} set={set} refEpoch={refEpoch} />}
          display={
            <div className="panel-stack">
              <DisplayPanel view={view} set={set} />
              <PlaybackOptions meta={meta} mode={view.mode} speed={view.speed} onSpeed={(speed) => set({ speed })} ends={view.ends} onEnds={(ends) => set({ ends })} partial={view.partial} onPartial={(partial) => set({ partial })} />
            </div>
          }
          layers={<LayersPanel view={view} set={set} catalogStatus={catalogStatus} />}
          share={<SharePanel link={() => viewLink(spec, fov, view, false)} embedLink={() => viewLink(spec, fov, view, true)} exportUrl={(k) => exportUrl(meta.id, k)} />}
          detailsOpen={!!drawer}
          onDetails={() => openDetails("spectrum")}
          is3d={is3d}
          on3d={toggle3d}
        />
      )}
      {is3d && meta && <View3DBar view={view} set={set} spin={spin} onSpin={setSpin} />}
      <div className="floating-bottom">{dock}</div>
      {drawer && fullMeta && (
        <Drawer
          tab={drawer}
          onTab={setDrawer}
          onClose={() => setDrawer(null)}
          meta={source ? { ...fullMeta, photometry: source.photometry } : fullMeta}
          tablesReady={(!wantExposures || !!exposures) && (!wantPhotometry || !!photometry || !!source)}
          tablesError={tablesError}
          source={source}
          onClearSource={() => setSource(null)}
          target={target}
          epoch={view.epoch}
          band={view.band}
          mode={view.mode}
          ylog={view.ylog}
          onYlog={(ylog) => set({ ylog })}
          flagBits={flagBits}
          onFlagBits={(b) => set({ flagBits: b })}
          flagsOn={flagsOn}
          onFlagsOn={setFlagsOn}
        />
      )}
    </div>
  );
}
