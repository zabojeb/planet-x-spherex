"""Regression: 97 Klotho's tracklet on its SPHEREx field (sequence 5344660567070b76cde7) was not identified.

sb_ident's rates are instantaneous rates seen from SPHEREx and swing by ~+-15"/h over the 98-min orbit, so
propagating the mid-epoch position with them missed the first and last detections by 25-47". Identification
now interpolates between positions queried at the tracklet's first and last detection times.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from spherex_core.field import known

# The real tracklet T001 (epoch 0): one D1 detection, four D4 detections 2 min apart, one late D4 detection.
DETECTIONS = [
    {"mjd": 60807.083914835, "ra": 171.2402937282681, "dec": 11.123106421306687, "band": "D1"},
    {"mjd": 60807.283752451, "ra": 171.24899896312624, "dec": 11.119689979977261, "band": "D4"},
    {"mjd": 60807.285244696, "ra": 171.24899896312624, "dec": 11.119689979977261, "band": "D4"},
    {"mjd": 60807.286736942, "ra": 171.24899896312624, "dec": 11.119689979977261, "band": "D4"},
    {"mjd": 60807.288229187, "ra": 171.24899896312624, "dec": 11.119689979977261, "band": "D4"},
    {"mjd": 60807.35358634, "ra": 171.252481, "dec": 11.11969, "band": "D4"},
]
TRACKLET = {"id": "T001", "epoch": 0, "n": 6, "mjd_mid": 60807.26357740851, "rate_arcsec_per_hr": 6.869,
            "pa_deg": 108.43, "detections": DETECTIONS, "known": None, "identification": []}

# sb_ident answers from SPHEREx (Horizons observer -163182), recorded on 2026-11-14: position plus the
# instantaneous rates at each queried time. Note the Dec rate's sign flips between queries.
ANSWERS = {
    60807.2636: (171.2471, 11.12, 11.76, -11.68),
    60807.0839: (171.23962, 11.12331, 10.22, 14.21),
    60807.3536: (171.25258, 11.11956, 11.94, 13.86),
}


def fake_field_objects(fd, mjd, vmag_lim=21.0):
    key = min(ANSWERS, key=lambda t: abs(t - mjd))
    assert abs(key - mjd) < 1e-3, f"unexpected query time {mjd}"
    ra, dec, rra, rdec = ANSWERS[key]
    return [{"name": "97 Klotho (A868 DB)", "kind": "asteroid", "mjd": mjd, "ra": ra, "dec": dec, "vmag": 12.8,
             "ra_rate": rra, "dec_rate": rdec, "err_ra": 0.1, "err_dec": 0.1, "x": 50.0, "y": 50.0, "inside": True,
             "speed_arcsec_per_hr": float(np.hypot(rra, rdec)),
             "pa_deg": float(np.degrees(np.arctan2(rra, rdec)) % 360.0)}]


def test_old_mid_epoch_propagation_misses_klotho():
    mid = fake_field_objects(None, 60807.2636)
    assert known._rank(dict(TRACKLET), mid) == []


def test_bracketed_positions_identify_klotho(monkeypatch):
    monkeypatch.setattr(known, "field_objects", fake_field_objects)
    t = {**TRACKLET, "detections": [dict(d) for d in DETECTIONS]}
    stats = known.identify(SimpleNamespace(), [t], 21.0)
    assert stats["matched"] == 1 and stats["queries"] == 2
    best = t["known"]
    assert best["name"].startswith("97 Klotho")
    assert best["sep_arcsec"] < 5.0
    assert best["dpa_deg"] < 15.0 and best["dspeed_frac"] < 0.30
    assert all(abs(oc["dra"]) < 5 and abs(oc["ddec"]) < 5 for oc in best["o_minus_c_arcsec"])


@pytest.mark.network
def test_klotho_identified_live(tmp_path):
    """Live JPL Horizons (SPHEREx state) + sb_ident: the recorded tracklet is identified as 97 Klotho."""
    from spherex_core import services
    from spherex_core.processing import make_grid

    services.configure(cache_root=tmp_path)
    try:
        fd = SimpleNamespace(grid=make_grid(171.2465, 11.12, 6.0), n=59)
        t = {**TRACKLET, "detections": [dict(d) for d in DETECTIONS]}
        stats = known.identify(fd, [t], 21.0)
        if stats["error"] and ("429" in stats["error"] or "503" in stats["error"] or "imed out" in stats["error"]):
            pytest.skip(f"JPL busy: {stats['error']}")
        assert stats["error"] is None and stats["matched"] == 1
        assert t["known"]["name"].startswith("97 Klotho") and t["known"]["sep_arcsec"] < 6.0
    finally:
        services.configure(None)


def test_position_at_interpolates_track():
    o = {"mjd": 0.0, "ra": 10.0, "dec": 0.0, "ra_rate": 999.0, "dec_rate": 999.0,
         "track": [[1.0, 359.99, 0.0], [2.0, 0.01, 1.0]]}
    ra, dec = known.position_at(o, 1.5)
    assert abs(ra) < 1e-9 or abs(ra - 360.0) < 1e-9
    assert abs(dec - 0.5) < 1e-12
