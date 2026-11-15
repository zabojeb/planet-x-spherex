/** What the viewer shows for the current mode: pure functions over Derived (no React). */
import { formatMonth } from "./astro";
import type { BandSel, Derived } from "./render";
import type { ViewState } from "./state";
import type { BandInfo, ExposureStack, PlaneKey } from "./types";

export type Display =
  | { kind: "scalar"; img: ArrayLike<number>; lo: number; hi: number; unit: string }
  | { kind: "diverging"; img: ArrayLike<number>; lim: number; unit: string }
  | { kind: "rgb"; ch: [Float32Array, Float32Array, Float32Array]; lims: [number, number][] }
  | { kind: "empty"; message: string };

export interface ExposureView {
  key: string;
  stack: ExposureStack | null;
  index: number;
  error: string | null;
}

export const unitFor = (sel: BandSel) => (sel === "all" ? "S/N" : "MJy/sr");

export const wlRange = (b: BandInfo) => `${b.wl_min.toFixed(2)}–${b.wl_max.toFixed(2)} µm`;

export function bandLabel(sel: BandSel | "rgb", bands: BandInfo[]): string {
  if (sel === "rgb") return "False colour (all detectors)";
  if (sel === "all") return "All detectors (S/N)";
  const b = bands.find((x) => x.id === sel);
  return b ? `${b.id} · ${wlRange(b)}` : sel;
}

/** Human description of the false-colour channels, e.g. "R = D5+D6 · G = D3+D4 · B = D1+D2". */
export function rgbLabel(d: Derived): string {
  return ["R", "G", "B"].map((c, i) => `${c} = ${d.rgbGroups[i].join("+")}`).join(" · ");
}

export const isPartial = (d: Derived, e: number) => !!d.seq.meta.epochs[e]?.partial;

/** Epochs a player cycles through for the current mode and band (partial visits only on request). */
export function playableEpochs(d: Derived, v: ViewState): number[] {
  const all = d.playable(v.mode === "rgb" ? "rgb" : v.band);
  const full = all.filter((e) => !isPartial(d, e));
  const list = v.partial || full.length < 2 ? all : full;
  if (v.ends && list.length > 2 && v.mode !== "stack" && v.mode !== "exp") return [list[0], list[list.length - 1]];
  return list;
}

/** Reference epoch for Difference: the chosen one, else the earliest epoch with at least half the
 *  exposures of the best-covered epoch (a short partial visit makes a noisy reference). */
export function effectiveRef(d: Derived, v: ViewState): number {
  if (v.ref >= 0 && d.has(v.ref, v.band)) return v.ref;
  const any = d.playable(v.band);
  const full = any.filter((e) => !isPartial(d, e));
  const eps = full.length ? full : any;
  const count = (e: number) => {
    const ep = d.seq.meta.epochs[e];
    return v.band === "all" ? ep.n : ep.bands[v.band]?.n ?? 0;
  };
  const best = Math.max(0, ...eps.map(count));
  return eps.find((e) => count(e) >= best / 2) ?? eps[0] ?? 0;
}

export function stackEpochs(d: Derived, v: ViewState): number[] {
  const avail = d.playable(v.band);
  if (!v.stack) return avail;
  const sel = v.stack.filter((e) => avail.includes(e));
  return sel.length ? sel : avail;
}

/** Planes the current view needs: `now` for the frame on screen, `soon` for the frames likely next
 *  (neighbouring epochs while paused, every playable epoch while playing). */
/** Planes the view needs, by urgency: `now` is the image on screen, `after` what is drawn over it (the flag
 *  overlay), `soon` the frames the player or a step will show next. The image is fetched first, so it shows up
 *  without waiting for the overlays. */
export type PlaneNeeds = { now: PlaneKey[]; after: PlaneKey[]; soon: PlaneKey[] };

