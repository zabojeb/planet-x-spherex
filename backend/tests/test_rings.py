"""Ring photometry around saturated cores (offline, synthetic stars)."""

import numpy as np

from spherex_core.flags import FLAG_BITS, RING_BAD, RING_CORE
from spherex_core.photometry import radial_profile, ring_flux
from spherex_core.pipeline.assemble import apply_rings

OMEGA = 37.86
OVERFLOW = 1 << FLAG_BITS["OVERFLOW"]
SAT_LEVEL = 50.0


def saturated_star(flux, x=30.3, y=29.6, n=61, bkg=0.2, noise=0.01, seed=0):
    """A star with broad wings (Moffat, beta 2.5); pixels above SAT_LEVEL are flagged OVERFLOW and clipped."""
    gy, gx = np.mgrid[0:n, 0:n]
    r2 = (gx - x) ** 2 + (gy - y) ** 2
    alpha, beta = 1.2, 2.5
    prof = (beta - 1) / (np.pi * alpha ** 2) * (1 + r2 / alpha ** 2) ** -beta
    rng = np.random.default_rng(seed)
    img = bkg + flux * prof + rng.normal(0, noise, (n, n))
    flags = np.zeros((n, n), np.int32)
    sat = img > SAT_LEVEL
    flags[sat] |= OVERFLOW
    img[sat] = SAT_LEVEL
    return img.astype(np.float32), np.full((n, n), noise ** 2, np.float32), flags, (x, y)


def test_ring_flux_scales_with_total_flux_despite_saturation():
    profiles = []
    for flux in (2000.0, 4000.0):
        img, var, flags, (x, y) = saturated_star(flux)
        p = radial_profile(img, var, flags, x, y, RING_BAD, RING_CORE, frame_bkg=0.2)
        assert p is not None and p["core_r"] > 1.5  # the core saturates
        profiles.append(p)
    assert profiles[1]["core_r"] >= profiles[0]["core_r"]  # brighter star, wider saturated core
    r_in = int(np.ceil(max(p["core_r"] for p in profiles))) + 1
    f1, f2 = (ring_flux(p, r_in, r_in + 3, OMEGA)["flux_mjy"] for p in profiles)
    assert abs(f2 / f1 - 2.0) < 0.03


def test_unsaturated_source_has_no_core_and_ring_rejects_edge():
    img, var, flags, (x, y) = saturated_star(200.0)  # peak below saturation
    p = radial_profile(img, var, flags, x, y, RING_BAD, RING_CORE, frame_bkg=0.2)
    assert p["core_r"] == 0.0
    edge = radial_profile(img, var, flags, 2.0, 30.0, RING_BAD, RING_CORE, frame_bkg=0.2)
    assert ring_flux(edge, 4, 7, OMEGA) is None  # ring runs off the cutout


def _frame(band, flux, saturated):
    img, var, flags, (x, y) = saturated_star(flux)
    phot = {"flux_mjy": 1.0, "err_mjy": 0.1, "flagged": saturated, "ap_flags": 0, "masked_frac": 0.0,
            "method": "psf", "saturated": saturated, "npix": 20, "chi2": 1.0, "psf_weight": 0.5,
            "flux_ap_mjy": 1.0, "err_ap_mjy": 0.1}
    return {"band": band, "phot": phot, "omega": OMEGA,
            "ring": radial_profile(img, var, flags, x, y, RING_BAD, RING_CORE, frame_bkg=0.2)}


def test_apply_rings_switches_only_saturated_detectors():
    ok = [_frame("D1", f, True) for f in (2000.0, 3000.0, 4000.0)]
    ok += [_frame("D5", 200.0, False) for _ in range(3)]
    info = apply_rings(ok)
    assert set(info) == {"D1"}
    d1 = [r["phot"] for r in ok if r["band"] == "D1"]
    assert all(p["method"] == "ring" and p["relative"] for p in d1)
    assert len({(p["ring_r_in"], p["ring_r_out"]) for p in d1}) == 1  # one ring for the whole detector
    fl = [p["flux_mjy"] for p in d1]
    assert abs(fl[2] / fl[0] - 2.0) < 0.05
    assert all(r["phot"]["method"] == "psf" for r in ok if r["band"] == "D5")

