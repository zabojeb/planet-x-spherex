# ADR-0022: Follow solar-system objects with IRSA MOST and a co-moving sequence mode

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The challenge lists asteroids and comets among the objects SPHEREx can reveal, and the analog review ranks
solar-system objects third ([docs/research/analogs.md](../research/analogs.md), gap 3). Our pipeline searches a
fixed (or proper-motion) position, which loses a comet within hours. IRSA's Moving Object Search Tool (MOST)
resolves names through JPL Horizons, integrates the orbit and matches it against SPHEREx footprints; its GET
API returns, per matched Level 2 file, the object's predicted position at the exposure start and the file
uri (3I/ATLAS, Aug 1–20 2025: 160 files in ~2 s; 433 Eros over a year: 954 files in 12–165 s).

## Decision

- `spherex_core/moving/most.py` calls `nph-most` (`catalog=spherex`, `output_mode=Brief`), parses the IPAC
  table with astropy, caches answers for a week under `<cache>/moving/`, and turns Horizons' "multiple
  matches" answer into `AmbiguousObject` with the candidates' names and NAIF IDs.
- `Target` gains an optional `ephemeris` ([[mjd, ra, dec], ...]); `position_at` interpolates it (RA
  unwrapped, linear extrapolation at the ends) instead of applying proper motion.
- `SequenceParams` gains `object_name`, `object_type`, `naif_id`, `obs_begin`, `obs_end`; they enter the key
  only when set, so existing sequence ids do not change.
- **Co-moving mode** in `pipeline/sequence.py`: frames come from MOST (one per exposure and detector, DR1
  preferred), and every exposure is cut out and resampled on its own grid centred on the object at that
  exposure's time. Co-adds therefore stack the object while stars trail. `patch_comoving_meta` puts the
  target on the central pixel in `meta.json`, adds per-exposure and per-epoch `center`, `grid.frame =
  "comoving"` and a `moving` block.
- API: `GET /api/moving/search` (202 while MOST works, 409 with `choices`, 404, 502) returns the exposures,
  the ephemeris and a `suggested_request` to post to `POST /api/sequences`.

## Consequences

- 3I/ATLAS: 160 frames, 1 epoch, built in 11 s; the brightest pixel of every detector's co-add is on the
  central pixel (S/N 69 in D1 down to 3 in D6). 433 Eros: 954 frames, 6 epochs from 2025-06 to 2026-06, 47 s,
  centred in every epoch and detector.
- Photometry runs at the object; the ePSF fit assumes a point source, so for comets the aperture flux
  (`flux_ap_mjy`) is the better number.
- Catalog overlays and archival epochs do not apply in the co-moving frame (archival returns 404).
- MOST is a dependency: long windows can take minutes, hence background searches (ADR-0023).

## Alternatives considered

- **Our own ephemerides (Horizons API or astroquery) plus TAP footprint searches:** more code and many TAP
  queries along the track; MOST already does the matching against SPHEREx footprints.
- **Star-fixed grids for moving objects:** the object would cross the field (or leave it) within one epoch;
  the co-moving grid is what makes stacking useful.
