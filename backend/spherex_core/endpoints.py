"""Every remote source we use, registered with `upstreams` so the fastest healthy one is used first.

Kinds and their endpoints (order here is only the starting preference; measured latency and failures decide):

    spherex-files   AWS S3 open-data bucket · IRSA ibe (same files, same byte layout) · optional mirror (R2, ...)
    spherex-meta    local mirror (archive/mirror.py) · IRSA TAP · IRSA SIA
    gaia            ESA Gaia archive · MAST · IRSA · VizieR
    catwise         IRSA · VizieR · NOIRLab Data Lab
    2mass           IRSA · VizieR · VizieR CfA mirror (ASU) · NOIRLab Data Lab
    simbad          CDS · Harvard mirror
    names           CDS Sesame · Harvard Sesame mirror (raced, see targets.resolve)
    solar-system    JPL SB identification / Horizons · IRSA MOST (SPHEREx-centric) · SkyBoT (geocentric)
"""

from __future__ import annotations

import urllib.parse

from . import upstreams
from .config import settings
from .upstreams import Endpoint


def host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc


S3 = Endpoint("aws-s3", "spherex-files", host(settings.s3_base), "AWS S3 open data (us-east-1)", concurrency=96)
# Pseudo-services for IRSA's file server and cutout service: their failures must not open the breaker of IRSA TAP
# (same host, different service: ibe kept serving files while TAP answered 502 on 2026-11-15).
IRSA_FILES = Endpoint("irsa-ibe", "spherex-files", "irsa-ibe", "IRSA ibe HTTPS", concurrency=12, priority=1)
IRSA_CUTOUT = Endpoint("irsa-cutout", "spherex-cutouts", "irsa-cutout", "IRSA cutout service (ADR-0038)")
S3_CUTOUT = Endpoint("s3-range", "spherex-cutouts", host(settings.s3_base), "Range reads (S3 + fallbacks)", priority=1)
FILE_MIRROR = (Endpoint("file-mirror", "spherex-files", host(settings.file_mirror_base),
                        "S3-compatible mirror (SPHEREX_FILE_MIRROR_BASE)", concurrency=64, priority=-1)
               if settings.file_mirror_base else None)

META_MIRROR = Endpoint("local-mirror", "spherex-meta", "local-mirror", "Local SPHEREx file catalogue", priority=-1)
META_TAP = Endpoint("irsa-tap", "spherex-meta", host(settings.tap_url), "IRSA TAP")
META_SIA = Endpoint("irsa-sia", "spherex-meta", host(settings.irsa_sia_url), "IRSA SIA2", priority=1)

GAIA_ESA = Endpoint("esa-gaia", "gaia", host(settings.gaia_tap_url), "ESA Gaia archive")
GAIA_MAST = Endpoint("mast-gaia", "gaia", host(settings.mast_gaia_tap_url), "MAST (STScI)", priority=1)
GAIA_IRSA = Endpoint("irsa-gaia", "gaia", host(settings.tap_url), "IRSA gaia_dr3_source", priority=2)
GAIA_VIZIER = Endpoint("vizier-gaia", "gaia", host(settings.vizier_tap_url), "VizieR I/355", priority=3)

CATWISE_IRSA = Endpoint("irsa-catwise", "catwise", host(settings.tap_url), "IRSA catwise_2020")
CATWISE_VIZIER = Endpoint("vizier-catwise", "catwise", host(settings.vizier_tap_url), "VizieR II/365", priority=1)
CATWISE_DATALAB = Endpoint("datalab-catwise", "catwise", host(settings.datalab_tap_url),
                           "NOIRLab Data Lab catwise2020.main", priority=2)
TWOMASS_IRSA = Endpoint("irsa-2mass", "2mass", host(settings.tap_url), "IRSA fp_psc")
TWOMASS_VIZIER = Endpoint("vizier-2mass", "2mass", host(settings.vizier_tap_url), "VizieR II/246", priority=1)
TWOMASS_CFA = Endpoint("vizier-cfa-2mass", "2mass", host(settings.vizier_asu_url), "VizieR CfA mirror II/246 (ASU)",
                       priority=2)
TWOMASS_DATALAB = Endpoint("datalab-2mass", "2mass", host(settings.datalab_tap_url), "NOIRLab Data Lab twomass.psc",
                           priority=3)

SIMBAD = [Endpoint(f"simbad-{host(u).split('.')[1] if host(u).count('.') >= 2 else host(u)}", "simbad", host(u),
                   f"SIMBAD TAP ({host(u)})", priority=i) for i, u in enumerate(settings.simbad_tap_urls)]

SSO_JPL = Endpoint("jpl", "solar-system", "ssd-api.jpl.nasa.gov", "JPL SB identification / Horizons")
# MOST runs on IRSA's host but kept answering while TAP did not: its own pseudo-service, like ibe
SSO_MOST = Endpoint("irsa-most", "solar-system", "irsa-most", "IRSA MOST (SPHEREx-centric)", priority=1)
SSO_SKYBOT = Endpoint("skybot", "solar-system", "ssp.imcce.fr", "IMCCE SkyBoT", note="geocentric positions",
                      priority=2)

upstreams.register(*(e for e in (FILE_MIRROR, S3, IRSA_FILES) if e is not None))
upstreams.register(IRSA_CUTOUT, S3_CUTOUT)
upstreams.register(META_MIRROR, META_TAP, META_SIA)
upstreams.register(GAIA_ESA, GAIA_MAST, GAIA_IRSA, GAIA_VIZIER)
upstreams.register(CATWISE_IRSA, CATWISE_VIZIER, CATWISE_DATALAB, TWOMASS_IRSA, TWOMASS_VIZIER, TWOMASS_CFA,
                   TWOMASS_DATALAB)
upstreams.register(*SIMBAD)
upstreams.register(SSO_JPL, SSO_MOST, SSO_SKYBOT)


def file_url(ep: Endpoint, canonical: str) -> str | None:
    """The URL of a SPHEREx file at endpoint `ep`, from its canonical AWS S3 URL (None if not mappable)."""
    if not canonical.startswith(settings.s3_base):
        return canonical if ep is S3 else None
    key = canonical[len(settings.s3_base):]
    if ep is S3:
        return canonical
    if ep is IRSA_FILES:
        return settings.irsa_file_base + key
    if FILE_MIRROR is not None and ep is FILE_MIRROR:
        return settings.file_mirror_base.rstrip("/") + "/" + key
    return None
