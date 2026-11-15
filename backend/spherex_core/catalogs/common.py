"""Helpers shared by the catalog modules. Every source dict has: id, ra, dec, epoch (Julian year of the
position), pmra, pmdec (mas/yr, pmra includes cos dec; None if unknown), mag, mag_band."""

from __future__ import annotations

import math
import urllib.parse
import urllib.request

MAX_ROWS = 3000


def num(v) -> float | None:
    """Float from a CSV cell, None for empty or non-numeric cells."""
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def circle(ra: float, dec: float, r: float, ra_col: str = "ra", dec_col: str = "dec") -> str:
    """ADQL cone constraint (on columns named ra/dec unless given)."""
    return f"1 = CONTAINS(POINT('ICRS', {ra_col}, {dec_col}), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {r:.6f}))"


def box(ra: float, dec: float, r: float, ra_col: str = "ra", dec_col: str = "dec") -> str:
    """Plain-SQL box around the cone, for TAP services without ADQL geometry (NOIRLab Data Lab); keep only the
    rows `in_circle` afterwards. Handles RA wrap-around and the poles."""
    d0, d1 = max(dec - r, -90.0), min(dec + r, 90.0)
    where = f"{dec_col} BETWEEN {d0:.7f} AND {d1:.7f}"
    if max(abs(d0), abs(d1)) >= 89.9:
        return where
    w = min(r / math.cos(math.radians(max(abs(d0), abs(d1)))), 180.0)
    lo, hi = ra - w, ra + w
    if lo < 0:
        return f"{where} AND ({ra_col} >= {lo + 360:.7f} OR {ra_col} <= {hi:.7f})"
    if hi >= 360:
        return f"{where} AND ({ra_col} >= {lo:.7f} OR {ra_col} <= {hi - 360:.7f})"
    return f"{where} AND {ra_col} BETWEEN {lo:.7f} AND {hi:.7f}"


def in_circle(ra: float, dec: float, r: float, ra2: float | None, dec2: float | None) -> bool:
    if ra2 is None or dec2 is None:
        return False
    a, b, da = math.radians(dec), math.radians(dec2), math.radians(ra2 - ra)
    c = math.sin(a) * math.sin(b) + math.cos(a) * math.cos(b) * math.cos(da)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) <= r


def vizier_asu(source: str, ra: float, dec: float, r: float, columns: list[str], sort: str | None = None,
               timeout: float = 60.0) -> tuple[list[dict], dict]:
    """A VizieR cone over plain ASU (tab-separated; the CfA mirror has no TAP): (rows as dicts of strings,
    units per column)."""
    from .. import netpolicy, upstreams
    from ..config import settings

    q = {"-source": source, "-c": f"{ra:.7f} {dec:+.7f}", "-c.rd": f"{r:.6f}", "-out": ",".join(columns),
         "-out.max": str(MAX_ROWS)}
    if sort:
        q["-sort"] = sort
    url = settings.vizier_asu_url + "?" + urllib.parse.urlencode(q)
    service = urllib.parse.urlsplit(url).netloc
    netpolicy.check(service)
    timeout, _ = netpolicy.timeouts(timeout, 1)
    if upstreams.has_fallback():
        timeout = min(timeout, upstreams.FALLBACK_TIMEOUT_S)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as e:
        netpolicy.failed(service, e)
        raise RuntimeError(f"VizieR ASU ({service}) failed: {e}") from e
    if text.lstrip().startswith("<"):  # an HTML error page (CDS's "Page unavailable")
        netpolicy.failed(service, "HTML error page")
        raise RuntimeError(f"VizieR ASU ({service}) answered an error page")
    netpolicy.succeeded(service)
    return parse_asu_tsv(text)


def parse_asu_tsv(text: str) -> tuple[list[dict], dict]:
    """VizieR asu-tsv: '#' comments, then column names, units, a dashes line and the data rows (all
    tab-separated, cells space-padded). An empty table has no header at all."""
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    dash = next((i for i, ln in enumerate(lines) if ln.strip() and set(ln) <= set("-\t ")), None)
    if dash is None or dash < 2:
        return [], {}
    names = [c.strip() for c in lines[dash - 2].split("\t")]
    units = dict(zip(names, (u.strip() for u in lines[dash - 1].split("\t"))))
    rows = []
    for ln in lines[dash + 1:]:
        if not ln.strip():  # end of the table
            break
        rows.append(dict(zip(names, (c.strip() for c in ln.split("\t")))))
    return rows, units