export function planesNeeded(d: Derived, v: ViewState, playing: boolean, flags: boolean): PlaneNeeds {
  const sel = v.mode === "rgb" ? "rgb" : v.band;
  const now: PlaneKey[] = [];
  const after: PlaneKey[] = [];
  const soon: PlaneKey[] = [];
  const frame = (e: number, out: PlaneKey[], over: PlaneKey[]) => {
    out.push(...d.planesFor(e, sel));
    if (v.mode === "diff") out.push(...d.planesFor(effectiveRef(d, v), sel));
    if (flags) over.push(...d.planesFor(e, sel, "flags"));
  };
  if (v.mode === "exp") return { now, after, soon };
  if (v.mode === "stack") {
    for (const e of stackEpochs(d, v)) {
      now.push(...d.planesFor(e, v.band));
      if (v.band !== "all") now.push(...d.planesFor(e, v.band, "coverage"));
    }
    if (flags) after.push(...d.planesFor(v.epoch, v.band, "flags"));
    return { now, after, soon };
  }
  frame(v.epoch, now, after);
  const list = playableEpochs(d, v);
  const i = list.indexOf(v.epoch);
  const next = playing ? list : [list[i + 1], list[i - 1]].filter((e): e is number => e !== undefined);
  const soonOver: PlaneKey[] = [];
  for (const e of next) if (e !== v.epoch) frame(e, soon, soonOver);
  soon.push(...soonOver); // the next images before their flag maps
  const seen = new Set([...now, ...after].map((k) => `${k.array}|${k.e}|${k.b}`));
  return {
    now,
    after,
    soon: soon.filter((k) => {
      const id = `${k.array}|${k.e}|${k.b}`;
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    }),
  };
}

/** One layer of a 3D cube: the view that produces it and its height in the cube (0 = bottom, 1 = top). */
export interface Slice {
  key: string;
  label: string;
  z: number;
  current: boolean;
  view: ViewState;
}

/** Layers of the cube for the current 3D view. Time cube: every epoch (partial visits included), at its date,
 *  in Blink, Difference or RGB (Stack has no per-epoch frame, so it shows Blink; Exposures with one detector is
 *  built in App from the single exposures of the epoch, and falls back to Blink here). λ cube: every
 *  detector of the current epoch, bottom to top in wavelength order, each with its own stretch. */
export function slices3d(d: Derived, v: ViewState): Slice[] {
  const meta = d.seq.meta;
  if (v.view3d === "time") {
    const mode = v.mode === "stack" || v.mode === "exp" ? "blink" : v.mode;
    const eps = d.playable(mode === "rgb" ? "rgb" : v.band);
    if (!eps.length) return [];
    const t = (e: number) => meta.epochs[e].mjd_mean;
    const t0 = t(eps[0]);
    const span = t(eps[eps.length - 1]) - t0;
    return eps.map((e) => ({
      key: `e${e}`,
      label: formatMonth(t(e)),
      z: span > 0 ? (t(e) - t0) / span : 0.5,
      current: e === v.epoch,
      view: { ...v, mode, epoch: e },
    }));
  }
  if (v.view3d === "bands") {
    const mode = v.mode === "rgb" || v.mode === "exp" ? "blink" : v.mode;
    const ids = meta.bands.map((b) => b.id).filter((b) => (mode === "stack" ? d.playable(b).length > 0 : d.hasBand(v.epoch, b)));
    return ids.map((b, i) => {
      const info = meta.bands.find((x) => x.id === b)!;
      return { key: b, label: `${b} · ${wlRange(info)}`, z: ids.length > 1 ? i / (ids.length - 1) : 0.5, current: b === v.band, view: { ...v, mode, band: b } };
    });
  }
  return [];
}

/** Planes for a cube: every layer now; while playing the λ cube, the other epochs' layers next. */
export function planesNeeded3d(d: Derived, v: ViewState, playing: boolean): PlaneNeeds {
  const seen = new Set<string>();
  const collect = (views: ViewState[]) => {
    const out: PlaneKey[] = [];
    for (const sv of views)
      for (const k of planesNeeded(d, sv, false, false).now) {
        const id = `${k.array}|${k.e}|${k.b}`;
        if (!seen.has(id)) seen.add(id), out.push(k);
      }
    return out;
  };
  if (v.view3d === "time" && v.mode === "exp" && v.band !== "all") return { now: [], after: [], soon: [] }; // single exposures, not planes
  const now = collect(slices3d(d, v).map((s) => s.view));
  if (v.view3d !== "bands" || v.mode === "stack") return { now, after: [], soon: [] };
  const list = playableEpochs(d, v);
  const i = list.indexOf(v.epoch);
  const next = playing ? list : [list[i + 1], list[i - 1]].filter((e): e is number => e !== undefined);
  return { now, after: [], soon: collect(next.filter((e) => e !== v.epoch).flatMap((e) => slices3d(d, { ...v, epoch: e }).map((s) => s.view))) };
}

