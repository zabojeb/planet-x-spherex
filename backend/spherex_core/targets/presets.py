"""The five test targets named in the challenge brief, with positions, proper motions and display hints.

default_band / default_mode say what the web client opens a preset with: the view that shows what the
target is in the challenge for (motion, a cold brown dwarf, variability).
"""

from __future__ import annotations

from .model import Target

PRESETS: dict[str, Target] = {
    t.id: t
    for t in [
        Target(
            id="barnard", name="Barnard's Star", ra=269.4520769586, dec=4.6933649666,
            pmra=-801.551, pmdec=10362.394, ref_epoch=2000.0, parallax=546.976,
            source="SIMBAD (Gaia DR3)", fov_arcmin=6.0,
            default_band="all", default_mode="diff",  # the motion shows as a +/- pair between epochs
            description="M4 dwarf with the largest proper motion of any star, 10.4″/yr.",
        ),
        Target(
            id="eps-indi-b", name="ε Indi B", ra=331.0441465885, dec=-56.7828441951,
            pmra=3981.977, pmdec=-2466.832, ref_epoch=2000.0, parallax=270.658,
            source="SIMBAD (Gaia DR3)", fov_arcmin=16.0,
            default_band="all", default_mode="blink",  # B and A move together across the 16′ field
            description="T1 + T6 brown-dwarf binary, 6.7′ from the star ε Indi A; moves 4.7″/yr.",
        ),
        Target(
            id="wise-0855", name="WISE 0855−0714", ra=133.7947597, dec=-7.2451456,
            pmra=-8118.0, pmdec=677.0, ref_epoch=2010.5896, parallax=449.0,
            source="AllWISE J085510.74−071442.5; motion Luhman & Esplin 2016", fov_arcmin=6.0,
            default_band="D6", default_mode="rgb",  # only the long-wavelength end shows it; red in RGB
            description="Coldest known brown dwarf (~285 K), 8.1″/yr. Visible only at the long-wavelength end "
                        "(D5–D6); in 2025–26 it passes 8″ from a G = 16.3 star that dominates D1–D4.",
        ),
        Target(
            id="rw-aur", name="RW Aurigae", ra=76.9565260346, dec=30.4014381739,
            pmra=7.23, pmdec=-20.33, ref_epoch=2000.0, source="SIMBAD", fov_arcmin=6.0,
            default_band="D6", default_mode="blink",  # faded by ~2x at 4.4-5 um over the year
            description="Young T Tauri binary with deep dimming episodes.",
        ),
        Target(
            id="r-dra", name="R Draconis", ra=248.1676108330, dec=66.7549794172,
            pmra=-20.643, pmdec=6.149, ref_epoch=2000.0, source="SIMBAD (Gaia DR3)", fov_arcmin=6.0,
            default_band="D1", default_mode="blink",  # a Mira's amplitude is largest at short wavelengths
            description="Mira variable with a ≈245-day period, next to the north ecliptic pole deep field.",
        ),
    ]
}
