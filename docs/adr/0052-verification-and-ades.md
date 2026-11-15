# ADR-0052: Verify candidates with free-position ePSF fits and export ADES for station 288 as follow-up data

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

A candidate needs evidence a person can judge (Astrometrica's verification window) and positions with honest errors; a report must be correct for a space-based observer (MPC code 288).

## Decision

`POST /api/sequences/{id}/verify` fits the detector's ePSF with free x, y, flux and background (weights 1/VARIANCE, masked and saturated pixels excluded) on the native cutouts of up to 40 exposures nearest the candidate's time, at the position predicted from an optional motion, and returns position, σ (fit covariance, scaled by √χ²), flux, SNR and χ² per exposure, plus 11×11 stare and track strips from the aligned exposures. `GET /api/field/{job}/ades.psv` writes tracklets as ADES PSV: `stn` 288, `sys` ICRF_KM, `ctr` 399, `pos1–3` from Horizons at `obsTime`, `astCat` Gaia3, note Z, no magnitudes, `mode` CCD as a placeholder for HgCdTe, rms = √((8″/2.355/SNR)² + 0.35²); the header says FOLLOW-UP DATA, NOT A SUBMISSION.

## Consequences

Exports are usable for follow-up planning; a submission needs MPC agreement on mode and on non-survey measurements of survey data, and verified (fitted) positions rather than grid peaks.

## Alternatives considered

80-column format only (no observer position for space stations without a second line); submitting automatically (not ours to decide).
