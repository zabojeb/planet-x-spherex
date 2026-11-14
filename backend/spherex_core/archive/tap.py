"""Minimal synchronous TAP client (IRSA, ESA Gaia, any IVOA TAP service) returning CSV rows."""

from __future__ import annotations

import csv
import io
import logging
import time
import urllib.parse
import urllib.request

from ..config import settings

log = logging.getLogger(__name__)


def tap_csv(adql: str, url: str | None = None, timeout: float = 300, retries: int = 3,
            extra: dict | None = None) -> list[dict]:
    """Run a synchronous TAP query (default service: IRSA) and return CSV rows as dicts."""
    params = {"QUERY": adql, "FORMAT": "csv", "REQUEST": "doQuery", "LANG": "ADQL", **(extra or {})}
    full = (url or settings.tap_url) + "?" + urllib.parse.urlencode(params)
    last: Exception | None = None
    service = urllib.parse.urlsplit(full).netloc
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
            return rows
        except Exception as e:  # noqa: BLE001 - retry any transient failure
            last = e
            log.warning("tap query failed s=%.2f service=%s attempt=%d/%d error=%s", time.perf_counter() - t0,
                        service, attempt + 1, retries, f"{e.__class__.__name__}: {e}".splitlines()[0][:200])
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"TAP query failed after {retries} attempts: {last}")
