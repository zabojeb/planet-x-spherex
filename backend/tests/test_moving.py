"""Solar-system objects offline: MOST parsing, ephemeris, frames, the cache key and the co-moving patch."""

import hashlib
import io
import json

import numpy as np
import pytest
from astropy.table import Table

from spherex_core.config import settings
from spherex_core.moving import (AmbiguousObject, guess_type, object_target, patch_comoving_meta, rows_to_frames,
                                 search_summary)
from spherex_core.moving.ephemeris import interpolate
from spherex_core.moving.most import MostResult, MostRow, _error_from_body, build_query, parse_most
from spherex_core.pipeline.params import ALGO_VERSION, SequenceParams

URI = "ibe/data/spherex/{rel}/level2/2025W31_2A/l2b-v27-2026-225/{d}/level2_2025W31_2A_{obs}{det}_spx_l2b.fits"


def most_text() -> str:
    """A MOST 'Brief' answer: keyword lines followed by an IPAC table (written by astropy)."""
    t = Table(names=["ra_obj", "dec_obj", "sun_dist", "geo_dist", "dist_ctr", "phase", "vmag", "match",
                     "observationid", "productid", "mjd_start", "mjd_end", "uri"],
              dtype=[float, float, float, float, float, float, float, int, str, str, float, float, str])
    for obs, det, rel, t0, ra, dec in [("0156_1", "D3", "dr1", 60888.3334, 252.664196, -17.490529),
                                       ("0156_1", "D6", "dr1", 60888.3334, 252.664196, -17.490529),
                                       ("0157_1", "D3", "dr1", 60888.4100, 252.6640, -17.4904),
                                       ("0157_1", "D3", "qr3", 60888.4100, 252.6640, -17.4904)]:
        t.add_row([ra, dec, 3.48, 2.81, 0.58, 13.9, 14.04, 1, f"2025W31_2A_{obs}", det, t0, t0 + 0.00133,
                   URI.format(rel=rel, d=det[1], obs=obs, det=det)])
    buf = io.StringIO()
    t.write(buf, format="ipac")
    head = '\\catalog = "spherex"\n\\object_name = "ATLAS (C/2025 N1)"\n\\eccentricity = " 6.1396"\n'
    return head + buf.getvalue()


def test_parse_most():
    r = parse_most(most_text(), build_query("3I", begin="2025-08-01", end="2025-08-20"))
    assert r.object_name == "ATLAS (C/2025 N1)" and r.elements["eccentricity"] == "6.1396"
    assert len(r.rows) == 4 and r.rows[0].band == "D3" and abs(r.rows[0].ra - 252.664196) < 1e-9
    assert [row.collection for row in r.rows].count("spherex_qr3") == 1
    assert len(r.ephemeris()) == 2 and r.query["obj_type"] == "Comet"


def test_rows_to_frames_prefer_dr1():
    r = parse_most(most_text(), build_query("3I"))
    frames = rows_to_frames(r.rows)
    assert len(frames) == 3  # the QR3 copy of exposure 0157 D3 is dropped
    assert all(f.collection == "spherex_dr1" for f in frames)
    assert frames[0].s3_url == settings.s3_base + frames[0].uri.removeprefix("ibe/data/spherex/")
    assert frames[0].wl_min_um == 1.64 and frames[1].band == "D6"


def test_interpolate_wraps_ra_and_extrapolates():
    eph = [[1.0, 0.1, 1.0], [0.0, 359.9, 0.0]]  # unsorted on purpose
    ra, dec = interpolate(eph, 0.5)
    assert abs(((ra + 180) % 360) - 180) < 1e-9 and abs(dec - 0.5) < 1e-12
    ra2, dec2 = interpolate(eph, 2.0)
    assert abs(ra2 - 0.3) < 1e-9 and abs(dec2 - 2.0) < 1e-12
    ras, _ = interpolate(eph, np.array([0.0, 1.0]))
    np.testing.assert_allclose(ras, [359.9, 0.1])


def test_guess_type():
    for name in ("3I", "3I/ATLAS", "C/2025 N1", "1P/Halley", "12P", "P/2010 A2"):
        assert guess_type(name) == "Comet", name
    for name in ("Eros", "433", "2024 YR4", "Apophis"):
        assert guess_type(name) == "Asteroid", name


