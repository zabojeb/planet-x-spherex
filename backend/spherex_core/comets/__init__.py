"""Comet discovery (ADR-0063): find moving sources that look like comets, and watch the known ones.

* hunt.py: the comet search job over a sequence (fast tracklets, slow links over days, shift-and-stack), ranked by
  comet-likeness; on a co-moving sequence the same tests run on its own object ("object check").
* coma.py: extent and tail of a moving source against field stars built the same way.
* signature.py: CO2 4.26 / CO 4.67 / H2O bands against the reflected-sunlight continuum.
* slow.py: group maps, parallax-aware multi-day linking and shift-and-stack for slow, distant movers.
* orbits.py: two-body elements and propagation, and the digest2-style orbit-class score of a short arc.
* score.py: the comet-likeness combination.
* geometry.py: Sun direction, anti-solar position angle, elongation, SPHEREx's heliocentric track and parallax.
* watch.py: the known-comet watch (JPL SBDB comets now, IRSA MOST, pinned prewarm co-moving builds, activity).
"""

from .hunt import SEARCH_VERSION, HuntOptions, comet_search

__all__ = ["SEARCH_VERSION", "HuntOptions", "comet_search"]
