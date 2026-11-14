# ADR-0054: Identify tracklets against positions queried at their first and last detection, not instantaneous rates

- **Status:** Accepted (extends ADR-0049)
- **Date:** 2026-11-14

## Context

ADR-0049 computes known-object positions from SPHEREx's own position with JPL sb_ident, once per epoch at the mean tracklet time, and propagated them linearly with the RA/Dec rates sb_ident returns. On the 97 Klotho field (sequence `5344660567070b76cde7`) the moving-object search found Klotho's tracklet T001 (6 detections over 6.5 h, 6.9″/h, PA 108°) but identification matched 0 tracklets.

Querying sb_ident at each detection time showed that the positions themselves agree with every detection to ≤ 2.4″, but the rates do not describe the motion: they are instantaneous rates seen from SPHEREx and include the spacecraft's ~7.5 km/s orbital velocity — about ±15″/h for an object 2.4 AU away, changing sign every 98-min orbit. Klotho's Dec rate read −11.7″/h at the mid time and +13.9 / +14.2″/h at the first and last detections, while the object drifted south at ~2″/h. Propagating with them put the end detections 25–47″ off and the speed 2.4× too high, so the ±30 % speed criterion rejected the match.

## Decision

`known.identify` now queries sb_ident (SPHEREx-centric, two-pass) at each tracklet's first and last detection time — up to 10 tracklets per epoch get their own pair, the rest share the epoch's earliest and latest detection times — and attaches to each object a `track` of those two positions. `position_at` interpolates along the track (wrapping RA), and the speed and direction used for Astrometrica's ±15° / ±30 % criteria come from the displacement between the two positions (`mean_ra_rate`, `mean_dec_rate`); the instantaneous values are kept as `instant_speed_arcsec_per_hr` / `instant_pa_deg`. Objects seen at only one of the two times keep the old behaviour. Stats report `positions: "SPHEREx-centric, queried at each tracklet's first and last detection"`.

## Consequences

- Klotho's T001 is identified (all O−C ≤ 3″, ΔPA ≈ 2°, Δspeed ≈ 7 %); a regression test replays the recorded sb_ident answers.
- Two sb_ident queries per tracklet instead of one per epoch (cached on disk; JPL back-off unchanged).
- The 98-min parallax wobble between the two queried times (amplitude ≈ 9.7″/Δ for an object Δ AU away) is not modelled; it stays inside the 30″ position floor for Δ > 0.5 AU. Near-Earth objects closer than that need per-detection queries (follow-up).
- The overlay endpoint (`known-objects`) still shows instantaneous motion arrows; their direction can swing over an orbit.

## Alternatives considered

- **Query at every detection time**: exact, but 6–20 queries per tracklet against a rate-limited service.
- **Geocentric rates plus a parallax correction**: needs each object's distance and a careful frame transformation; the bracketed queries give the same accuracy with the existing API.
- **Loosen the speed tolerance**: would hide the bug and admit wrong matches.