def test_ambiguous_name_choices():
    body = ("<html>Object Name Error: User Input String=Psyche Multiple matches to the input string in the list "
            "from Horizons. Psyche (spacecraft), Object Type=, NAIF ID=-255, Designation=2023-157A Psyche (mission "
            "target), Object Type=Asteroid, NAIF ID=2000016, Designation=NO_VALUE 16 Psyche, Object Type=Asteroid, "
            "NAIF ID=20000016, Designation=A852 FA Workspace</html>")
    err = _error_from_body(body, 400)
    assert isinstance(err, AmbiguousObject)
    assert [c["naif_id"] for c in err.choices] == [-255, 2000016, 20000016]
    assert [c["name"] for c in err.choices] == ["Psyche (spacecraft)", "Psyche (mission target)", "16 Psyche"]
    assert [c["designation"] for c in err.choices] == ["2023-157A", "NO_VALUE", "A852 FA"]
    assert err.choices[2]["type"] == "Asteroid" and err.choices[0]["type"] is None
    unknown = _error_from_body("No matching NAIF ID was found! 4 Vesta, Object Type=Asteroid, NAIF ID=20000004", 400)
    assert isinstance(unknown, LookupError) and "20000004" in str(unknown)


def _legacy_key(p: SequenceParams) -> str:
    """The key formula before moving objects existed: ids of ordinary sequences must not change."""
    d = dict(ra=round(p.ra, 6), dec=round(p.dec, 6), pmra=round(p.pmra, 1), pmdec=round(p.pmdec, 1),
             ref=round(p.ref_epoch, 3), fov=round(p.fov_arcmin, 3), gap=round(p.gap_days, 3),
             maxd=round(p.max_epoch_days, 3), coll=list(p.collections or settings.collections), v=ALGO_VERSION)
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:20]


def test_params_key_unchanged_without_object():
    p = SequenceParams(ra=269.4520769586, dec=4.6933649666, pmra=-801.551, pmdec=10362.394, fov_arcmin=6.0)
    assert not p.is_moving_object and p.key() == _legacy_key(p)
    q = SequenceParams(ra=252.66, dec=-17.49, fov_arcmin=6.0, object_name="3I", object_type="Comet",
                       obs_begin="2025-08-01", obs_end="2025-08-20")
    assert q.is_moving_object and q.key() != _legacy_key(q)
    assert SequenceParams(ra=1.0, dec=2.0, naif_id=20000433).is_moving_object


def _result() -> MostResult:
    rows = [MostRow(60888.0 + i * 0.1, 60888.0 + i * 0.1 + 0.001, 250.0 + i * 0.01, -17.0 - i * 0.002, "D3",
                    URI.format(rel="dr1", d=3, obs=f"0{i}_1", det="D3"), f"2025W31_2A_0{i}_1") for i in range(5)]
    return MostResult("TEST (2025 AA)", build_query("2025 AA"), {}, rows)


def test_object_target_follows_ephemeris():
    t = object_target(_result())
    assert t.ephemeris and len(t.ephemeris) == 5 and t.pmra == 0.0
    ra, dec = t.position_at(60888.25)
    assert abs(ra - 250.025) < 1e-9 and abs(dec + 17.005) < 1e-9
    assert "ephemeris" in t.to_dict()


def test_search_summary_and_patch():
    res = _result()
    s = search_summary(res)
    assert s["n_exposures"] == 5 and s["suggested_request"]["object_name"] == "2025 AA"
    assert s["suggested_request"]["object_type"] == "Asteroid" and len(s["exposures"]) == 5
    target = object_target(res)
    n = 11
    meta = {"grid": {"n": n, "ra": 250.02, "dec": -17.004},
            "exposures": [{"mjd": 60888.0 + i * 0.1, "target_px": [0.0, 0.0]} for i in range(5)],
            "epochs": [{"mjd_mean": 60888.2}], "target": {"track": [{"epoch": 0, "x": 99.0, "y": -3.0}]}}
    patch_comoving_meta(meta, target, res)
    assert meta["grid"]["frame"] == "comoving" and meta["moving"]["object"] == "TEST (2025 AA)"
    assert all(e["target_px"] == [5.0, 5.0] for e in meta["exposures"])
    assert meta["target"]["track"][0]["x"] == 5.0 and abs(meta["exposures"][2]["center"][0] - 250.02) < 1e-9
    assert len(meta["epochs"][0]["center"]) == 2


def test_build_query_requires_object():
    with pytest.raises(ValueError):
        build_query(None)
    assert build_query(naif_id=20000433)["input_type"] == "naifid_input"


@pytest.mark.network
def test_most_live_3i():
    from spherex_core.moving import query_most

    r = query_most("3I", "Comet", "2025-08-01", "2025-08-03", use_cache=False)
    assert r.rows and "ATLAS" in r.object_name and all(row.band.startswith("D") for row in r.rows)
