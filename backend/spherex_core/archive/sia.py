"""Image search through IRSA's SIA2 service: a second live source for the frames of a position when TAP fails.

SIA answers from a different service stack than TAP at IRSA (ingestion lags by about a day), so it is the
fallback after the local mirror and TAP. Returns the same Frame objects as the TAP path.
"""

from __future__ import annotations

import io
import logging
import re
import time
import urllib.parse
import urllib.request

from .. import netpolicy
from ..config import settings

log = logging.getLogger(__name__)
_DET = re.compile(r"_\d+D(\d)_spx")


def query_frames(ra: float, dec: float, collections: tuple[str, ...], timeout: float = 120, retries: int = 2) -> list:
    from astropy.io.votable import parse

    from .spherex import Frame, dedupe

    timeout, retries = netpolicy.timeouts(timeout, retries)
    service = urllib.parse.urlsplit(settings.irsa_sia_url).netloc
    rows = []
    for coll in collections:
        params = urllib.parse.urlencode({"COLLECTION": coll, "POS": f"CIRCLE {ra:.7f} {dec:.7f} 0.0003",
                                         "RESPONSEFORMAT": "votable", "MAXREC": "100000"})
        url = f"{settings.irsa_sia_url}?{params}"
        last: Exception | None = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as r:
                    table = parse(io.BytesIO(r.read()), verify="ignore").get_first_table().to_table()
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(netpolicy.pause(1.5 * (attempt + 1)))
        else:
            raise RuntimeError(f"SIA query failed for {coll}: {last}")
        for rec in table:
            access = str(rec["access_url"])
            if not access.endswith(".fits"):
                continue
            uri = access.split("irsa.ipac.caltech.edu/", 1)[-1].split("?", 1)[0]
            m = _DET.search(uri)
            band = str(rec["energy_bandpassname"]).replace("SPHEREx-", "") if "energy_bandpassname" in table.colnames \
                else (f"D{m.group(1)}" if m else "")
            t0, t1 = float(rec["t_min"]), float(rec["t_max"])
            rows.append(Frame(uri=uri, band=band, mjd=(t0 + t1) / 2, mjd_start=t0, mjd_end=t1,
                              collection=str(rec["obs_collection"]) or coll, obs_id=str(rec["obs_id"]),
                              wl_min_um=float(rec["em_min"]) * 1e6, wl_max_um=float(rec["em_max"]) * 1e6))
    log.info("sia search done service=%s frames=%d", service, len(rows))
    return dedupe(rows, collections)
