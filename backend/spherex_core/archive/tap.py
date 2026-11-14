"""Minimal synchronous TAP client (IRSA, ESA Gaia, any IVOA TAP service) returning CSV rows."""

from __future__ import annotations

import csv
import io
import time
import urllib.parse
import urllib.request

from ..config import settings


def tap_csv(adql: str, url: str | None = None, timeout: float = 300, retries: int = 3,
            extra: dict | None = None) -> list[dict]:
    """Run a synchronous TAP query (default service: IRSA) and return CSV rows as dicts."""
    params = {"QUERY": adql, "FORMAT": "csv", "REQUEST": "doQuery", "LANG": "ADQL", **(extra or {})}
    full = (url or settings.tap_url) + "?" + urllib.parse.urlencode(params)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(full, timeout=timeout) as r:
                text = r.read().decode()
            if text.lstrip().startswith("<"):  # VOTable error document instead of CSV
                raise RuntimeError(text[:300])
            return list(csv.DictReader(io.StringIO(text)))
        except Exception as e:  # noqa: BLE001 - retry any transient failure
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"TAP query failed after {retries} attempts: {last}")
