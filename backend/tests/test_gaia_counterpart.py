"""Catalogue counterparts of fast movers in point analysis (offline; Gaia rows recorded from each archive).

Regression for ε Indi B (4.7″/yr): rows propagated to the sequence's mean epoch were compared with the request
position at its own epoch (J2000), ~122″ away, so the verdict claimed "no Gaia counterpart".
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from spherex_core import catalogs
from spherex_core.analysis import point
from spherex_core.catalogs import gaia as gaia_cat
from spherex_core.processing import make_grid

RAW = json.loads((Path(__file__).parent / "data" / "gaia_fast_movers.json").read_text())["rows"]
VIZIER_COLS = {"source_id": "Source", "ra": "RA_ICRS", "dec": "DE_ICRS", "pmra": "pmRA", "pmdec": "pmDE",
               "parallax": "Plx", "phot_g_mean_mag": "Gmag", "bp_rp": "BP-RP"}


def _parsed(field: str, source: str, monkeypatch) -> list[dict]:
    """The recorded TAP rows as each archive returns them, through that archive's own parser."""
    raw = RAW[field]
    if source == "vizier":
        raw = [{VIZIER_COLS[k]: v for k, v in r.items()} for r in raw]
    monkeypatch.setattr(gaia_cat, "tap_csv", lambda *a, **k: raw)
    if source == "vizier":
        return gaia_cat._vizier(0.0, 0.0, 0.01)
    url = {"esa": "esa", "mast": "mast", "irsa": "irsa"}[source]
    return gaia_cat._standard(url, "table", 0.0, 0.0, 0.01)
EPS_INDI_B = dict(ra=331.0441465885, dec=-56.7828441951, pmra=3981.977, pmdec=-2466.832, ref_epoch=2000.0)
BARNARD = dict(ra=269.4520769586, dec=4.6933649666, pmra=-801.551, pmdec=10362.394, ref_epoch=2000.0)
# mean epochs (MJD) of the visits of the real sequences
EPOCHS = {"eps_indi_b": [60840.0, 60990.0, 61160.0, 61310.0], "barnard": [60920.0, 61130.0, 61290.0]}
TWO_MASS = {  # 2MASS rows (no proper motions; positions at their own observation epoch)
    "eps_indi_b": [{"id": "2MASS J22041052-5646577", "ra": 331.043838, "dec": -56.782705, "epoch": 1999.6,
                    "pmra": None, "pmdec": None, "mag": 11.2, "mag_band": "Ks"}],
    "barnard": [{"id": "2MASS J17574849+0441405", "ra": 269.452045, "dec": 4.694604, "epoch": 2000.4,
                 "pmra": None, "pmdec": None, "mag": 4.5, "mag_band": "Ks"}],
}


def _ctx(field: str, target: dict):
    years = [2000.0 + (m - 51544.5) / 365.25 for m in EPOCHS[field]]
    y_mid = sum(years) / len(years)
    req = point.PointRequest(**target)
    ra, dec = point._point_at(req, y_mid)
    meta = {"epochs": [{"index": i, "mjd_mean": m} for i, m in enumerate(EPOCHS[field])]}
    return SimpleNamespace(meta=meta, grid=make_grid(ra, dec, 6.0)), req, years


def _fake_cone(gaia_rows: list[dict], field: str, calls: list):
    def cone(name, ra, dec, radius_deg, max_age_hours=0):
        calls.append((name, ra, dec, radius_deg))
        if name == "gaia":
            return gaia_rows
        if name == "2mass":
            return TWO_MASS[field]
        return []
    return cone


SOURCES = ("esa", "mast", "irsa", "vizier")


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("field,target", [("eps_indi_b", EPS_INDI_B), ("barnard", BARNARD)])
def test_fast_mover_matches_its_gaia_entry(monkeypatch, field, target, source):
    rows = _parsed(field, source, monkeypatch)
    calls: list = []
    monkeypatch.setattr(catalogs, "cone", _fake_cone(rows, field, calls))
    ctx, req, years = _ctx(field, target)
    ident = point.identification(ctx, req, years)
    gaia = ident["gaia"]
    assert gaia["counterpart"], (field, source, gaia)
    best = gaia["matches"][0]
    assert best["sep_arcsec"] < 1.0
    assert best["pm_total"] > 4000
    # every epoch's predicted position lands on the target's position at that epoch
    for t, yr in zip(best["track"], years):
        ra_t, dec_t = point._point_at(req, yr)
        assert point._sep(t["ra"], t["dec"], ra_t, dec_t) < 1.0
    # 2MASS (no motions) still matches at its own, old epoch
    assert ident["2mass"]["counterpart"]
    # the Gaia cone is centred near the 2016 position, not the J2000 one
    g_ra, g_dec = next((c[1], c[2]) for c in calls if c[0] == "gaia")
    ra16, dec16 = point._point_at(req, 2016.0)
    assert point._sep(g_ra, g_dec, ra16, dec16) < 40.0


def test_verdict_names_the_gaia_match_not_a_candidate(monkeypatch):
    monkeypatch.setattr(catalogs, "cone", _fake_cone(_parsed("eps_indi_b", "mast", monkeypatch), "eps_indi_b", []))
    ctx, req, years = _ctx("eps_indi_b", EPS_INDI_B)
    ident = point.identification(ctx, req, years)
    findings = point.verdict({"identification": ident,
                              "motion": {"combined": {"pmra": 3600.0, "pmdec": -2500.0, "pm_total": 4380.0,
                                                      "pm_total_err": 250.0, "pa_deg": 125.0}}})
    texts = " | ".join(f["text"] for f in findings["findings"])
    assert "no Gaia" not in texts and "No Gaia" not in texts, texts
    assert "Gaia DR3" in texts


def test_no_counterpart_claimed_on_error_or_truncated_cone():
    motion = {"combined": {"pm_total": 1200.0, "pm_total_err": 100.0, "pa_deg": 90.0}}
    for gaia in ({"error": "TimeoutError: no answer"}, {"matches": [], "n_in_radius": 0, "counterpart": False,
                                                      "truncated": True}):
        texts = " | ".join(f["text"] for f in point.verdict({"identification": {"gaia": gaia},
                                                             "motion": motion})["findings"])
        assert "no Gaia counterpart" not in texts and "No Gaia DR3 counterpart" not in texts, texts
    ok = {"matches": [], "n_in_radius": 0, "counterpart": False, "truncated": False}
    texts = " | ".join(f["text"] for f in point.verdict({"identification": {"gaia": ok},
                                                         "motion": motion})["findings"])
    assert "no Gaia counterpart" in texts and "No Gaia DR3 counterpart" in texts


def test_static_point_without_motion_is_compared_at_the_sequence_epoch(monkeypatch):
    """A plain click (no motion) must still match a static Gaia star at the click."""
    star = {"id": "Gaia DR3 42", "ra": 10.0, "dec": 20.0, "epoch": 2016.0, "pmra": 5.0, "pmdec": -3.0,
            "mag": 14.0, "mag_band": "G"}
    monkeypatch.setattr(catalogs, "cone", lambda name, *a, **k: [star] if name == "gaia" else [])
    req = point.PointRequest(ra=10.0, dec=20.0)
    meta = {"epochs": [{"index": 0, "mjd_mean": 61000.0}]}
    ctx = SimpleNamespace(meta=meta, grid=make_grid(10.0, 20.0, 4.0))
    ident = point.identification(ctx, req, [2025.9])
    assert ident["gaia"]["counterpart"]
