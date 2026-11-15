"""CSV of a long-baseline light curve: one row per point, every survey and band."""

from __future__ import annotations

import csv
import io

COLUMNS = ("band", "survey", "date", "mjd", "mjd_min", "mjd_max", "year", "flux_mjy", "err_mjy", "mag", "mag_err",
           "mag_system", "mag_anchored", "flux_anchored_mjy", "anchored", "group", "method", "relative", "rel",
           "rel_err", "saturated", "flagged", "calibrated", "n", "coverage")


def lightcurve_csv(result: dict) -> str:
    buf = io.StringIO()
    t = result.get("target") or {}
    buf.write(f"# long-baseline light curve of {t.get('name') or ''} ra={t.get('ra')} dec={t.get('dec')} "
              f"sequence={result.get('sequence')} version={result.get('version')}\n")
    buf.write("# flux in mJy; mag Vega for J/H/Ks/W1/W2, AB for SPHEREx D1-D6, -2.5 log10(rel) for relative points;\n")
    buf.write("# mag_anchored: zero point of fitted groups moved onto the band's reference group "
              "(see analysis.anchoring)\n")
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    w.writeheader()
    for p in result.get("points", []):
        w.writerow({k: ("" if p.get(k) is None else p.get(k)) for k in COLUMNS})
    return buf.getvalue()
