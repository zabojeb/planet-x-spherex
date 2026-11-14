/** Chart and overlay colours: the dataviz palette's dark-mode steps (validated with validate_palette.js). */
export const SERIES = {
  blue: "#3987e5", // slot 1
  orange: "#d95926", // slot 2
  aqua: "#199e70", // slot 3
};

/** Overlay catalogs use the first three categorical slots (they validate all-pairs on the dark surface). SIMBAD's
 *  orchid is not a palette slot: slots 4–8 fail all-pairs against the first three on black, this one passes every
 *  check (worst CVD ΔE 9.1, normal-vision 19.2, ≥ 3:1 on #000), and it also carries its own shape and name labels. */
export const CATALOG_STYLE: Record<string, { color: string; label: string; shape: "circle" | "square" | "diamond" | "plus" }> = {
  gaia: { color: SERIES.blue, label: "Gaia DR3", shape: "circle" },
  catwise: { color: SERIES.orange, label: "CatWISE2020", shape: "square" },
  "2mass": { color: SERIES.aqua, label: "2MASS", shape: "diamond" },
  simbad: { color: "#c94fb0", label: "SIMBAD", shape: "plus" },
};

/** Catalogues that can be drawn on the sky, in drawing order (named objects last, on top). */
export const CATALOG_IDS = ["gaia", "catwise", "2mass", "simbad"] as const;
export type CatalogId = (typeof CATALOG_IDS)[number];

export const STATUS = { warning: "#fab219", critical: "#d03b3b", good: "#0ca30c" };

/** Contamination-flag overlay: a status colour that cannot be mistaken for the amber UI accent. */
export const FLAG_OVERLAY = STATUS.critical;

// Ordinal blue ramp for epochs (oldest = darkest step 600, newest = lightest step 200), validated --ordinal on #1a1a19.
const RAMP = ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"];

function mix(a: string, b: string, t: number): string {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const c = [16, 8, 0].map((s) => Math.round(((pa >> s) & 255) * (1 - t) + ((pb >> s) & 255) * t));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

export function epochColor(e: number, n: number): string {
  if (n <= 1) return RAMP[2];
  const pos = (e / (n - 1)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(pos));
  return mix(RAMP[i], RAMP[i + 1], pos - i);
}
