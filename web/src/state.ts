/** View state, its defaults and the URL that encodes it (the only place that reads or writes the URL). */
import type { BandSel, Cmap, Stretch } from "./render";
import type { FlagBit, Target } from "./types";

export type Mode = "blink" | "diff" | "stack" | "rgb" | "exp";
export const MODES: { id: Mode; label: string; key: string; hint: string }[] = [
  { id: "blink", label: "Blink", key: "b", hint: "Play the epochs like a movie" },
  { id: "diff", label: "Difference", key: "d", hint: "Epoch minus a reference epoch: movers become +/− pairs" },
  { id: "stack", label: "Stack", key: "s", hint: "Average the selected epochs for a deeper image" },
  { id: "rgb", label: "RGB", key: "r", hint: "False colour: red = 3.8–5 µm, green = 1.6–3.8 µm, blue = 0.75–1.6 µm" },
  { id: "exp", label: "Exposures", key: "e", hint: "Single exposures of one epoch: asteroids jump between frames" },
];

export const OVERLAYS = ["target", "track", "gaia", "catwise", "2mass", "flags", "compass"] as const;
export type Overlay = (typeof OVERLAYS)[number];

export interface ViewState {
  mode: Mode;
  band: BandSel;
  epoch: number;
  ref: number;
  speed: number; // ms per frame
  stretch: Stretch;
  lo: number;
  hi: number;
  cmap: Cmap;
  zoom: number; // 0 = fit
  smooth: boolean;
  overlays: Overlay[];
  flagBits: number[] | null; // null = defaults derived from meta (see defaultFlagBits)
  ends: boolean; // blink only first and last epoch
  partial: boolean; // include partial visits in playback
  ylog: boolean; // spectrum y axis
  stack: number[] | null; // null = all epochs
}

export const DEFAULT_VIEW: ViewState = {
  mode: "blink",
  band: "all",
  epoch: 0,
  ref: -1,
  speed: 600,
  stretch: "asinh",
  lo: 1,
  hi: 99.7,
  cmap: "gray",
  zoom: 0,
  smooth: false,
  overlays: ["target", "track", "compass"],
  flagBits: null,
  ends: false,
  partial: false,
  ylog: true,
  stack: null,
};

export type TargetSpec =
  | { kind: "preset"; id: string }
  | { kind: "coords"; target: Target };

export interface UrlState {
  target: TargetSpec | null;
  fov: number | null;
  view: Partial<ViewState>;
  embed: boolean;
  hasParams: boolean;
}

const num = (v: string | null): number | undefined => {
  if (v == null || v === "") return undefined;
  const x = Number(v);
  return Number.isFinite(x) ? x : undefined;
};

export function parseUrl(search = window.location.search): UrlState {
  const q = new URLSearchParams(search);
  let target: TargetSpec | null = null;
  const ra = num(q.get("ra"));
  const dec = num(q.get("dec"));
  if (q.get("target")) target = { kind: "preset", id: q.get("target")! };
  else if (ra !== undefined && dec !== undefined) {
    target = {
      kind: "coords",
      target: {
        ra,
        dec,
        pmra: num(q.get("pmra")) ?? 0,
        pmdec: num(q.get("pmdec")) ?? 0,
        ref_epoch: num(q.get("ref_epoch")) ?? 2000,
        name: q.get("name") || null,
      },
    };
  }
  const view: Partial<ViewState> = {};
  const mode = q.get("mode") as Mode | null;
  if (mode && MODES.some((m) => m.id === mode)) view.mode = mode;
  const band = q.get("band");
  if (band && (band === "all" || /^D\d+$/.test(band))) view.band = band;
  const setNum = (key: keyof ViewState, param: string) => {
    const v = num(q.get(param));
    if (v !== undefined) (view as Record<string, unknown>)[key] = v;
  };
  setNum("epoch", "epoch");
  setNum("ref", "ref");
  setNum("speed", "speed");
  setNum("lo", "lo");
  setNum("hi", "hi");
  setNum("zoom", "zoom");
  const stretch = q.get("stretch");
  if (stretch === "asinh" || stretch === "linear" || stretch === "log") view.stretch = stretch;
  const cmap = q.get("cmap");
  if (cmap === "gray" || cmap === "ice") view.cmap = cmap;
  if (q.has("smooth")) view.smooth = q.get("smooth") === "1";
  if (q.has("ends")) view.ends = q.get("ends") === "1";
  if (q.has("partial")) view.partial = q.get("partial") === "1";
  if (q.has("ylog")) view.ylog = q.get("ylog") !== "0";
  if (q.has("ov")) view.overlays = (q.get("ov") || "").split(",").filter((o): o is Overlay => (OVERLAYS as readonly string[]).includes(o));
  if (q.has("flags")) view.flagBits = (q.get("flags") || "").split(",").map(Number).filter((b) => Number.isInteger(b) && b >= 0 && b < 32);
  if (q.has("stack")) view.stack = (q.get("stack") || "").split(",").map(Number).filter((e) => Number.isInteger(e) && e >= 0);
  return { target, fov: num(q.get("fov")) ?? null, view, embed: q.get("embed") === "1", hasParams: [...q.keys()].length > 0 };
}

