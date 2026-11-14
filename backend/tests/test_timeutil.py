import numpy as np

from spherex_core.timeutil import MJD_J2000, jd_to_mjd, mjd_to_iso, mjd_to_year, year_to_mjd


def test_j2000_reference():
    assert float(mjd_to_year(MJD_J2000)) == 2000.0
    assert float(year_to_mjd(2000.0)) == MJD_J2000


def test_round_trip_scalar_and_array():
    mjd = np.array([51544.5, 57170.0, 60914.213196455])
    np.testing.assert_allclose(year_to_mjd(mjd_to_year(mjd)), mjd, rtol=0, atol=1e-9)
    assert abs(float(mjd_to_year(57170.0)) - 2015.4018) < 1e-4  # CatWISE2020 reference epoch


def test_iso_and_jd():
    assert mjd_to_iso(60914.213196455) == "2025-08-27 05:07"
    assert mjd_to_iso(51544.5) == "2000-01-01 12:00"
    assert jd_to_mjd(2451545.0) == 51544.5
