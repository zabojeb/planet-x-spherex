"""Minimal synchronous TAP client (IRSA, ESA Gaia, any IVOA TAP service) returning CSV rows."""

from __future__ import annotations

import csv
import io
import logging
import time
import urllib.parse
import urllib.request

from .. import netpolicy
from ..config import settings

log = logging.getLogger(__name__)


def tap_csv(adql: str, url: str | None = None, timeout: float = 300, retries: int = 3,
            extra: dict | None = None) -> list[dict]:
    """Run a synchronous TAP query (default service: IRSA) and return CSV rows as dicts."""
    params = {"QUERY": adql, "FORMAT": "csv", "REQUEST": "doQuery", "LANG": "ADQL", **(extra or {})}
    full = (url or settings.tap_url) + "?" + urllib.parse.urlencode(params)
    last: Exception | None = None
    service = urllib.parse.urlsplit(full).netloc
    netpolicy.check(service)  # interactive callers skip a service that failed minutes ago
    requested = timeout
    timeout, retries = netpolicy.timeouts(timeout, retries)
    from .. import upstreams

    if upstreams.has_fallback():  # another source can answer: one quick attempt, then let it
        timeout, retries = min(timeout, upstreams.FALLBACK_TIMEOUT_S), 1
    elif upstreams.last_resort() and netpolicy.is_fast():  # the faster sources failed: one longer attempt
        timeout, retries = max(timeout, min(requested, upstreams.LAST_RESORT_TIMEOUT_S)), 1
    for attempt in range(retries):
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(full, timeout=timeout) as r:
                text = r.read().decode()
            if text.lstrip().startswith("<"):  # VOTable error document instead of CSV
                raise RuntimeError(text[:300])
            rows = list(csv.DictReader(io.StringIO(text)))
            log.info("tap query done s=%.2f service=%s rows=%d kb=%.0f attempt=%d", time.perf_counter() - t0,
                     service, len(rows), len(text) / 1e3, attempt + 1)
            netpolicy.succeeded(service)
            return rows
        except Exception as e:  # noqa: BLE001 - retry any transient failure
            last = e
            log.warning("tap query failed s=%.2f service=%s attempt=%d/%d error=%s", time.perf_counter() - t0,
                        service, attempt + 1, retries, f"{e.__class__.__name__}: {e}".splitlines()[0][:200])
            if attempt < retries - 1:
                time.sleep(netpolicy.pause(2.0 * (attempt + 1)))
    netpolicy.failed(service, last or "failed")
    raise RuntimeError(f"TAP query failed after {retries} attempts: {last}")
