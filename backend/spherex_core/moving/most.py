"""IRSA Moving Object Search Tool (MOST): which SPHEREx exposures saw a solar-system object, and where.

MOST resolves the object with JPL Horizons, integrates its orbit over the requested dates and matches the
predicted positions against SPHEREx image footprints. We call its GET API
(https://irsa.ipac.caltech.edu/cgi-bin/MOST/nph-most, catalog=spherex, output_mode=Brief) and parse the
IPAC table: one row per matched Level 2 file with the object's predicted position (ra_obj, dec_obj) at
the exposure start, distances, phase angle, V magnitude and the file uri. Answers are cached on disk
under <cache>/moving/ because long windows take minutes (433 Eros over 19 months: ~165 s, 954 rows).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date

from astropy.io import ascii as asciitable

from .. import cache, endpoints, upstreams
from ..errors import UpstreamError

MOST_URL = "https://irsa.ipac.caltech.edu/cgi-bin/MOST/nph-most"
MISSION_START = "2025-04-01"  # SPHEREx survey start; default beginning of the search window
CACHE_HOURS = 24 * 7
_COMET = re.compile(r"^\s*(\d+\s*[PID](\b|/)|[PCDXI]/|\d+I\b)", re.I)


class AmbiguousObject(LookupError):
    """Horizons found several objects for the name. `choices` lists them (name, type, naif_id)."""

    def __init__(self, message: str, choices: list[dict]):
        super().__init__(message)
        self.choices = choices


@dataclass
class MostRow:
    """One matched Level 2 file and the object's predicted position at its start time."""

    mjd_start: float
    mjd_end: float
    ra: float
    dec: float
    band: str  # D1..D6
    uri: str  # ibe/data/spherex/<release>/level2/...
    obs_id: str
    vmag: float | None = None
    sun_dist: float | None = None  # au
    geo_dist: float | None = None  # au, from the observer
    phase: float | None = None  # deg
    dist_ctr: float | None = None  # deg, distance from the image centre

    @property
    def mjd(self) -> float:
        return (self.mjd_start + self.mjd_end) / 2.0

    @property
    def collection(self) -> str:
        """spherex_dr1, spherex_qr3, ... from the release folder in the uri ('_deep' when in a deep folder)."""
        m = re.search(r"spherex/([a-z0-9]+)(_deep)?/", self.uri)
        release = m.group(1) if m else "unknown"
        return f"spherex_{release}{'_deep' if (m and m.group(2)) or '/deep/' in self.uri else ''}"


@dataclass
class MostResult:
    object_name: str
    query: dict
    elements: dict = field(default_factory=dict)
    rows: list[MostRow] = field(default_factory=list)

    def ephemeris(self) -> list[list[float]]:
        """[[mjd_start, ra, dec], ...], one per exposure, sorted by time."""
        seen: dict[float, list[float]] = {}
        for r in self.rows:
            seen.setdefault(round(r.mjd_start, 7), [r.mjd_start, r.ra, r.dec])
        return [seen[k] for k in sorted(seen)]

    def to_dict(self) -> dict:
        return {"object_name": self.object_name, "query": self.query, "elements": self.elements,
                "rows": [asdict(r) for r in self.rows]}

    @classmethod
    def from_dict(cls, d: dict) -> "MostResult":
        return cls(d["object_name"], d["query"], d.get("elements", {}), [MostRow(**r) for r in d["rows"]])


def guess_type(name: str) -> str:
    """'Comet' for designations like 3I, 1P/Halley, C/2025 N1, 12P; 'Asteroid' otherwise."""
    return "Comet" if _COMET.match(name) else "Asteroid"


def build_query(name: str | None = None, obj_type: str | None = None, begin: str | None = None,
                end: str | None = None, naif_id: int | None = None) -> dict:
    """Normalised MOST parameters (also the cache key)."""
    q = {"catalog": "spherex", "output_mode": "Brief", "ephem_step": "0.25",
         "obs_begin": begin or MISSION_START, "obs_end": end or date.today().isoformat()}
    if naif_id is not None:
        q.update(input_type="naifid_input", obj_naifid=str(int(naif_id)))
    elif name and name.strip():
        q.update(input_type="name_input", obj_name=name.strip(), obj_type=(obj_type or guess_type(name)).title())
    else:
        raise ValueError("give an object name or a NAIF ID")
    return q


def cache_path(q: dict):
    """Disk cache file of a MOST answer for normalised parameters (build_query)."""
    key = hashlib.sha1(json.dumps(q, sort_keys=True).encode()).hexdigest()[:16]
    return cache.layout().root / "moving" / f"most_{key}.json"


_cache_path = cache_path


def cached(q: dict) -> MostResult | None:
    """The cached answer for normalised parameters (build_query), if still fresh."""
    path = _cache_path(q)
    if cache.is_fresh(path, CACHE_HOURS):
        return MostResult.from_dict(json.loads(path.read_text()))
    return None


