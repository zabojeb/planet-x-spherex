# ADR-0010: Proper-motion-aware targets

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

Three of the five test targets move fast. Barnard's Star moves 10.4″ per year and is about 4.5′ from its J2000 position; WISE 0855 moves 8.1″ per year; ε Indi B 4.7″ per year. A small field centred on catalogue coordinates can miss the target entirely, and photometry at a fixed position measures empty sky.

## Decision

A target is a position, a reference epoch (Julian year) and a proper motion in mas/yr (`pmra` includes cos δ), with optional parallax for information. Positions are propagated linearly:

    dec(t) = dec0 + pmdec · (t − t0)
    ra(t)  = ra0 + pmra · (t − t0) / cos(dec(t))

The propagated position is used for:

- the archive query (position at 2026.0, ADR-0003);
- the centre of the output grid (median epoch of the exposures, ADR-0006);
- the photometry position in every exposure (ADR-0009);
- the predicted track drawn on the viewer, one point per epoch.

Presets carry SIMBAD values (Gaia DR3 where available) obtained through CDS Sesame. The WISE 0855 preset uses the coordinates from the challenge brief as a position at epoch 2010.4 (the WISE epoch implied by its designation) and the proper motion published by Luhman & Esplin (2016).

## Consequences

- For Barnard's Star, per-visit centroids agree with the propagated positions to better than 1″. Offsets in declination from the J2000 position were 266.0″, 271.8″ and 276.0″ measured, against 265.8″, 271.6″ and 276.0″ predicted.
- Parallax (0.55″ for Barnard's Star) and perspective acceleration are ignored.
- The WISE 0855 reference epoch is an assumption that is still being validated ([docs/methods.md](../methods.md#known-limitations)).
- Coordinates typed by users have zero proper motion unless they also give it (the API accepts `pmra`, `pmdec`, `ref_epoch`).

## Alternatives considered

- **Static coordinates:** fail for the challenge's own test targets.
- **Full astrometric propagation with astropy (`apply_space_motion`):** includes parallax and radial velocity, but needs distances we often lack and is slower. For these targets the terms we drop are under 1″ (Barnard's Star's parallax is 0.55″), well under a 6.15″ pixel.
