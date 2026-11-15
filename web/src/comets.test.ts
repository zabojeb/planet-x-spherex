// Comet hunt helpers (ADR-0063). Run with `npm test` (node --test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { evidenceLine, motionLine, scoreBand, spectrumPath, stampPixels, stampRange, wlToX, type CometCandidate } from "./comets.ts";

const cand = (over: Partial<CometCandidate> = {}): CometCandidate => ({
  id: "H001", kind: "slow", epoch: 0, ra: 10, dec: 20, mjd: 60900, x: 30, y: 28, rate_arcsec_per_hr: 0.42, pa_deg: 118.4,
  n_exposures: 40, status: "unidentified",
  comet_likeness: { score: 88, log_odds: 2, terms: { orbit: -1, morphology: 3, spectrum: 0 }, why: ["morphology extended"] },
  coma: { verdict: "extended", coma_score: 0.99, extent_sigma: 7.12, tail: { pa_deg: 95, sigma: 5, detected: true, aligned: true } },
  spectrum: { verdict: "gas emission", gas_sigma: 6, bands: [{ id: "co2", label: "CO2", snr: 6.04, detected: true }, { id: "co", label: "CO", snr: 1.2, detected: false }] },
  orbit: { most_likely: "tno", comet_like: 0.1, probabilities: {} },
  ...over,
});

test("score bands", () => {
  assert.equal(scoreBand(90).tone, "hot");
  assert.equal(scoreBand(50).tone, "warm");
  assert.equal(scoreBand(3).label, "asteroid-like");
});

test("evidence and motion lines", () => {
  assert.equal(evidenceLine(cand()), "extended 7.1σ · tail PA 95° ✓ · CO₂ 6.0σ · orbit tno");
  assert.equal(evidenceLine(cand({ coma: { verdict: "point-like", coma_score: 0.01 }, spectrum: { verdict: "", gas_sigma: null, bands: [] }, orbit: null })), "point-like");
  assert.equal(motionLine(cand()), "slow mover · 0.42″/h PA 118°");
  assert.equal(motionLine(cand({ kind: "tracklet", rate_arcsec_per_hr: 31.27 })), "tracklet · 31.3″/h PA 118°");
  assert.equal(motionLine(cand({ kind: "object", epoch: 1 })), "visit 2 · 0.42″/h PA 118°");
});

test("stamps: percentile range, grey levels, holes", () => {
  const img = [
    [0, 1, 2],
    [3, 100, 5],
    [6, null, 8],
  ];
  const [lo, hi] = stampRange(img);
  assert.ok(lo <= 1 && hi >= 8);
  const px = stampPixels(img);
  assert.equal(px.length, 9);
  assert.equal(px[7], -1); // the hole
  assert.equal(px[4], 255); // the peak saturates the stretch
  assert.ok(px[0] >= 0 && px[0] < px[8]);
});

test("spectrum path spans the box", () => {
  const pts: [number, number, number][] = [[0.75, 1, 0.1], [2.0, 2, 0.1], [4.26, 5, 0.1], [5.0, 2, 0.1]];
  const path = spectrumPath(pts, 220, 54).split(" ").map((p) => p.split(",").map(Number));
  assert.equal(path.length, 4);
  for (const [x, y] of path) {
    assert.ok(x >= 0 && x <= 220 && y >= 0 && y <= 54);
  }
  assert.ok(Math.abs(wlToX(4.26, 220) - path[2][0]) < 0.1);
  assert.ok(path[2][1] < path[0][1]); // the brightest point is highest
  assert.equal(spectrumPath([], 10, 10), "");
});