def query_most(name: str | None = None, obj_type: str | None = None, begin: str | None = None,
               end: str | None = None, naif_id: int | None = None, timeout: float = 600,
               use_cache: bool = True) -> MostResult:
    """Matched SPHEREx exposures for a solar-system object. Raises AmbiguousObject, LookupError
    (unknown object) or UpstreamError (MOST unreachable or unreadable)."""
    q = build_query(name, obj_type, begin, end, naif_id)
    path = _cache_path(q)
    if use_cache and cache.is_fresh(path, CACHE_HOURS):
        return MostResult.from_dict(json.loads(path.read_text()))
    url = MOST_URL + "?" + urllib.parse.urlencode(q)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        # 4xx answers (unknown or ambiguous object) mean the service works
        upstreams.record(endpoints.SSO_MOST, time.perf_counter() - t0, e.code < 500, e, breaker=False)
        raise _error_from_body(body, e.code) from e
    except Exception as e:  # noqa: BLE001 - network failures
        upstreams.record(endpoints.SSO_MOST, time.perf_counter() - t0, False, e, breaker=False)
        raise UpstreamError(f"IRSA MOST is unavailable: {e}") from e
    upstreams.record(endpoints.SSO_MOST, time.perf_counter() - t0, True, breaker=False)
    result = parse_most(text, q)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result.to_dict()))
    tmp.replace(path)
    return result


_DESIGNATION = re.compile(r"^(NO_VALUE|\d{4}-\d{3}[A-Z]+|[A-Z]?\d{3,4} [A-Z]{2}\d*|[PCDXI]/\d{4} [A-Z]+\d*)\s+")


def _parse_choices(text: str) -> list[dict]:
    """Candidates listed by MOST/Horizons: '<name>, Object Type=<t>, NAIF ID=<id>, Designation=<d> <next name>...'.
    The designation runs into the next name without a separator, so known designation forms are peeled off."""
    for marker in ("from Horizons.", "was found!"):
        if marker in text:
            text = text.split(marker, 1)[1]
    choices: list[dict] = []
    for seg in text.split("Designation="):
        m = re.search(r"(.*?), Object Type=([^,]*), NAIF ID=(-?\d+)", seg)
        head = (seg if m is None else m.group(1)).strip()
        # only segments after the first start with the previous candidate's designation
        d = _DESIGNATION.match(head) if choices else None
        if d:
            choices[-1]["designation"] = d.group(1)
        if m is None:
            continue
        name = (head[d.end():].strip() or head) if d else head
        choices.append({"name": name, "type": m.group(2).strip() or None, "naif_id": int(m.group(3)),
                        "designation": None})
    return choices


def _error_from_body(body: str, status: int) -> Exception:
    text = " ".join(re.sub(r"<[^>]+>", " ", body).split())
    choices = _parse_choices(text)
    if "multiple matches" in text.lower():
        return AmbiguousObject("Several objects match this name; pick one by NAIF ID.", choices)
    if "no matching" in text.lower() or "error" in text.lower():
        msg = "MOST/Horizons does not know this object."
        if choices:
            msg += " Did you mean: " + "; ".join(f"{c['name']} (NAIF ID {c['naif_id']})" for c in choices[:5])
        err = LookupError(msg)
        err.choices = choices  # type: ignore[attr-defined]
        return err
    return UpstreamError(f"IRSA MOST answered HTTP {status}: {text[:200]}")


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def parse_most(text: str, query: dict) -> MostResult:
    """Parse a MOST 'Brief' IPAC table answer."""
    keywords = dict(re.findall(r'^\\(\w+)\s*=\s*"?(.*?)"?\s*$', text, flags=re.M))
    name = keywords.get("object_name", query.get("obj_name") or query.get("obj_naifid", "?")).strip()
    elements = {k: keywords[k].strip() for k in ("element_epoch", "eccentricity", "inclination",
                                                 "argument_perihelion", "ascending_node", "perihelion_distance",
                                                 "perihelion_time", "semimajor_axis", "mean_anomaly",
                                                 "magnitude_parameters") if k in keywords}
    rows: list[MostRow] = []
    if "|" in text:
        try:
            table = asciitable.read(text, format="ipac", guess=False)
        except Exception as e:  # noqa: BLE001
            raise UpstreamError(f"could not read the MOST answer: {e}") from e
        for t in table:
            uri = str(t["uri"]).strip()
            band = str(t["productid"]).strip()
            if not uri or not band.startswith("D"):
                continue
            rows.append(MostRow(mjd_start=float(t["mjd_start"]), mjd_end=float(t["mjd_end"]),
                                ra=float(t["ra_obj"]), dec=float(t["dec_obj"]), band=band, uri=uri,
                                obs_id=str(t["observationid"]).strip(), vmag=_num(t["vmag"]),
                                sun_dist=_num(t["sun_dist"]), geo_dist=_num(t["geo_dist"]),
                                phase=_num(t["phase"]), dist_ctr=_num(t["dist_ctr"])))
    rows.sort(key=lambda r: (r.mjd_start, r.band))
    return MostResult(name, query, elements, rows)