/** What to draw, or null while the planes of the current frame are still downloading. Stretch limits
 *  are taken over all epochs already in memory, so they settle as more of the sequence arrives. */
export function computeDisplay(d: Derived, v: ViewState, exp: ExposureView): Display | null {
  const sel = v.band;
  if (v.mode === "rgb") {
    if (!d.hasAny(v.epoch)) return { kind: "empty", message: "No data in this epoch" };
    if (!d.ready(v.epoch, "rgb")) return null;
    const epochs = d.playable("rgb").filter((e) => d.ready(e, "rgb"));
    // Black point at the sky level (median) so the noisier long-wavelength channel does not tint the sky red.
    const lims = [0, 1, 2].map((c) =>
      d.limits(`rgb|${c}|${epochs.join(",")}`, () => epochs.map((e) => d.rgb(e)[c]), Math.max(v.lo, 50), v.hi),
    ) as [number, number][];
    return { kind: "rgb", ch: d.rgb(v.epoch), lims };
  }
  if (v.mode === "exp") {
    if (sel === "all") return { kind: "empty", message: "Pick one detector to see its single exposures" };
    if (!d.hasBand(v.epoch, sel)) return { kind: "empty", message: `No ${sel} exposures in this epoch` };
    if (exp.error) return { kind: "empty", message: exp.error };
    if (!exp.stack) return { kind: "empty", message: "Loading exposures…" };
    const k = exp.stack.indices.length;
    const i = Math.min(Math.max(exp.index, 0), k - 1);
    const all = exp.stack.data;
    const [lo, hi] = d.limits(`exp|${exp.key}`, () => [all], v.lo, v.hi);
    return { kind: "scalar", img: all.subarray(i * d.size, (i + 1) * d.size), lo, hi, unit: "MJy/sr" };
  }
  if (v.mode === "stack") {
    const epochs = stackEpochs(d, v);
    if (!epochs.length) return { kind: "empty", message: `No ${sel} data` };
    if (!epochs.every((e) => d.ready(e, sel) && (sel === "all" || d.ready(e, sel, "coverage")))) return null;
    const img = d.stack(sel, epochs);
    const [lo, hi] = d.limits(`stack|${sel}|${epochs.join(",")}`, () => [img], v.lo, v.hi);
    return { kind: "scalar", img, lo, hi, unit: unitFor(sel) };
  }
  if (!d.has(v.epoch, sel)) return { kind: "empty", message: `No ${sel === "all" ? "" : sel + " "}data in this epoch` };
  if (!d.ready(v.epoch, sel)) return null;
  // Difference needs a second visit: with one (a preview of the first visit, or a single-visit field) the visit
  // itself is shown until another arrives.
  const ref = v.mode === "diff" ? effectiveRef(d, v) : -1;
  if (v.mode === "diff" && !(ref === v.epoch && d.playable(sel).length < 2)) {
    if (!d.ready(ref, sel)) return null;
    const others = d.playable(sel).filter((e) => e !== ref && d.ready(e, sel));
    const [a, b] = d.limits(`diff|${sel}|${ref}|${others.join(",")}`, () => (others.length ? others : [ref]).map((e) => d.diff(sel, e, ref)),
      100 - v.hi, v.hi);
    const lim = Math.max(Math.abs(a), Math.abs(b)) || 1;
    return { kind: "diverging", img: d.diff(sel, v.epoch, ref), lim, unit: unitFor(sel) };
  }
  const loaded = d.playable(sel).filter((e) => d.ready(e, sel));
  const [lo, hi] = d.limits(`img|${sel}|${loaded.join(",")}`, () => loaded.map((e) => d.image(e, sel)), v.lo, v.hi);
  return { kind: "scalar", img: d.image(v.epoch, sel), lo, hi, unit: unitFor(sel) };
}
