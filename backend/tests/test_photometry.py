"""PSF photometry with an ePSF, saturation handling and the systematic floor (offline, synthetic data)."""

import numpy as np

from spherex_core.flags import ARTIFACTS, FLAG_BITS, MASK_DEFAULT, PHOT_BAD, SATURATION
from spherex_core.photometry import (SYSTEMATIC_FLOOR, epsf_values, measure, mjy_per_unit, psf_model,
                                     psf_photometry)

OVS = 5
OMEGA = 37.86  # arcsec^2, SPHEREx pixel
OVERFLOW = 1 << FLAG_BITS["OVERFLOW"]
TRANSIENT = 1 << FLAG_BITS["TRANSIENT"]


def gaussian_epsf(sigma_native=0.65, size=33):
    """A 5x-oversampled, pixel-convolved Gaussian ePSF summing to 1 (photutils convention)."""
    c = (size - 1) / 2
    y, x = np.mgrid[0:size, 0:size]
    s = sigma_native * OVS
    # pixel convolution approximated by adding the variance of a unit box (1/12 px^2)
    s2 = s * s + OVS * OVS / 12.0
    psf = np.exp(-((x - c) ** 2 + (y - c) ** 2) / (2 * s2))
    return psf / psf.sum()


def star_image(psf, x, y, flux_native, bkg=0.3, shape=(31, 31), noise=0.0, seed=0):
    gy, gx = np.mgrid[0:shape[0], 0:shape[1]]
    img = bkg + flux_native * psf_model(psf, OVS, gx - x, gy - y, x - round(x), y - round(y))
    rng = np.random.default_rng(seed)
    var = np.full(shape, noise ** 2 if noise else 1e-4)
    if noise:
        img = img + rng.normal(0, noise, shape)
    return img.astype(np.float32), var.astype(np.float32), np.zeros(shape, dtype=np.int32)


def test_model_sums_to_one_at_any_phase():
    psf = gaussian_epsf()
    for fx, fy in [(0.0, 0.0), (0.3, -0.2), (-0.5, 0.5)]:
        k = np.arange(-8, 9, dtype=float)
        ox, oy = np.meshgrid(k - fx, k - fy)
        assert abs(psf_model(psf, OVS, ox, oy, fx, fy).sum() - 1) < 1e-9
    # raw ePSF sampling already conserves flux to ~1 % at native resolution
    k = np.arange(-8, 9, dtype=float)
    ox, oy = np.meshgrid(k - 0.3, k + 0.1)
    assert abs(epsf_values(psf, OVS, ox, oy).sum() - 1) < 0.01


def test_psf_fit_recovers_flux_and_background():
    psf = gaussian_epsf()
    img, var, flags = star_image(psf, 15.3, 14.6, flux_native=50.0, bkg=0.3)
    r = psf_photometry(img, var, flags, 15.3, 14.6, psf, OVS, OMEGA, MASK_DEFAULT, SATURATION, ARTIFACTS)
    assert r is not None and not r["saturated"] and not r["flagged"]
    assert abs(r["flux_mjy"] / (50.0 * mjy_per_unit(OMEGA)) - 1) < 1e-6
    assert abs(r["bkg"] - 0.3) < 1e-6 and r["chi2"] < 1e-6


def test_saturated_core_is_skipped():
    psf = gaussian_epsf()
    img, var, flags = star_image(psf, 15.0, 15.0, flux_native=500.0, noise=0.01)
    flags[14:17, 14:17] = OVERFLOW | TRANSIENT  # saturated pixels also carry masked bits in real data
    img[14:17, 14:17] = 3.0  # clipped values
    r = psf_photometry(img, var, flags, 15.0, 15.0, psf, OVS, OMEGA, MASK_DEFAULT, SATURATION, ARTIFACTS)
    assert r is not None and r["saturated"]
    assert r["flagged"] and r["psf_weight"] < 0.2  # only the wings are left: measured but flagged
    assert abs(r["flux_mjy"] / (500.0 * mjy_per_unit(OMEGA)) - 1) < 0.02
    flags[14:17, 14:17] = 0  # only the central pixel saturated: enough PSF left, not flagged
    flags[15, 15] = OVERFLOW
    img2, _, _ = star_image(psf, 15.0, 15.0, flux_native=500.0, noise=0.01)
    img2[15, 15] = 3.0
    r = psf_photometry(img2, var, flags, 15.0, 15.0, psf, OVS, OMEGA, MASK_DEFAULT, SATURATION, ARTIFACTS)
    assert r["saturated"] and not r["flagged"]
    assert abs(r["flux_mjy"] / (500.0 * mjy_per_unit(OMEGA)) - 1) < 0.01


