/** What the viewer shows for the current mode: pure functions over Derived (no React). */
import type { BandSel, Derived } from "./render";
import type { ViewState } from "./state";
import type { BandInfo, ExposureStack } from "./types";

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

export function computeDisplay(d: Derived, v: ViewState, exp: ExposureView): Display {
  const sel = v.band;
  if (v.mode === "rgb") {
    if (!d.hasAny(v.epoch)) return { kind: "empty", message: "No data in this epoch" };
    const epochs = d.playable("rgb");
    // Black point at the sky level (median) so the noisier long-wavelength channel does not tint the sky red.
    const lims = [0, 1, 2].map((c) =>
      d.limits(`rgb|${c}`, () => epochs.map((e) => d.rgb(e)[c]), Math.max(v.lo, 50), v.hi),
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
    const img = d.stack(sel, epochs);
    const [lo, hi] = d.limits(`stack|${sel}|${epochs.join(",")}`, () => [img], v.lo, v.hi);
    return { kind: "scalar", img, lo, hi, unit: unitFor(sel) };
  }
  if (!d.has(v.epoch, sel)) return { kind: "empty", message: `No ${sel === "all" ? "" : sel + " "}data in this epoch` };
  if (v.mode === "diff") {
    const ref = effectiveRef(d, v);
    const others = d.playable(sel).filter((e) => e !== ref);
    const [a, b] = d.limits(`diff|${sel}|${ref}`, () => (others.length ? others : [ref]).map((e) => d.diff(sel, e, ref)),
      100 - v.hi, v.hi);
    const lim = Math.max(Math.abs(a), Math.abs(b)) || 1;
    return { kind: "diverging", img: d.diff(sel, v.epoch, ref), lim, unit: unitFor(sel) };
  }
  const [lo, hi] = d.limits(`img|${sel}`, () => d.playable(sel).map((e) => d.image(e, sel)), v.lo, v.hi);
  return { kind: "scalar", img: d.image(v.epoch, sel), lo, hi, unit: unitFor(sel) };
}
