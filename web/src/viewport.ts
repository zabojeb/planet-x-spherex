/**
 * Field view geometry: the pure maths behind SkyView's pan, zoom and pinch (tested in viewport.test.ts).
 *
 * The image layer is n × n layer pixels (x right, y down) drawn with `translate(tx, ty) scale(s)`. A view is the
 * scale s plus the layer point c drawn at the frame's anchor (ox, oy), the middle of the visible frame. Grid
 * pixels (x right, y up, integers at pixel centres) are what the WCS in astro.ts works in.
 *
 * Every gesture keeps the layer point it grabbed under the pointer: a drag at a fixed scale, a wheel step at
 * the cursor, a pinch at the fingers' midpoint (which may move as well as spread).
 */

export type Pt = [number, number];

export interface View {
  s: number; // screen pixels per layer pixel
  c: Pt; // layer point at the anchor
}

/** Screen point where the view's centre point c is drawn. */
export interface Anchor {
  ox: number;
  oy: number;
}

/** Screen position of the layer's origin (the translate of its CSS transform). */
export const layerOrigin = (v: View, a: Anchor): Pt => [a.ox - v.c[0] * v.s, a.oy - v.c[1] * v.s];

export const screenToLayer = (v: View, a: Anchor, sx: number, sy: number): Pt => [v.c[0] + (sx - a.ox) / v.s, v.c[1] + (sy - a.oy) / v.s];

export const layerToScreen = (v: View, a: Anchor, lx: number, ly: number): Pt => [a.ox + (lx - v.c[0]) * v.s, a.oy + (ly - v.c[1]) * v.s];

export const gridToLayer = (n: number, x: number, y: number): Pt => [x + 0.5, n - y - 0.5];

export const layerToGrid = (n: number, lx: number, ly: number): Pt => [lx - 0.5, n - ly - 0.5];

/** The view at scale s that draws layer point l at screen point (sx, sy). */
export const viewWith = (a: Anchor, l: Pt, s: number, sx: number, sy: number): View => ({ s, c: [l[0] - (sx - a.ox) / s, l[1] - (sy - a.oy) / s] });

/** Zoom to scale s keeping the layer point under (sx, sy) where it is. */
export const zoomAround = (v: View, a: Anchor, s: number, sx: number, sy: number): View => viewWith(a, screenToLayer(v, a, sx, sy), s, sx, sy);

/** A drag that started at `from` on view v0 and is now at `to`: the point grabbed at `from` stays under the pointer. */
export const dragged = (v0: View, a: Anchor, from: Pt, to: Pt): View => viewWith(a, screenToLayer(v0, a, from[0], from[1]), v0.s, to[0], to[1]);

export interface PinchStart {
  v: View; // view when the second finger came down
  mid: Pt; // the fingers' midpoint then
  d: number; // and their distance
}

/** A pinch now at midpoint `mid` with the fingers `d` apart: scale follows the spread (through `clamp`), and the
 *  layer point that was under the starting midpoint stays under the moving one. */
export const pinched = (p: PinchStart, a: Anchor, mid: Pt, d: number, clamp: (s: number) => number = (s) => s): View =>
  viewWith(a, screenToLayer(p.v, a, p.mid[0], p.mid[1]), clamp((p.v.s * d) / p.d), mid[0], mid[1]);

/** The fingers' midpoint and distance. */
export function spread(a: Pt, b: Pt): { mid: Pt; d: number } {
  return { mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], d: Math.hypot(a[0] - b[0], a[1] - b[1]) };
}

/** One wheel event as a zoom factor (> 1 zooms in). Lines and pages count as pixels like Aladin does; a
 *  trackpad pinch arrives as ctrl + small deltas and gets a stronger gain so it tracks the fingers. */
export function wheelFactor(e: { deltaY: number; deltaMode: number; ctrlKey: boolean }, pageHeight = 800): number {
  const px = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? pageHeight : 1);
  return Math.exp(-px * (e.ctrlKey ? 0.01 : 0.0016));
}
