// Screen ↔ layer ↔ grid ↔ sky transforms behind SkyView's gestures. Run with `npm test` (node --test, Node 23+
// strips the types itself).
import assert from "node:assert/strict";
import { test } from "node:test";
import { pixelToWorld, separation, worldToPixel } from "./astro.ts";
import type { Grid } from "./types.ts";
import { dragged, gridToLayer, layerOrigin, layerToGrid, layerToScreen, pinched, placeCard, screenToLayer, spread, wheelFactor, zoomAround, type Anchor, type Pt, type View } from "./viewport.ts";

const close = (a: number, b: number, tol: number, what: string) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} vs ${b}`);
const closePt = (a: Pt, b: Pt, tol: number, what: string) => {
  close(a[0], b[0], tol, `${what} x`);
  close(a[1], b[1], tol, `${what} y`);
};

// a 16′ field at ε Indi (dec −56.8°) and R Dra (+66.8°): high declinations, where a wrong axis shows at once
const grids: Grid[] = [
  { ra: 330.84, dec: -56.79, n: 155, pixscale_arcsec: 6.2, fov_arcmin: 16 },
  { ra: 248.25, dec: 66.75, n: 59, pixscale_arcsec: 6.2, fov_arcmin: 6 },
];
const anchor: Anchor = { ox: 720, oy: 450 };
const views: View[] = [
  { s: 4.2, c: [77.5, 77.5] },
  { s: 48, c: [12.25, 40.75] },
  { s: 0.31, c: [-300, 900] },
];

/** Sky position under a screen point. */
const skyAt = (g: Grid, v: View, sx: number, sy: number) => pixelToWorld(g, ...layerToGrid(g.n, ...screenToLayer(v, anchor, sx, sy)));
/** Screen point of a sky position. */
const screenOf = (g: Grid, v: View, ra: number, dec: number) => layerToScreen(v, anchor, ...gridToLayer(g.n, ...worldToPixel(g, ra, dec)));

test("screen, layer and grid round-trip", () => {
  for (const v of views)
    for (const q of [[0, 0], [1440, 900], [333.3, 77.7]] as Pt[]) {
      closePt(layerToScreen(v, anchor, ...screenToLayer(v, anchor, ...q)), q, 1e-9, "screen→layer→screen");
      closePt(gridToLayer(59, ...layerToGrid(59, ...q)), q, 1e-12, "layer→grid→layer");
    }
});

test("the layer origin matches the CSS transform: layer point (0, 0) is drawn at (tx, ty)", () => {
  for (const v of views) closePt(layerToScreen(v, anchor, 0, 0), layerOrigin(v, anchor), 1e-9, "origin");
});

test("sky ↔ screen round-trip at high declination", () => {
  for (const g of grids)
    for (const v of views) {
      const [ra, dec] = skyAt(g, v, 512, 300);
      closePt(screenOf(g, v, ra, dec), [512, 300], 1e-6, "sky→screen");
    }
});

test("north is up and east is left on screen", () => {
  for (const g of grids) {
    const v: View = { s: 6, c: [g.n / 2, g.n / 2] };
    const c = skyAt(g, v, 720, 450);
    const up = skyAt(g, v, 720, 400);
    const left = skyAt(g, v, 670, 450);
    assert.ok(up[1] > c[1], "a point above the centre is further north");
    close(up[0], c[0], 1e-6, "straight up keeps the RA (on the central meridian)");
    const dra = ((left[0] - c[0] + 540) % 360) - 180;
    assert.ok(dra > 0, "a point left of the centre is further east (larger RA)");
  }
});

test("a drag keeps the grabbed sky point under the pointer, on both axes", () => {
  for (const g of grids)
    for (const v0 of views) {
      const from: Pt = [600, 380];
      const grabbed = skyAt(g, v0, ...from);
      for (const to of [[700, 380], [600, 520], [431, 77], [1300, 880]] as Pt[]) {
        const v = dragged(v0, anchor, from, to);
        assert.equal(v.s, v0.s);
        const now = skyAt(g, v, ...to);
        assert.ok(separation(grabbed[0], grabbed[1], now[0], now[1]) * 3600 < 1e-6, `sky under the pointer moved: ${now} vs ${grabbed}`);
        // horizontal pointer motion moves the view horizontally only, vertical only vertically
        const dx = to[0] - from[0];
        const dy = to[1] - from[1];
        close(v.c[0], v0.c[0] - dx / v0.s, 1e-9, "centre x");
        close(v.c[1], v0.c[1] - dy / v0.s, 1e-9, "centre y");
      }
    }
});

test("zooming keeps the sky under the cursor", () => {
  for (const g of grids)
    for (const v0 of views) {
      const at: Pt = [1111, 222];
      const before = skyAt(g, v0, ...at);
      for (const s of [v0.s * 1.7, v0.s / 3, 48]) {
        const v = zoomAround(v0, anchor, s, ...at);
        assert.equal(v.s, s);
        const after = skyAt(g, v, ...at);
        assert.ok(separation(before[0], before[1], after[0], after[1]) * 3600 < 1e-6, "sky under the cursor moved");
      }
    }
});

test("a pinch zooms with the spread and pans with the midpoint", () => {
  for (const g of grids)
    for (const v of views) {
      const a0: Pt = [500, 400];
      const b0: Pt = [700, 520];
      const start = { v, ...spread(a0, b0) };
      const held = skyAt(g, v, ...start.mid);
      const a1: Pt = [420, 610];
      const b1: Pt = [900, 700];
      const now = spread(a1, b1);
      const v1 = pinched(start, anchor, now.mid, now.d);
      close(v1.s / v.s, now.d / start.d, 1e-12, "scale follows the spread");
      const under = skyAt(g, v1, ...now.mid);
      assert.ok(separation(held[0], held[1], under[0], under[1]) * 3600 < 1e-6, "sky under the fingers moved");
      // a clamped pinch still keeps the midpoint's sky under the fingers
      const v2 = pinched(start, anchor, now.mid, now.d, () => 48);
      assert.equal(v2.s, 48);
      const under2 = skyAt(g, v2, ...now.mid);
      assert.ok(separation(held[0], held[1], under2[0], under2[1]) * 3600 < 1e-6, "clamped pinch moved the sky under the fingers");
    }
});

test("wheel: down zooms out, a trackpad pinch (ctrl) is stronger, lines count as 16 px", () => {
  assert.ok(wheelFactor({ deltaY: 100, deltaMode: 0, ctrlKey: false }) < 1);
  assert.ok(wheelFactor({ deltaY: -100, deltaMode: 0, ctrlKey: false }) > 1);
  close(wheelFactor({ deltaY: 3, deltaMode: 1, ctrlKey: false }), wheelFactor({ deltaY: 48, deltaMode: 0, ctrlKey: false }), 1e-12, "lines");
  assert.ok(wheelFactor({ deltaY: -10, deltaMode: 0, ctrlKey: true }) > wheelFactor({ deltaY: -10, deltaMode: 0, ctrlKey: false }));
});

test("an anchored card sits beside its point and never leaves the screen", () => {
  const m = { top: 64, right: 12, bottom: 110, left: 12 };
  const box: Pt = [1440, 900];
  const card: Pt = [440, 520];
  // room on the right: just right of the point, its top a little above it
  assert.deepEqual(placeCard([400, 300], card, box, m), { left: 418, top: 270, side: "right" });
  // near the right edge: flips to the left of the point
  assert.deepEqual(placeCard([1300, 300], card, box, m), { left: 842, top: 270, side: "left" });
  // too low: lifted so its bottom clears the transport; too high: below the header
  assert.equal(placeCard([400, 850], card, box, m).top, 900 - 110 - 520);
  assert.equal(placeCard([400, 10], card, box, m).top, 64);
  // a narrow box with no room on either side: as far right as it fits
  assert.deepEqual(placeCard([200, 300], [440, 300], [500, 800], m), { left: 48, top: 276, side: "over" });
  // a point panned far off the screen still leaves the card wholly on it
  for (const at of [[-5000, -5000], [5000, 5000], [-5000, 5000], [5000, -5000]] as Pt[]) {
    const { left, top } = placeCard(at, card, box, m);
    assert.ok(left >= m.left && left + card[0] <= box[0] - m.right, `left ${left} for ${at}`);
    assert.ok(top >= m.top && top + card[1] <= box[1] - m.bottom, `top ${top} for ${at}`);
  }
});
