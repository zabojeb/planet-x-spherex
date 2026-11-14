"""Epoch merging, NaN-aware resampling and co-add hole filling (offline)."""

import numpy as np

from spherex_core.processing import fill_holes, group_epochs, merge_small_epochs, resample


def test_small_epoch_merges_into_close_neighbour():
    mjd = np.concatenate([np.linspace(0, 10, 21), [30.0, 31.0], np.linspace(200, 210, 25)])
    ep = merge_small_epochs(mjd, np.array([0] * 21 + [1] * 2 + [2] * 25))
    assert ep.tolist() == [0] * 23 + [1] * 25


def test_small_epoch_far_away_stays_partial():
    mjd = np.concatenate([np.linspace(0, 10, 21), [110.0, 111.0], np.linspace(400, 410, 25)])
    ep, partial = group_epochs(mjd)
    assert ep.tolist() == [0] * 21 + [1] * 2 + [2] * 25
    assert partial == [False, True, False]


def test_merge_prefers_the_closer_neighbour():
    mjd = np.concatenate([np.linspace(0, 10, 30), [45.0], np.linspace(50, 60, 30)])
    ep = merge_small_epochs(mjd, np.array([0] * 30 + [1] + [2] * 30))
    assert ep[30] == ep[31]  # 5 days to the next epoch beats 35 days to the previous one
    assert ep.max() == 1


def test_resample_does_not_grow_masked_areas():
    a = np.arange(100, dtype=float).reshape(10, 10)
    a[5, 5] = np.nan
    ys, xs = np.mgrid[0:10, 0:10].astype(float)
    out = resample(a, xs, ys)  # identity mapping
    assert np.isnan(out[5, 5]) and np.isfinite(out).sum() == 99
    out = resample(a, xs + 0.3, ys)  # sub-pixel shift: the hole stays one pixel wide
    assert np.isnan(out[5, 5]) and np.isfinite(out[5, 4]) and np.isfinite(out[5, 6])
    assert np.isclose(out[2, 2], a[2, 2] + 0.3)


def test_fill_holes_marks_pixels_masked_in_every_frame():
    mean = np.zeros((4, 4), dtype=np.float32)
    coverage = np.full((4, 4), 5, dtype=np.uint16)
    coverage[1, 2] = 0  # saturated core, masked everywhere
    coverage[3, 3] = 0  # outside the footprint, no data at all
    raw = np.full((5, 4, 4), np.nan, dtype=np.float32)
    raw[:, 1, 2] = [9.0, 10.0, 11.0, 12.0, 100.0]
    filled = fill_holes(mean, coverage, raw)
    assert filled.tolist() == [[False] * 4, [False, False, True, False], [False] * 4, [False] * 4]
    assert mean[1, 2] == 11.0 and mean[3, 3] == 0.0
