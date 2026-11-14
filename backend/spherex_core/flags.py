"""SPHEREx FLAGS bitmask: bit names, the masks the pipeline applies, and header parsing.

Bit definitions travel in the FLAGS header as MSKNnnnn (name) / MSKDnnnn (description) keywords;
FLAG_BITS mirrors them (identical in DR1 and QR3) so masks can be built without a header.
"""

from __future__ import annotations

import numpy as np

FLAG_BITS = {
    "TRANSIENT": 0, "OVERFLOW": 1, "SUR_ERROR": 2, "NONFUNC": 6, "DICHROIC": 7, "MISSING_DATA": 9,
    "HOT": 10, "COLD": 11, "FULLSAMPLE": 12, "PHANMISS": 14, "NONLINEAR": 15, "PERSIST": 17,
    "OUTLIER": 19, "CROSSTALK": 20, "SOURCE": 21, "GHOST": 22, "GHOST_FPA": 23, "GHOST_EXT": 24,
    "STREAK": 25, "BLOOM": 26, "SNOWBALL": 27, "HALO": 28,
}

FlagDefs = dict[int, tuple[str, str]]  # bit -> (name, description)

# Bits the pipeline sets itself in flags.bin. They never occur in SPHEREx files (FLAGS uses bits 0-28).
PIPELINE_DEFS: FlagDefs = {
    31: ("FILLED", "Pipeline: masked in every exposure of this epoch; value filled from the masked pixels"),
}
FILLED = 1 << 31


def flag_mask(*names: str) -> int:
    return sum(1 << FLAG_BITS[n] for n in names)


# Pixels dropped from images: cosmic rays, bad or lost pixels, persistence, streaks and snowballs.
MASK_DEFAULT = flag_mask("TRANSIENT", "SUR_ERROR", "NONFUNC", "MISSING_DATA", "HOT", "COLD", "PERSIST",
                         "OUTLIER", "STREAK", "SNOWBALL")
# Saturated or nonlinear pixels: kept in images, excluded from PSF fits.
SATURATION = flag_mask("OVERFLOW", "NONLINEAR")
# Bright-source artifacts that corrupt a measurement on the pixels they touch.
ARTIFACTS = flag_mask("GHOST", "BLOOM", "CROSSTALK")
# Pixels that make photometry unreliable. HALO is informational only: in the validation it barely changed
# fluxes (3.3 % vs 2.5 % scatter) while being set on ~18 % of measurements.
PHOT_BAD = MASK_DEFAULT | SATURATION | ARTIFACTS
# A measurement is flagged when more than this fraction of its aperture / PSF weight sits on masked pixels.
MAX_MASKED_FRACTION = 0.2


def read_flag_defs(header) -> FlagDefs:
    """Bit definitions from the MSKN/MSKD keywords of a FLAGS header."""
    defs: FlagDefs = {}
    for key in header:
        if key.startswith("MSKN"):
            defs[int(key[4:])] = (str(header[key]).strip(), str(header.get(f"MSKD{key[4:]}", "")).strip())
    return defs


def flag_defs_to_text(defs: FlagDefs) -> str:
    """Tab-separated 'bit name description' lines, as stored next to cached cutouts."""
    return "\n".join(f"{b}\t{n}\t{d}" for b, (n, d) in sorted(defs.items()))


def flag_defs_from_text(text: str) -> FlagDefs:
    defs: FlagDefs = {}
    for line in text.splitlines():
        b, n, d = (line.split("\t") + ["", ""])[:3]
        if b.strip().isdigit():
            defs[int(b)] = (n, d)
    return defs


def flag_bits_table(defs: FlagDefs) -> list[dict]:
    """The 'flag_bits' entries of meta.json: the file's bits, then the pipeline's own bits (pipeline: true)."""
    table = [{"bit": b, "name": name, "description": desc, "masked": bool(MASK_DEFAULT >> b & 1),
              "phot_bad": bool(PHOT_BAD >> b & 1)} for b, (name, desc) in sorted(defs.items())]
    table += [{"bit": b, "name": name, "description": desc, "masked": False, "phot_bad": False, "pipeline": True}
              for b, (name, desc) in sorted(PIPELINE_DEFS.items())]
    return table


def count_bits(flags: np.ndarray) -> dict[str, int]:
    """Number of pixels carrying each flag, only the flags that occur (in FLAG_BITS order)."""
    counts = {name: int(((flags >> bit) & 1).sum()) for name, bit in FLAG_BITS.items()}
    return {k: v for k, v in counts.items() if v}
