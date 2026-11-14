# ADR-0053: Analyze one clicked position as one job with independent steps and a plain-language verdict

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The viewer's most useful action is "click a point → Analyze": a person (or an AI agent through MCP) wants to know what the object at a position is and whether it is interesting, without calling eight endpoints and combining their answers. The individual analyses already exist (re-measure photometry, spectral analysis, motion fit, periodogram, field candidates, known objects), but they have different costs, depend on different upstream services (ESA Gaia, IRSA, CDS mirrors, JPL), and any of them can fail.

## Decision

`POST /api/sequences/{id}/analyze {ra, dec, pmra?, pmdec?, ref_epoch?, name?}` starts or joins a job on the field job runner (`field/jobs.py`, kind `p`): deduplicated per sequence and position rounded to 1e-4° (≈0.4″) and motion rounded to 1 mas/yr, every `GET /api/analyses/{job}` is a heartbeat, `DELETE` withdraws, a job nobody polls for 60 s is cancelled (410), results are written next to the sequence and served immutable.

`analysis/point.py` runs eight steps in order — identification, photometry, spectrum (+ classification + spectral variability), motion, periodogram, colours (red excess), known objects, tracklets — each wrapped so that an exception becomes `{"error": ...}` in its slot and the job still finishes. Cancellation is checked between steps. When the request carries no motion and a Gaia DR3 source lies within 4″ at the sequence's epochs, photometry follows that source's motion (recorded in `point.motion_used`), so fast movers are measured where they are in each exposure; the motion fit itself is blind so it can be compared with Gaia.

A rule-based `verdict` turns the slots into findings `{kind, text, confidence, novelty, evidence}` and an overall `novelty` in [0, 1] (0.1 base plus each finding's contribution, clipped): unexplained motion without a Gaia counterpart (+0.55), unidentified intra-visit mover (+0.5 high quality), red excess without Gaia (+0.35), brown-dwarf / composite spectra (+0.2–0.25), variability (+0.1–0.15); a SIMBAD identification (−0.3), Gaia match (−0.15) or known asteroid (−0.1) lower it. The rules are explicit so a person can see why a point was ranked. Variability is only claimed when a detector's ratio spectrum changes by ≥ 5 % at |z| ≥ 5 — for saturated (ring, relative) photometry by ≥ 25 % in at least two detectors, because rings drift by up to ~14 % (and one detector by ~30 %) on the constant Barnard's Star — and a period only with ≥ 5 epochs and P > 20 d, so within-visit cadence and ring systematics don't produce false periods.

## Consequences

- One call answers "what is this and is it new?" for the UI and for agents; partial upstream outages degrade the answer instead of failing it.
- The steps run sequentially in one thread (≈10–60 s, dominated by re-measuring every exposure and by first-time catalogue / JPL queries); the job runner limits concurrency to two.
- Intra-visit tracklets come from a finished field analysis of the same sequence; without one the slot says so instead of running the 4–5 min search.
- Novelty weights are heuristics; they are for ranking and explanation, not probabilities.

## Alternatives considered

- **Fan-out from the client** (eight requests): more round trips, each with its own error handling, and no single verdict for agents.
- **Run steps in parallel**: the expensive step (photometry) already uses the process pool; parallel catalogue and JPL queries would hit rate limits.
- **A learned novelty score**: no labelled set of "interesting" SPHEREx points exists yet; explicit rules are explainable and testable.
