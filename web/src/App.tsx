/** Top-level wiring: target + field of view -> server build -> derived images -> stage, controls, panels and charts. */
import { ExternalLink } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AmbiguousObject, ApiError, cachedArchivalFrame, cachedTable, ensurePlanes, exportUrl, fetchArchivalFrame, fetchCatalog, fetchExposures, fetchTable, getTargets,
  loadArchival, loadObjectIndex, loadSequence, measureSource, movingRequest, prefetchSequence, searchMoving, type Progress,
} from "./api";
import { archivalFov, embed as embedPlane, frameLabel, gridOffset, selectFrames, stretchGroup } from "./archival";
import { ArchivalStrip, LongBaselineControls } from "./components/LongBaseline";
import { MovingCard, type MovingState } from "./components/Moving";
import { FieldPanel, MeasureTools, useFieldTools, type Candidate } from "./components/FieldTools";
import type { AnalysisSubject } from "./components/chart/PointAnalysisPanel";
import type { VerifySubject } from "./components/chart/VerifyPanel";
import { formatDate, formatRange, mjdToYear, pixelToWorld, propagate, separation, tanToWorld, totalPm, worldToPixel } from "./astro";
import { CATALOG_IDS, type CatalogId } from "./colors";
import { DetectorControl, DetectorPanel, DisplayPanel, LayersPanel, ModeControl, View3DBar, type CatalogStatus } from "./components/Controls";
import type { Layer, Object3D, TrackPoint3D } from "./components/Sky3D";
import { CoordField } from "./components/CoordField";
import { Drawer, type DrawerTab } from "./components/Drawer";
import { GoToPanel, rememberPlace, type Place } from "./components/GoTo";
import { ObjectsPanel } from "./components/Objects";
import { PickCard } from "./components/PickCard";
import { Rail } from "./components/Rail";
import { HIGH_PM, SkyView, catalogMarkers, markerTip, selectionWorld, sourceKey, valueAt, type Selection, type StepDir } from "./components/SkyView";
import { Hero, ProgressBar } from "./components/Progress";
import { SharePanel, TopLeft } from "./components/TopBar";
import { Dock, PlaybackOptions } from "./components/Transport";
import { bandLabel, computeDisplay, effectiveRef, isPartial, planesNeeded, planesNeeded3d, playableEpochs, rgbLabel, slices3d, stackEpochs, stepEpoch, type Display, type ExposureView } from "./display";
import { safeStorage, useMediaQuery } from "./hooks";
import { Derived, percentiles, type BandSel } from "./render";
import { describe, loadRecents, pickFor, randomField, randomObject, saveRecent, searchSpace, type Facet, type ObjectIndex, type Pick, type Recent } from "./search";
import { DEFAULT_VIEW, EMBED_VIEW3D, MODES, defaultFlagBits, parseUrl, viewLink, writeUrl, type TargetSpec, type View3D, type ViewState } from "./state";
import type { ArchivalMeta, CatalogSource, ExposureStack, Meta, MovingChoice, Photometry, SequenceData, Target } from "./types";

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
const xy = ([x, y]: [number, number]) => ({ x, y });

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
  if (keep && fromUrl.longBase) mode = "blink"; // a long-baseline link plays in Blink
  if (band !== "all" && !d.playable(band).length) band = "all";
  const playable = d.playable(band);
  const full = playable.filter((e) => !data.meta.epochs[e]?.partial);
  // Difference mode opens on the last visit (the first is the reference, which would show nothing).
  const start = mode === "diff" ? full[full.length - 1] : full[0];
  const keepEpoch = keep && fromUrl.epoch !== undefined && playable.includes(v.epoch);
  const epoch = keepEpoch ? v.epoch : start ?? playable[0] ?? 0;
  return { ...v, band, mode, epoch, ref: keep ? v.ref : -1, stack: keep ? v.stack : null, arch: keep ? v.arch : -1 };
}

/** Space taken by the floating chrome where the image must not go under it (keep in sync with styles.css):
 * phones stack the top bar, HUD and a two-row dock; mid widths put the HUD above a one-row dock. */
const MOBILE_FRAME = { top: 108, bottom: 290 };
const MID_FRAME = { top: 160, bottom: 264 }; // top: below the coordinate box
/** The HUD's left edge and the clearance it keeps from the dock before it moves above it. */
const HUD_LEFT = 20;
const HUD_DOCK_GAP = 16;