const fmt = (x: number, d = 6) => String(Number(x.toFixed(d)));

export function buildQuery(spec: TargetSpec | null, fov: number, view: ViewState, embed = false): string {
  const q = new URLSearchParams();
  if (spec?.kind === "preset") q.set("target", spec.id);
  else if (spec?.kind === "coords") {
    const t = spec.target;
    q.set("ra", fmt(t.ra));
    q.set("dec", fmt(t.dec));
    if (t.pmra || t.pmdec) {
      q.set("pmra", fmt(t.pmra, 2));
      q.set("pmdec", fmt(t.pmdec, 2));
      q.set("ref_epoch", fmt(t.ref_epoch, 3));
    }
    if (t.name) q.set("name", t.name);
  }
  q.set("fov", fmt(fov, 2));
  const d = DEFAULT_VIEW;
  if (view.mode !== d.mode) q.set("mode", view.mode);
  if (view.band !== d.band) q.set("band", view.band);
  q.set("epoch", String(view.epoch));
  if (view.mode === "diff" && view.ref >= 0) q.set("ref", String(view.ref));
  if (view.speed !== d.speed) q.set("speed", String(view.speed));
  if (view.stretch !== d.stretch) q.set("stretch", view.stretch);
  if (view.lo !== d.lo) q.set("lo", String(view.lo));
  if (view.hi !== d.hi) q.set("hi", String(view.hi));
  if (view.cmap !== d.cmap) q.set("cmap", view.cmap);
  if (view.zoom) q.set("zoom", String(view.zoom));
  if (view.smooth) q.set("smooth", "1");
  if (view.ends) q.set("ends", "1");
  if (view.partial) q.set("partial", "1");
  if (!view.ylog) q.set("ylog", "0");
  if (view.overlays.join(",") !== d.overlays.join(",")) q.set("ov", view.overlays.join(","));
  if (view.flagBits) q.set("flags", view.flagBits.join(","));
  if (view.stack) q.set("stack", view.stack.join(","));
  if (embed) q.set("embed", "1");
  return q.toString();
}

/** Flag bits shown by default: those kept in the images but unreliable for photometry (saturation, ghosts…). */
export function defaultFlagBits(bits: FlagBit[]): number[] {
  return bits.filter((f) => f.phot_bad && !f.masked).map((f) => f.bit);
}

export function writeUrl(spec: TargetSpec | null, fov: number, view: ViewState, embed: boolean): void {
  const query = buildQuery(spec, fov, view, embed);
  if (query !== window.location.search.slice(1)) window.history.replaceState(null, "", `?${query}`);
}

/** Absolute link to the current view; with embed=true the page shows only the viewer. */
export function viewLink(spec: TargetSpec | null, fov: number, view: ViewState, embed = false): string {
  return `${window.location.origin}${window.location.pathname}?${buildQuery(spec, fov, view, embed)}`;
}