def test_fully_saturated_falls_back_to_aperture():
    psf = gaussian_epsf()
    img, var, flags = star_image(psf, 15.0, 15.0, flux_native=5000.0)
    flags[11:20, 11:20] = OVERFLOW  # nothing usable left inside the ePSF support
    assert psf_photometry(img, var, flags, 15.0, 15.0, psf, OVS, OMEGA, MASK_DEFAULT, SATURATION,
                          ARTIFACTS) is None
    rec = measure(img, var, flags, 15.0, 15.0, 7.5, OMEGA, psf, OVS, MASK_DEFAULT, SATURATION, ARTIFACTS,
                  PHOT_BAD)
    assert rec["method"] == "aperture" and rec["saturated"] and rec["flagged"]
    assert rec["npix"] is None and rec["chi2"] is None


def test_measure_record_and_systematic_floor():
    psf = gaussian_epsf()
    img, var, flags = star_image(psf, 15.2, 15.1, flux_native=80.0, noise=0.005, seed=3)
    rec = measure(img, var, flags, 15.2, 15.1, 7.5, OMEGA, psf, OVS, MASK_DEFAULT, SATURATION, ARTIFACTS,
                  PHOT_BAD)
    assert rec["method"] == "psf" and not rec["saturated"] and not rec["flagged"]
    assert rec["err_mjy"] >= SYSTEMATIC_FLOOR * rec["flux_mjy"]
    assert rec["flux_ap_mjy"] is not None and rec["err_ap_mjy"] >= SYSTEMATIC_FLOOR * abs(rec["flux_ap_mjy"])
    assert abs(rec["flux_mjy"] / (80.0 * mjy_per_unit(OMEGA)) - 1) < 0.02
    assert set(rec) >= {"method", "flux_mjy", "err_mjy", "saturated", "npix", "chi2", "flux_ap_mjy",
                        "err_ap_mjy", "flagged", "ap_flags", "masked_frac"}


def test_masked_weight_flags_the_fit():
    psf = gaussian_epsf()
    img, var, flags = star_image(psf, 15.0, 15.0, flux_native=80.0)
    flags[15, 13:16] = TRANSIENT  # cosmic-ray hit across the core: > 20 % of the PSF weight
    r = psf_photometry(img, var, flags, 15.0, 15.0, psf, OVS, OMEGA, MASK_DEFAULT, SATURATION, ARTIFACTS)
    assert r is not None and r["masked_frac"] > 0.2 and r["flagged"]


def _same(a, b) -> bool:
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(_same(u, v) for u, v in zip(a, b))
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return np.array_equal(np.asarray(a), np.asarray(b), equal_nan=True)
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (np.isnan(a) and np.isnan(b))
    return a == b


def test_variance_is_read_only_within_variance_radius():
    """The reader fetches VARIANCE only within VARIANCE_RADIUS rows of the target (ADR-0037): every measurement
    must come out the same as with the full variance, for a faint and for a saturated star."""
    from spherex_core.flags import RING_BAD, RING_CORE
    from spherex_core.photometry import VARIANCE_RADIUS, radial_profile

    psf = gaussian_epsf()
    rng = np.random.default_rng(7)
    for flux, saturate in [(80.0, False), (4000.0, True)]:
        x, y = 44.3, 47.6
        img, _, flags = star_image(psf, x, y, flux_native=flux, shape=(91, 91), noise=0.01, seed=5)
        var = rng.uniform(5e-5, 2e-4, img.shape).astype(np.float32)
        flags[rng.random(img.shape) < 0.03] = OVERFLOW
        if saturate:
            flags[45:51, 42:47] = OVERFLOW | TRANSIENT
        k = int(np.floor(y))

        def windowed(radius):
            v = np.full_like(var, np.nan)
            v[k - radius:k + radius + 1] = var[k - radius:k + radius + 1]
            return v

        def run(v):
            return (measure(img, v, flags, x, y, 7.5, OMEGA, psf, OVS, MASK_DEFAULT, SATURATION, ARTIFACTS, PHOT_BAD),
                    radial_profile(img, v, flags, x, y, RING_BAD, RING_CORE, frame_bkg=0.3))

        assert _same(run(var), run(windowed(VARIANCE_RADIUS)))
        assert not _same(run(var)[1], run(windowed(VARIANCE_RADIUS - 3))[1])  # the test can see a short window