export default function App() {
  const initial = useMemo(() => parseUrl(), []);
  const embed = initial.embed;
  const mobile = useMediaQuery("(max-width: 760px)");
  const [presets, setPresets] = useState<Target[]>([]);
  const [presetsError, setPresetsError] = useState<string | null>(null);
  const [spec, setSpec] = useState<TargetSpec>(initial.target ?? { kind: "preset", id: "barnard" });
  const [fovOverride, setFovOverride] = useState<number | null>(initial.fov);
  const [view, setView] = useState<ViewState>({ ...DEFAULT_VIEW, ...(embed ? { view3d: EMBED_VIEW3D } : null), ...initial.view });
  const [seq, setSeq] = useState<SequenceData | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [playing, setPlaying] = useState(!initial.hasParams);
  const [hero, setHero] = useState(!initial.hasParams && !embed && safeStorage(INTRO_KEY) !== "1");
  // Two things follow the HUD (compass, scale, date) in the bottom-left corner, measured rather than guessed
  // from breakpoints because its width and height change with the mode and the date label:
  // - hudLifted: on screens wider than a phone the HUD moves above the dock only when it would actually
  //   reach it (its right edge within HUD_DOCK_GAP of the dock's left edge);
  // - hudClear: the landing index stays above the HUD (--hud-clear for the .hero rule).
  const [hudLifted, setHudLifted] = useState(false);
  const downWidth = useRef(0); // the HUD's width when last measured in its unlifted place
  const [hudClear, setHudClear] = useState(0);
  useEffect(() => {
    let hud: Element | null = null;
    let dock: Element | null = null;
    const watch = (prev: Element | null, sel: string) => {
      const el = document.querySelector(sel);
      if (el !== prev) {
        if (prev) ro.unobserve(prev);
        if (el) ro.observe(el);
      }
      return el;
    };
    const measure = () => {
      hud = watch(hud, ".hud-min");
      dock = watch(dock, ".dock");
      const h = hud?.getBoundingClientRect();
      const d = dock?.getBoundingClientRect();
      // The HUD's unlifted left edge is the gutter. Lifting shrinks the image frame, which can change the scale
      // bar's length and so the HUD's width; to drop back down it must fit with the width it last had down there
      // (or its current width, if larger), so it does not flip between the two places at the threshold.
      setHudLifted((was) => {
        if (mobile || !h || !d || !h.width) return false;
        if (!was) downWidth.current = h.width;
        const width = was ? Math.max(h.width, downWidth.current) : h.width;
        return HUD_LEFT + width + HUD_DOCK_GAP > d.left;
      });
      setHudClear(hero && h && h.height ? Math.ceil(window.innerHeight - h.top) : 0);
    };
    const ro = new ResizeObserver(measure);
    const mo = new MutationObserver(measure); // the HUD and the dock mount once the first sequence is in
    mo.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", measure);
    measure();
    return () => {
      ro.disconnect();
      mo.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [hero, mobile]);
  const [objectIndex, setObjectIndex] = useState<ObjectIndex | null>(null);
  const [recents, setRecents] = useState<Recent[]>(() => loadRecents());
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
  const [analysis, setAnalysis] = useState<AnalysisSubject | null>(null);
  const [verify, setVerify] = useState<VerifySubject | null>(null);
  const [ruler, setRuler] = useState(false);
  const [magnifier, setMagnifier] = useState(false);
  const [spin, setSpin] = useState(false);
  const last3d = useRef<Exclude<View3D, "off">>(initial.view.view3d && initial.view.view3d !== "off" ? initial.view.view3d : "time");
  const [sel, setSel] = useState<Selection | null>(null);
  const [hoverKey, setHoverKey] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ ra: number; dec: number; key: number } | null>(null);

  const set = useCallback((patch: Partial<ViewState>) => setView((v) => ({ ...v, ...patch })), []);
  if (view.view3d !== "off") last3d.current = view.view3d;
  const is3d = view.view3d !== "off";
  const isCube = view.view3d === "time" || view.view3d === "bands";
  const toggle3d = () => set({ view3d: is3d ? "off" : last3d.current });

  useEffect(() => {
    getTargets().then(setPresets).catch((e) => setPresetsError(`Cannot reach the API: ${e.message || e}`));
  }, []);

  // The search index loads when the browser is idle, or at once when the search box or Random is touched.
  const warmIndex = useCallback(() => void loadObjectIndex().then(setObjectIndex), []);
  useEffect(() => {
    const idle = window.requestIdleCallback ?? ((f: () => void) => window.setTimeout(f, 1500));
    idle(warmIndex, { timeout: 4000 });
  }, [warmIndex]);
  const space = useMemo(() => searchSpace(objectIndex, presets), [objectIndex, presets]);

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
      { ra: target.ra, dec: target.dec, pmra: target.pmra, pmdec: target.pmdec, ref_epoch: target.ref_epoch, name: target.name ?? null, fov_arcmin: fov, ...(target.moving ?? {}) },
      setProgress,
      ctrl.signal,
    )
      .then((data) => {
        metaId.current = data.meta.id;
        rememberPlace({ name: target.name ?? null, ra: target.ra, dec: target.dec, pmra: target.pmra, pmdec: target.pmdec, ref_epoch: target.ref_epoch, preset: spec.kind === "preset" ? spec.id : null });
        setCatalogs({});
        setCatalogStatus({});
        setExp({ key: "", stack: null, error: null });
        setSource(null);
        setAnalysis(null);
        setVerify(null);
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
  }, [target?.ra, target?.dec, target?.pmra, target?.pmdec, target?.ref_epoch, target?.moving?.object_name, target?.moving?.naif_id, fov, reloadKey]);

  const derived = useMemo(() => (seq ? new Derived(seq) : null), [seq]);
  const meta = seq?.meta ?? null;
  /** Co-moving with a solar-system object: stars trail, catalogues and archival images do not apply. */
  const comoving = meta?.grid.frame === "comoving";
  useEffect(() => {
    setSel(null);
    setHoverKey(null);
  }, [meta?.id]);
  const playable = useMemo(() => (derived ? playableEpochs(derived, view) : []), [derived, view.mode, view.band, view.ref, view.ends, view.partial]); // eslint-disable-line react-hooks/exhaustive-deps
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
  // The spectrum tab uses the server's spectral analysis; it needs the raw table only on an older server.
  const [legacySpectrum, setLegacySpectrum] = useState(false);
  const wantPhotometry = !!seq && !!drawer && drawer !== "periodogram" && (drawer !== "spectrum" || legacySpectrum);
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

  // ---- long baseline: 2MASS / AllWISE / NEOWISE frames before the SPHEREx epochs (Blink, flat view)
  const archFov = useMemo(() => (meta ? archivalFov(target ?? meta.target, meta.grid.fov_arcmin) : null), [meta?.id, target?.pmra, target?.pmdec]); // eslint-disable-line react-hooks/exhaustive-deps
  const archKey = view.longBase && meta && !comoving && !embed ? `${meta.id}|${archFov ?? ""}` : "";
  const [archival, setArchival] = useState<{ key: string; meta: ArchivalMeta | null; error: string | null; progress: Progress | null }>({ key: "", meta: null, error: null, progress: null });
  useEffect(() => {
    if (!archKey || !meta) return;
    const ctrl = new AbortController();
    setArchival({ key: archKey, meta: null, error: null, progress: { stage: "queued", done: 0, total: 0, elapsed: 0 } });
    loadArchival(meta.id, archFov, (pr) => setArchival((a) => (a.key === archKey ? { ...a, progress: pr } : a)), ctrl.signal)
      .then((m) => setArchival({ key: archKey, meta: m, error: null, progress: null }))
      .catch((e) => !ctrl.signal.aborted && setArchival({ key: archKey, meta: null, error: String(e?.message || e), progress: null }));
    return () => ctrl.abort();
  }, [archKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const arch = archival.key === archKey && archKey ? archival : null;
  const archFrames = useMemo(() => (arch?.meta ? selectFrames(arch.meta.frames, view.archBands) : []), [arch?.meta, view.archBands.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const lbActive = !!arch?.meta && view.mode === "blink" && !is3d;
  const curArch = lbActive && view.arch >= 0 ? archFrames.find((f) => f.index === view.arch) ?? null : null;
  const [archTick, setArchTick] = useState(0);
  useEffect(() => {
    if (!lbActive || !meta) return;
    const ctrl = new AbortController();
    const order = curArch ? [curArch, ...archFrames.filter((f) => f !== curArch)] : archFrames;
    (async () => {
      for (const f of order) {
        if (ctrl.signal.aborted) return;
        if (cachedArchivalFrame(meta.id, archFov, f.index)) continue;
        await fetchArchivalFrame(meta.id, archFov, f.index, ctrl.signal).then(() => setArchTick((t) => t + 1), () => undefined);
      }
    })();
    return () => ctrl.abort();
  }, [lbActive, archFrames, curArch?.index]); // eslint-disable-line react-hooks/exhaustive-deps
  const archLimits = useRef(new Map<string, [number, number]>());
  const archDisplay: Display | null = useMemo(() => {
    if (!curArch || !meta) return null;
    const img = cachedArchivalFrame(meta.id, archFov, curArch.index);
    if (!img) return null;
    const group = archFrames.filter((f) => stretchGroup(f) === stretchGroup(curArch)).map((f) => cachedArchivalFrame(meta.id, archFov, f.index)).filter((a): a is Float32Array => !!a);
    const key = `${meta.id}|${archFov}|${stretchGroup(curArch)}|${group.length}|${view.lo}|${view.hi}`;
    let lim = archLimits.current.get(key);
    if (!lim) {
      const [a, b] = percentiles(group, [view.lo, view.hi]);
      lim = [a, b > a ? b : a + 1e-6];
      archLimits.current.set(key, lim);
    }
    return { kind: "scalar", img, lo: lim[0], hi: lim[1], unit: "MJy/sr" };
  }, [curArch, archTick, view.lo, view.hi]); // eslint-disable-line react-hooks/exhaustive-deps
  const archOff = lbActive && arch?.meta && meta ? gridOffset(arch.meta.grid, meta.grid) : 0;
  // SPHEREx frames embedded in the (wider) archival grid.
  const sxDisplay = useMemo((): Display | null => {
    if (!lbActive || !display || !arch?.meta || !meta) return display;
    const N = arch.meta.grid.n;
    if (display.kind === "scalar") return { ...display, img: embedPlane(display.img, meta.grid.n, N, archOff) };
    if (display.kind === "diverging") return { ...display, img: embedPlane(display.img, meta.grid.n, N, archOff) };
    return display;
  }, [lbActive, display, arch?.meta, archOff]); // eslint-disable-line react-hooks/exhaustive-deps
  const lastSky = useRef<Display | null>(null);
  const skyDisplay = lbActive ? (curArch ? archDisplay ?? lastSky.current : sxDisplay) : display;
  if (lbActive && skyDisplay) lastSky.current = skyDisplay;
  /** One timeline for playback: archival frames, then the playable SPHEREx epochs. */
  const sequenceItems: ({ a: number } | { e: number })[] = lbActive ? [...archFrames.map((f) => ({ a: f.index })), ...playable.map((e) => ({ e }))] : [];
  const seqPos = lbActive ? (curArch ? archFrames.indexOf(curArch) : archFrames.length + Math.max(0, playable.indexOf(view.epoch))) : -1;
  const goItem = (it: { a: number } | { e: number }) => ("a" in it ? set({ arch: it.a }) : set({ arch: -1, epoch: it.e }));
  const archStatus = (() => {
    if (!view.longBase) return null;
    if (arch?.error) return null;
    const pr = arch?.progress;
    if (pr) return pr.stage === "fetch" && pr.total ? `Building: ${pr.done}/${pr.total} survey images` : pr.stage === "search" ? "Searching 2MASS, AllWISE and NEOWISE" : "Queued on the server";
    if (arch?.meta) return view.mode !== "blink" ? "Plays in Blink mode" : `${archFrames.length} frames in ${view.archBands.join(" / ")} · ${arch.meta.grid.fov_arcmin.toFixed(1)}′ field`;
    return null;
  })();

  // ---- field tools: candidates, known objects, Track & Stack (their marks and stacked image go on the sky)
  const ft = useFieldTools(meta, view.epoch);

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
  const wanted = view.overlays.filter((o): o is CatalogId => (CATALOG_IDS as readonly string[]).includes(o));
  const catalogCtrl = useRef<AbortController | null>(null);
  useEffect(() => {
    catalogCtrl.current?.abort(); // a new sequence cancels catalog queries for the old one
    catalogCtrl.current = new AbortController();
    return () => catalogCtrl.current?.abort();
  }, [meta?.id]);
  useEffect(() => {
    if (!meta || comoving) return;
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
      if (sequenceItems.length > 1) {
        goItem(sequenceItems[(seqPos + 1) % sequenceItems.length]);
      } else if (view.mode === "exp") {
        if (nExp > 1) setExpIndex((i) => (i + 1) % nExp);
      } else if (view.mode !== "stack" && playable.length > 1) {
        setView((v) => ({ ...v, epoch: stepEpoch(playable, v.epoch, 1) ?? v.epoch }));
      }
    }, view.speed);
    return () => window.clearInterval(t);
  }, [playing, derived, view.mode, view.speed, playable, nExp, sequenceItems.length, seqPos]); // eslint-disable-line react-hooks/exhaustive-deps

  const step = (dir: 1 | -1) => {
    if (sequenceItems.length > 1) {
      goItem(sequenceItems[(seqPos + dir + sequenceItems.length) % sequenceItems.length]);
      return;
    }
    if (view.mode === "exp") {
      if (nExp) setExpIndex((i) => (i + dir + nExp) % nExp);
      return;
    }
    const e = stepEpoch(playable, view.epoch, dir);
    if (e !== undefined) set({ epoch: e });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName ?? "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape") {
        if (sel) setSel(null);
        else setDrawer(null);
      } else if (e.shiftKey && e.key.startsWith("Arrow")) {
        e.preventDefault();
        stepField(({ ArrowUp: "N", ArrowDown: "S", ArrowLeft: "E", ArrowRight: "W" } as const)[e.key as "ArrowUp"] ?? "N");
      } else if (e.key === " ") {
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
      else if (e.key.toLowerCase() === "g") toggleOverlay("grid");
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
    // The long baseline is a heavy server build: it is never carried over to a new target.
    setView((v) => ({ ...v, epoch: 0, ref: -1, stack: null, mode: v.mode === "exp" ? "blink" : v.mode, longBase: false, arch: -1 }));
    setPlaying(true);
    setHero(false);
  };
  const choosePreset = (id: string) => {
    setSpec({ kind: "preset", id });
    setFovOverride(null);
    resetForNewTarget();
  };
  /** Open what a search suggestion or Random picked. Only now does anything load. */
  const pick = (p: Pick, recent?: Recent) => {
    if (p.kind === "preset") choosePreset(p.id);
    else {
      setSpec({ kind: "coords", target: p.target });
      if (p.target.fov_arcmin) setFovOverride(null); // extended objects come with a field that fits them
      resetForNewTarget();
    }
    if (recent) setRecents(saveRecent(recent));
  };
  /** A comet or asteroid: find it in SPHEREx exposures (IRSA MOST, polled), then build a co-moving sequence. */
  const [moving, setMoving] = useState<MovingState | null>(null);
  const movingCtrl = useRef<AbortController | null>(null);
  const findMoving = (q: { name?: string; naif_id?: number }, label: string) => {
    movingCtrl.current?.abort();
    const ctrl = new AbortController();
    movingCtrl.current = ctrl;
    setHero(false);
    setMoving({ state: "search", query: label, progress: null });
    searchMoving(q, (pr) => !ctrl.signal.aborted && setMoving((m) => (m?.state === "search" ? { ...m, progress: pr } : m)), ctrl.signal)
      .then((res) => {
        if (ctrl.signal.aborted) return;
        const { target: t } = movingRequest(res);
        setMoving(null);
        setSpec({ kind: "coords", target: t });
        setFovOverride(null);
        resetForNewTarget();
        setRecents(saveRecent({ label: t.name ?? label, sub: `Solar system · co-moving · ${res.n_exposures} exposures`, pick: { kind: "target", target: t } }));
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        if (e instanceof AmbiguousObject) setMoving({ state: "choices", query: label, message: e.message, choices: e.choices });
        else setMoving({ state: "error", query: label, message: String(e?.message || e) });
      });
  };
  const closeMoving = () => {
    movingCtrl.current?.abort();
    setMoving(null);
  };
  const lastRandom = useRef<string | undefined>(undefined);
  /** Random: an object (of a kind, or weighted over kinds) or, for "field" and a quarter of plain clicks, a patch of sky. */
  const random = async (what: Facet | "field" | null) => {
    const idx = objectIndex ?? (await loadObjectIndex());
    const all = searchSpace(idx, presets);
    const obj = what === "field" || (what === null && Math.random() < 0.25) ? null : randomObject(all, what, lastRandom.current);
    if (obj) {
      lastRandom.current = obj.name;
      pick(pickFor(obj), { label: obj.name, sub: describe(obj), pick: pickFor(obj) });
    } else {
      const f = randomField();
      pick(f, { label: f.kind === "target" ? f.target.name ?? "Patch of sky" : "", sub: "Random patch of sky", pick: f });
    }
  };
  /** A new field at a position (the field of view is kept). */
  const goTo = (pl: Place) => {
    if (pl.preset && presets.some((t) => t.id === pl.preset)) return choosePreset(pl.preset);
    setSpec({ kind: "coords", target: { ra: pl.ra, dec: pl.dec, pmra: pl.pmra, pmdec: pl.pmdec, ref_epoch: pl.ref_epoch, name: pl.name } });
    setFovOverride(fov);
    resetForNewTarget();
  };
  /** Slew: a new field half a field away (east is left on the sky view). A fixed point (every stepped field
   *  is one) steps from the requested centre, so quick presses add up before the next field has loaded; a
   *  moving target steps from the field on screen, which sits where the target was at the observations. */
  const stepField = (dir: StepDir) => {
    const fixed = target && !target.pmra && !target.pmdec;
    const c = fixed ? target : meta?.grid ?? target;
    if (!c) return;
    const h = fov / 120;
    const [xi, eta] = { N: [0, h], S: [0, -h], E: [h, 0], W: [-h, 0] }[dir];
    const [ra, dec] = tanToWorld(c.ra, c.dec, xi, eta);
    goTo({ name: null, ra, dec, pmra: 0, pmdec: 0, ref_epoch: 2000 });
  };
  const toggleOverlay = (o: (typeof view.overlays)[number]) =>
    setView((v) => ({ ...v, overlays: v.overlays.includes(o) ? v.overlays.filter((x) => x !== o) : [...v.overlays, o] }));
  const prefetchPreset = (id: string) => {
    const t = presets.find((p) => p.id === id);
    if (t) prefetchSequence({ ra: t.ra, dec: t.dec, pmra: t.pmra, pmdec: t.pmdec, ref_epoch: t.ref_epoch, name: t.name ?? null, fov_arcmin: t.fov_arcmin ?? 6 });
  };
  /** Forced photometry of a catalogue source or a pinned position; its curves replace the target's in the drawer. */
  const measureAt = async (key: string, label: string, pos: { ra: number; dec: number; pmra: number; pmdec: number; ref_epoch: number }) => {
    if (!meta) return;
    const res = await measureSource(meta.id, { ...pos, name: label }).catch((e) => {
      throw e instanceof ApiError && (e.status === 404 || e.status === 405) ? new Error("Source photometry is not available on this server yet.") : e;
    });
    setSource({ key, label, photometry: res.photometry });
    setSel(null);
    setHero(false);
    setDrawer((d) => (d && d !== "details" ? d : "spectrum"));
  };
  const measureSelection = (s: Selection) => {
    if (s.kind === "source") {
      const src = s.src;
      return measureAt(`${s.cat}:${src.id}`, String(src.id), { ra: src.ra, dec: src.dec, pmra: src.pmra ?? 0, pmdec: src.pmdec ?? 0, ref_epoch: src.epoch });
    }
    if (s.kind === "point") return measureAt(`pt:${s.ra.toFixed(6)},${s.dec.toFixed(6)}`, `${s.ra.toFixed(5)} ${s.dec >= 0 ? "+" : ""}${s.dec.toFixed(5)}`, { ra: s.ra, dec: s.dec, pmra: 0, pmdec: 0, ref_epoch: 2000 });
    return Promise.resolve();
  };
  /** Open the automatic analysis of a selection in the drawer. */
  const analyzeSelection = (s: Selection, world: [number, number] | null) => {
    if (!world) return;
    let subject: AnalysisSubject;
    if (s.kind === "target" && target) subject = { key: `target:${meta?.id}`, label: target.name ?? "Target", pos: { ra: target.ra, dec: target.dec, pmra: target.pmra, pmdec: target.pmdec, ref_epoch: target.ref_epoch, name: target.name ?? null } };
    else if (s.kind === "source") subject = { key: `${s.cat}:${s.src.id}`, label: String(s.src.id), pos: { ra: s.src.ra, dec: s.src.dec, pmra: s.src.pmra ?? 0, pmdec: s.src.pmdec ?? 0, ref_epoch: s.src.epoch, name: String(s.src.id) } };
    else subject = { key: `pt:${world[0].toFixed(6)},${world[1].toFixed(6)}`, label: `${world[0].toFixed(5)} ${world[1] >= 0 ? "+" : ""}${world[1].toFixed(5)}`, pos: { ra: world[0], dec: world[1] } };
    setAnalysis(subject);
    setSel(null);
    setHero(false);
    setDrawer("analysis");
  };
  /** Per-exposure fits at a selection; a field candidate brings its trial motion along. */
  const verifySelection = (world: [number, number] | null, label: string) => {
    if (!world) return;
    const c = ft.cands.find((x) => x.key === ft.active);
    const near = c && Math.abs(c.ra - world[0]) < 1e-4 && Math.abs(c.dec - world[1]) < 1e-4 ? c : null;
    setVerify({ key: `v:${world[0].toFixed(6)},${world[1].toFixed(6)}:${near?.rate ?? 0}`, label: near ? `${near.title}` : label, ra: world[0], dec: world[1], rate: near?.rate, pa: near?.pa, epoch: near?.epoch, mjd_ref: near?.mjd });
    setSel(null);
    setDrawer("verify");
  };
  /** A candidate from the field list or a mark on the sky: select it there and fly to it. */
  const pickCandidate = (c: Candidate) => {
    ft.setActive(c.key);
    if (is3d) set({ view3d: "off" });
    if (c.epoch != null && c.epoch !== view.epoch) set({ epoch: c.epoch });
    setSel({ kind: "point", ra: c.ra, dec: c.dec });
    setFocus({ ra: c.ra, dec: c.dec, key: Date.now() });
    setHero(false);
  };
  const onMark = (key: string) => {
    const c = ft.cands.find((x) => x.key === key);
    if (c) return pickCandidate(c);
    if (key.startsWith("s:") && ft.search.state === "done") {
      const t = ft.search.result.candidates.find((x) => `s:${x.id}` === key);
      if (t) {
        ft.setStack({ ...ft.stack, rate: t.rate_arcsec_per_hr, pa: t.pa_deg });
        setSel({ kind: "point", ra: t.ra, dec: t.dec });
      }
      return;
    }
    const m = ft.marks.find((x) => x.key === key);
    if (m && meta) {
      const [ra, dec] = pixelToWorld(meta.grid, m.x, m.y);
      setSel({ kind: "point", ra, dec });
    }
  };
  const dismissHero = () => {
    setHero(false);
    safeStorage(INTRO_KEY, "1");
  };

  // ---- presentation
  const epochInfo = meta?.epochs[view.epoch];
  const exposureRows = fullMeta?.exposures ?? [];
  const year = (() => {
    if (curArch) return curArch.epoch_year;
    if (meta && view.mode === "exp" && expStack?.indices.length) {
      const ex = exposureRows[expStack.indices[Math.min(expIndex, expStack.indices.length - 1)]];
      if (ex) return mjdToYear(ex.mjd);
    }
    return epochInfo ? mjdToYear(epochInfo.mjd_mean) : 2026;
  })();
  const span: [number, number] | null =
    meta && meta.epochs.length > 1 ? [mjdToYear(meta.epochs[0].mjd_mean), mjdToYear(meta.epochs[meta.epochs.length - 1].mjd_mean)] : null;
  // In the long baseline the track runs from the archival positions (where the target is in the field) on.
  const archInField = lbActive ? archFrames.filter((f) => f.target_in_field && f.target_px) : [];
  const sxTrack: [number, number][] = meta ? meta.target.track.map((t) => [t.x + archOff, t.y + archOff]) : [];
  const track: [number, number][] = lbActive ? [...archInField.map((f) => f.target_px as [number, number]), ...sxTrack] : sxTrack;
  const trackIndex = curArch ? archInField.indexOf(curArch) : archInField.length + view.epoch;
  const trackPoint = meta ? meta.target.track.find((t) => t.epoch === view.epoch) ?? meta.target.track[0] : undefined;
  let targetPx: [number, number] | null = curArch
    ? curArch.target_in_field ? curArch.target_px : null
    : trackPoint ? [trackPoint.x + archOff, trackPoint.y + archOff] : null;
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
      targetPx = [ex.target_px[0] + archOff, ex.target_px[1] + archOff];
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
    if (curArch) {
      const at0 = propagate({ ra: meta.target.ra, dec: meta.target.dec, epoch: meta.target.ref_epoch, pmra: meta.target.pmra, pmdec: meta.target.pmdec }, curArch.epoch_year);
      hud = {
        epoch: frameLabel(curArch),
        dates: `${curArch.date.slice(0, 10)}${curArch.n_images > 1 ? ` · ${curArch.n_images} images` : ""}`,
        spectral: `${curArch.survey} ${curArch.band} · λ ${curArch.wl_um.toFixed(2)} µm`,
        frames: curArch.target_in_field ? "target in field" : "target outside the field",
        target: at0,
      };
    }
    if (comoving) hud.extra = hud.extra ? `${hud.extra} · co-moving: stars trail` : "co-moving: stars trail";
    if (ft.stackDisplay && !lbActive) hud.extra = `Track & Stack ${ft.stack.rate.toFixed(2)}″/h PA ${ft.stack.pa.toFixed(0)}°`;
  }
  const flagBits = meta ? view.flagBits ?? defaultFlagBits(meta.flag_bits) : [];
  const flagMask = flagBits.reduce((m, b) => m | (1 << b), 0);
  const flags = derived && view.overlays.includes("flags") && !lbActive ? derived.flagsOr(view.epoch, view.mode === "rgb" ? "rgb" : view.band) : null;
  const overlays = useMemo(() => new Set(view.overlays), [view.overlays]);
  const canPlay = sequenceItems.length > 1 || (view.mode === "exp" ? nExp > 1 : view.mode !== "stack" && playable.length > 1);
  const errorText = error ?? presetsError ?? (unknownPreset ? `Unknown target "${spec.kind === "preset" ? spec.id : ""}"` : null);

  const dateLabel = hud ? `${hud.epoch}${hud.extra ? ` · ${hud.extra}` : ""}` : "";
  const bandShort = view.mode === "rgb" ? "RGB" : view.band === "all" ? "All" : view.band;
  const openDetails = (tab: DrawerTab = "spectrum") => {
    setHero(false);
    setDrawer((d) => (d ? null : tab));
  };
  const setFlagsOn = (on: boolean) => set({ overlays: on ? [...view.overlays.filter((o) => o !== "flags"), "flags"] : view.overlays.filter((o) => o !== "flags") });

  // The field centre for the coordinate box: a fixed position as requested (so it shows at once while the field
  // builds), a moving target's field once built (centred where the target was during the observations).
  const fieldCentre: [number, number] | null =
    target && (target.pmra || target.pmdec) && meta && meta.target.ra === target.ra && meta.target.dec === target.dec
      ? [meta.grid.ra, meta.grid.dec]
      : target
        ? [target.ra, target.dec]
        : null;

  // ---- the selected object, target or pin, and its card
  const targetWorld = hud?.target ?? null;
  const targetName = target?.name || meta?.target.name || "Field centre";
  const selWorld = sel ? selectionWorld(sel, year, targetWorld) : null;
  /** The same object in the other catalogues shown (nearest within 3″; 6″, about a SPHEREx pixel, for a pin). */
  const counterparts = useMemo(() => {
    if (!sel || !selWorld) return [];
    const radius = (sel.kind === "point" ? 6 : 3) / 3600;
    const out: { cat: string; src: CatalogSource; sep: number }[] = [];
    for (const cat of CATALOG_IDS) {
      if (!view.overlays.includes(cat) || (sel.kind === "source" && sel.cat === cat)) continue;
      let best: (typeof out)[number] | null = null;
      for (const src of catalogs[cat] ?? []) {
        const [ra, dec] = propagate(src, year); // at the shown date: fast movers are far from their catalogue position
        if (Math.abs(dec - selWorld[1]) > radius) continue;
        const d = separation(selWorld[0], selWorld[1], ra, dec);
        if (d < radius && (!best || d < best.sep)) best = { cat, src, sep: d };
      }
      if (best) out.push(best);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, selWorld?.[0], selWorld?.[1], catalogs, view.overlays, year]);
  const card =
    sel && meta ? (
      <PickCard
        sel={sel}
        world={selWorld}
        year={year}
        targetName={targetName}
        target={target}
        targetWorld={targetWorld}
        value={sel.kind === "point" ? valueAt(display, meta.grid.n, ...worldToPixel(meta.grid, sel.ra, sel.dec)) : null}
        counterparts={counterparts}
        fovArcmin={fov}
        onClose={() => setSel(null)}
        onSelect={setSel}
        onDetails={() => {
          setSel(null);
          setSource(null);
          setHero(false);
          setDrawer("spectrum");
        }}
        onMeasure={sel.kind !== "target" ? () => measureSelection(sel) : undefined}
        onAnalyze={() => analyzeSelection(sel, selWorld)}
        onVerify={sel.kind !== "target" ? () => verifySelection(selWorld, sel.kind === "source" ? String(sel.src.id) : "Pinned position") : undefined}
        onCentre={sel.kind !== "target" && selWorld ? () => goTo({ name: sel.kind === "source" ? String(sel.src.id) : null, ra: selWorld[0], dec: selWorld[1], pmra: 0, pmdec: 0, ref_epoch: 2000 }) : undefined}
        onFollow={
          sel.kind === "source" && totalPm(sel.src) >= 20
            ? () => goTo({ name: String(sel.src.id), ra: sel.src.ra, dec: sel.src.dec, pmra: sel.src.pmra ?? 0, pmdec: sel.src.pmdec ?? 0, ref_epoch: sel.src.epoch })
            : undefined
        }
      />
    ) : null;


  // Catalogue sources for the 3D views, at the shown date; in the time cube fast movers also get their
  // position at every layer, so their path through the cube can be drawn beside the stars' columns.
  const objects3d: Object3D[] = useMemo(() => {
    if (!is3d || !meta) return [];
    const g = meta.grid;
    const at: { year: number; z: number }[] = [];
    if (expCube && expRows.length > 1) {
      const t0 = expRows[0].mjd;
      const dt = expRows[expRows.length - 1].mjd - t0;
      for (const ex of expRows) at.push({ year: mjdToYear(ex.mjd), z: dt > 0 ? (ex.mjd - t0) / dt : 0.5 });
    } else if (view.view3d === "time" && !expCube) for (const s of slices) at.push({ year: mjdToYear(meta.epochs[s.view.epoch].mjd_mean), z: s.z });
    return catalogMarkers(g, catalogs, overlays, year, null).map((m) => {
      const tip = markerTip(m.cat, m.src);
      return {
        key: sourceKey(m.cat, m.src),
        cat: m.cat,
        name: tip.name,
        sub: tip.sub,
        x: m.x,
        y: m.y,
        big: m.pm > HIGH_PM,
        label: m.cat === "simbad",
        rank: Number(m.src.refs ?? 0),
        path: m.pm > HIGH_PM && at.length > 1 ? at.map((a) => ({ ...xy(worldToPixel(g, ...propagate(m.src, a.year))), z: a.z })) : undefined,
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [is3d, meta, catalogs, overlays, year, view.view3d, expCube, expRows, slices]);
  /** Open a catalogue source on the flat sky, where its card and the fly-to live. */
  const openSource = (cat: string, src: CatalogSource) => {
    const [ra, dec] = propagate(src, year);
    if (is3d) set({ view3d: "off" });
    setSel({ kind: "source", cat, src });
    setFocus({ ra, dec, key: Date.now() });
    setHero(false);
  };

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
  // Short lines: they sit left of the dock (the rail shows the detector, the dock the epoch).
  const label3d = (() => {
    const first = slices[0];
    const last = slices[slices.length - 1];
    if (expCube && layers.length)
      return { date: `Time cube · ${layers.length} exposures`, sub: `${layers[0].label.slice(0, 5)} → ${layers[layers.length - 1].label.slice(0, 5)} · ${view.band}` };
    if (expCube) return { date: "Loading exposures…", sub: undefined };
    if (view.view3d === "time" && first)
      return { date: `Time cube · ${slices.length} epochs`, sub: `${first.label} → ${last.label} · ${sliceMode}` };
    if (view.view3d === "bands" && first && meta) {
      const wl = (id: string) => meta.bands.find((b) => b.id === id)!;
      const range = `${wl(first.view.band).wl_min.toFixed(2)}–${wl(last.view.band).wl_max.toFixed(2)} µm`;
      return { date: `λ cube · ${slices.length} detectors`, sub: `${range} · ${sliceMode}` };
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
        objects={objects3d}
        highlight={hoverKey}
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
        onPickObject={(key) => {
          for (const cat of CATALOG_IDS) {
            const src = view.overlays.includes(cat) ? catalogs[cat]?.find((s) => sourceKey(cat, s) === key) : undefined;
            if (src) return openSource(cat, src);
          }
        }}
        onInteract={() => hero && setHero(false)}
        shiftX={hero && !mobile && presets.length ? 180 : 0}
      />
    </Suspense>
  ) : (
    <SkyView
      grid={lbActive && arch?.meta ? arch.meta.grid : meta?.grid ?? null}
      display={!lbActive && ft.stackDisplay ? ft.stackDisplay : skyDisplay}
      view={view}
      overlays={overlays}
      flags={flags}
      flagMask={flagMask}
      catalogs={catalogs}
      year={year}
      span={span}
      targetPx={targetPx}
      loading={target?.name ? { name: target.name, note: target.description } : null}
      targetWorld={targetWorld}
      track={track}
      trackIndex={trackIndex}
      dateLabel={dateLabel}
      progress={progress}
      lockKey={meta?.id ?? "none"}
      error={errorText}
      onRetry={() => setReloadKey((k) => k + 1)}
      onZoom={(zoom) => set({ zoom })}
      onInteract={() => hero && setHero(false)}
      selection={sel}
      onSelect={(s) => {
        setSel(s);
        if (s) setHero(false);
      }}
      card={card}
      marks={lbActive ? undefined : ft.marks}
      onMark={onMark}
      ruler={ruler}
      magnifier={magnifier}
      invert={view.invert}
      highlight={hoverKey}
      focus={focus}
      onStep={embed ? undefined : stepField}
      shiftX={hero && !mobile && presets.length ? 180 : 0}
      frame={drawer ? undefined : mobile ? MOBILE_FRAME : hudLifted ? MID_FRAME : undefined}
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
        current={curArch ? -1 : view.epoch}
        available={derived.playable(view.mode === "rgb" ? "rgb" : view.band)}
        refEpoch={view.mode === "diff" ? refEpoch : null}
        stackSel={view.stack}
        onSelect={(e) => set({ epoch: e, arch: -1 })}
        onToggleStack={(e) => {
          const cur = stackEpochs(derived, view);
          const next = cur.includes(e) ? cur.filter((x) => x !== e) : [...cur, e].sort((a, b) => a - b);
          set({ stack: next.length ? next : null });
        }}
        exposures={view.mode === "exp" && nExp ? { count: nExp, index: Math.min(expIndex, nExp - 1), onIndex: setExpIndex } : null}
        date={hud?.dates ?? ""}
        archival={
          view.longBase && view.mode === "blink" && !is3d && !comoving ? (
            <ArchivalStrip frames={archFrames} current={curArch?.index ?? null} onSelect={(i) => set({ arch: i })} status={arch?.error ?? archStatus} />
          ) : undefined
        }
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
    <div
      className={`app earth${mobile ? " is-mobile" : ""}${drawer ? " drawer-open" : ""}${hero ? " hero-on" : ""}${hudLifted ? " hud-lifted" : ""}`}
      style={hudClear ? ({ "--hud-clear": `${hudClear}px` } as React.CSSProperties) : undefined}
    >
      <ProgressBar progress={progress ?? (planesBusy ? { stage: "download", done: 0, total: 0, elapsed: 0 } : null)} />
      {sky}
      <TopLeft
        space={space}
        indexReady={!!objectIndex}
        current={spec.kind === "preset" ? `p:${spec.id}` : `n:${spec.target.name ?? ""}`}
        recents={recents}
        fov={fov}
        compact={mobile}
        onPick={pick}
        onRandom={random}
        onWarm={warmIndex}
        onMoving={(name) => findMoving({ name }, name)}
        onFov={(f) => setFovOverride(f)}
        coords={<CoordField centre={fieldCentre} onGo={goTo} />}
      />
      {moving && <MovingCard st={moving} onChoose={(c: MovingChoice) => findMoving({ naif_id: c.naif_id }, c.name)} onClose={closeMoving} />}
      {hero && presets.length > 0 && <Hero presets={presets} onPick={choosePreset} onDismiss={dismissHero} onPrefetch={prefetchPreset} />}
      <Rail
        bandLabel={bandShort}
        goTo={(close: () => void) => (
          <GoToPanel current={targetWorld ?? (meta ? [meta.grid.ra, meta.grid.dec] : null)} currentName={targetName} presets={presets} fov={fov} onGo={goTo} onPreset={choosePreset} close={close} />
        )}
        objects={
          meta ? (
            <ObjectsPanel
              grid={meta.grid}
              catalogs={catalogs}
              status={catalogStatus}
              overlays={view.overlays}
              onToggle={toggleOverlay}
              year={year}
              targetWorld={targetWorld}
              onHover={setHoverKey}
              onPick={openSource}
            />
          ) : undefined
        }
        detector={meta && derived ? <DetectorPanel meta={meta} derived={derived} view={view} set={set} refEpoch={refEpoch} /> : undefined}
        display={
          meta ? (
            <div className="panel-stack">
              <DisplayPanel view={view} set={set} />
              <MeasureTools ruler={ruler} onRuler={setRuler} magnifier={magnifier} onMagnifier={setMagnifier} invert={view.invert} onInvert={(invert) => set({ invert })} />
              <PlaybackOptions meta={meta} mode={view.mode} speed={view.speed} onSpeed={(speed) => set({ speed })} ends={view.ends} onEnds={(ends) => set({ ends })} partial={view.partial} onPartial={(partial) => set({ partial })} />
            </div>
          ) : undefined
        }
        layers={
          meta ? (
            <>
              <LayersPanel view={view} set={set} catalogStatus={catalogStatus} />
              <LongBaselineControls
                on={view.longBase}
                onToggle={(on) => set(on ? { longBase: true, mode: "blink", view3d: "off", arch: -1 } : { longBase: false, arch: -1 })}
                bands={view.archBands}
                onBands={(archBands) => set({ archBands, arch: -1 })}
                unavailable={comoving ? "Not for co-moving sequences (the field follows the object)" : null}
                status={archStatus}
                error={arch?.error ?? null}
              />
            </>
          ) : undefined
        }
        field={meta && !comoving ? <FieldPanel t={ft} onPick={pickCandidate} epochLabel={`visit ${view.epoch + 1}`} /> : undefined}
        share={meta ? <SharePanel link={() => viewLink(spec, fov, view, false)} embedLink={() => viewLink(spec, fov, view, true)} exportUrl={(k) => exportUrl(meta.id, k)} /> : undefined}
        detailsOpen={!!drawer}
        onDetails={meta ? () => openDetails("spectrum") : undefined}
        is3d={is3d}
        on3d={meta ? toggle3d : undefined}
      />
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
          onNeedPhotometry={() => setLegacySpectrum(true)}
          analysis={analysis}
          verify={verify}
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
