import numpy as np
from astropy.io import fits

from spherex_core.flags import (ARTIFACTS, FILLED, FLAG_BITS, MASK_DEFAULT, PHOT_BAD, SATURATION, count_bits,
                                flag_bits_table, flag_defs_from_text, flag_defs_to_text, flag_mask, read_flag_defs)


def _flags_header() -> fits.Header:
    h = fits.Header()
    h["EXTNAME"] = "FLAGS"
    h["MSKN0000"], h["MSKD0000"] = "TRANSIENT", "Transient detected during SUR"
    h["MSKN0001"], h["MSKD0001"] = "OVERFLOW", "Overflow detected during SUR"
    h["MSKN0021"], h["MSKD0021"] = "SOURCE", "Pixel mapped to a known source"
    return h


def test_read_flag_defs_from_header():
    defs = read_flag_defs(_flags_header())
    assert defs == {0: ("TRANSIENT", "Transient detected during SUR"),
                    1: ("OVERFLOW", "Overflow detected during SUR"),
                    21: ("SOURCE", "Pixel mapped to a known source")}


def test_text_round_trip():
    defs = read_flag_defs(_flags_header())
    assert flag_defs_from_text(flag_defs_to_text(defs)) == defs
    assert flag_defs_from_text("junk line\n\n") == {}


def test_masks():
    assert flag_mask("TRANSIENT", "HOT") == 1 | (1 << 10)
    assert MASK_DEFAULT & (1 << FLAG_BITS["TRANSIENT"])
    assert not MASK_DEFAULT & (1 << FLAG_BITS["SOURCE"])  # known sources are never masked
    assert not MASK_DEFAULT & (1 << FLAG_BITS["OVERFLOW"])  # saturated cores stay visible...
    assert PHOT_BAD & (1 << FLAG_BITS["OVERFLOW"])  # ...but make photometry unreliable
    assert PHOT_BAD & MASK_DEFAULT == MASK_DEFAULT


def test_photometry_policy():
    assert not PHOT_BAD & (1 << FLAG_BITS["HALO"])  # informational since ALGO_VERSION 2
    assert PHOT_BAD & SATURATION == SATURATION and PHOT_BAD & ARTIFACTS == ARTIFACTS
    assert FILLED == 1 << 31 and not FILLED & (PHOT_BAD | MASK_DEFAULT)


def test_flag_bits_table_and_counts():
    table = flag_bits_table(read_flag_defs(_flags_header()))
    assert [t["bit"] for t in table] == [0, 1, 21, 31]
    assert table[-1]["name"] == "FILLED" and table[-1]["pipeline"] is True and not table[-1]["masked"]
    assert table[0] == {"bit": 0, "name": "TRANSIENT", "description": "Transient detected during SUR",
                        "masked": True, "phot_bad": True}
    assert table[1]["masked"] is False and table[1]["phot_bad"] is True
    flags = np.array([[1, 1 << 21], [(1 << 21) | 1, 0]], dtype=np.uint32)
    assert count_bits(flags) == {"TRANSIENT": 2, "SOURCE": 2}
    assert list(count_bits(flags)) == ["TRANSIENT", "SOURCE"]  # FLAG_BITS order, as in meta.json
