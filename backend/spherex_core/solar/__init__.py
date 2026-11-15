"""Solar-system science on co-moving sequences (ADR-0057): "different background, same comet".

    static_sky   template of the static sky behind the object, clean exposures and stacks (a build step)
    skyframes    images that cover a fixed sky point (TAP, or the MOST fixed-point search)
    geometry     heliocentric and SPHEREx-centric distances and phase angle per exposure
    comet        km-aperture photometry, coma profile, Afrho proxy, emission/absorption bands, activity
    asteroid     rotational light curve, period search, colours, spectral slope, hydration band, taxonomy
    movers       unidentified moving-object candidates from field analysis, with ADES export

Analyses read a finished sequence and run on the field-analysis job runner (field.jobs); the static-sky step
runs inside the sequence build on the build queue (spherex_core.jobs).
"""
