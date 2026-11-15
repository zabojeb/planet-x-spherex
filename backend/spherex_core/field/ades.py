"""ADES PSV export of tracklets for a space-based station (follow-up data, not a submission).

SPHEREx is MPC station 288. Every observation carries the spacecraft's geocentric position at obsTime
(`sys` ICRF_KM, `ctr` 399, `pos1`-`pos3` from Horizons), the reference catalogue (`astCat` Gaia3: the Level 2
WCS is solved against Gaia DR3) and note `Z` (survey data measured by a non-survey pipeline). Magnitudes are
left out: SPHEREx's narrow bands are not valid MPC bands. `mode` CCD is a placeholder (the detectors are
HgCdTe arrays; confirm with the MPC before any submission). Positions are measured on the resampled grid
(peak pixel unless verified), so the uncertainties are generous.
"""

from __future__ import annotations

import numpy as np

from . import observer
from .known import mjd_to_datetime

FWHM_ARCSEC = 8.0
WCS_FLOOR_ARCSEC = 0.35
COLUMNS = ("trkSub", "mode", "stn", "sys", "ctr", "pos1", "pos2", "pos3", "obsTime", "ra", "dec", "rmsRA", "rmsDec",
           "astCat", "notes", "remarks")


def _rms(snr: float | None) -> float:
    s = max(float(snr or 5.0), 1.0)
    return float(np.hypot(FWHM_ARCSEC / 2.355 / s, WCS_FLOOR_ARCSEC))


def ades_psv(tracklets: list[dict], sequence: str, include_low: bool = False) -> str:
    """PSV text of the high-quality tracklets (all with include_low); raises RuntimeError when the spacecraft
    positions cannot be fetched from Horizons."""
    tracklets = [t for t in tracklets if include_low or t.get("quality", "high") == "high"]
    mjds = [d["mjd"] for t in tracklets for d in t["detections"]]
    states = observer.states(mjds) if mjds else {}
    head = ["# version=2022", "# observatory", "! mpcCode 288", "# telescope", "! design Reflector",
            "! detector HgCdTe", "# comment",
            f"! line Seek In Space tracklets, sequence {sequence}. FOLLOW-UP DATA, NOT A SUBMISSION.",
            "! line Positions from resampled SPHEREx Level 2 images; mode CCD is a placeholder for HgCdTe.",
            "|".join(COLUMNS)]
    lines = []
    for t in tracklets:
        known = (t.get("known") or {}).get("name")
        for d in t["detections"]:
            st = states.get(f"{float(d['mjd']):.5f}")
            pos = st[:3] if st else [float("nan")] * 3
            ts = mjd_to_datetime(d["mjd"]).strftime("%Y-%m-%dT%H:%M:%S.%f")[:22] + "Z"
            r = _rms(d.get("snr"))
            lines.append("|".join([
                t["id"], "CCD", "288", "ICRF_KM", "399", f"{pos[0]:.3f}", f"{pos[1]:.3f}", f"{pos[2]:.3f}", ts,
                f"{d['ra']:.6f}", f"{d['dec']:+.6f}", f"{r:.2f}", f"{r:.2f}", "Gaia3", "Z",
                f"{d['band']} snr={d.get('snr', 0):.1f}" + (f" known={known}" if known else "")]))
    return "\n".join(head + lines) + "\n"
