"""The local object index: a few thousand objects worth looking at, with search aliases.

objects.json is built from SIMBAD by scripts/build_object_index.py and committed. The web client downloads
it once (GET /api/objects) and ranks suggestions locally while the user types, so typing never waits on the
network. The backend uses it to resolve an exact name or alias without asking CDS Sesame.

Row layout is given by the file's "fields" list; positions are ICRS at J2000 with SIMBAD proper motions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .model import Target
from .presets import PRESETS

INDEX_FILE = Path(__file__).with_name("objects.json")
log = logging.getLogger(__name__)

GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta",
         "θ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "omicron",
         "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau", "υ": "upsilon", "φ": "phi", "χ": "chi",
         "ψ": "psi", "ω": "omega"}


SYNONYMS = {"gliese": "gj", "gl": "gj"}  # GJ 699 = Gliese 699 = Gl 699
# Survey designations are typed with or without the "J": 'WISE J0855-0714' and 'WISE 0855-0714'.
SURVEY_J = re.compile(r"^(wisea|wisepc|wisep|wise|cwise|2massw|2massi|2mass|sdss|denisp|denis|ulas|pso)j(?=\d)")


def normalize(text: str) -> str:
    """Search key shared with the web client (web/src/search.ts): lower case, accents dropped, Greek letters
    spelled out, apostrophes removed, anything else that is not a letter or digit becomes a space, and a
    few catalogue synonyms unified."""
    s = unicodedata.normalize("NFKD", text.lower())
    s = "".join(GREEK.get(ch, ch) for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"['’`]", "", s)
    return " ".join(SYNONYMS.get(w, w) for w in re.sub(r"[^a-z0-9]+", " ", s).split())


def compact(text: str) -> str:
    """normalize() without spaces: 'GJ 699', 'gj699' and 'Gliese 699' share one key."""
    return normalize(text).replace(" ", "")


def match_keys(text: str) -> list[str]:
    """Exact-match keys of a name: its compact form, plus the form without a survey 'J'."""
    c = compact(text)
    alt = SURVEY_J.sub(r"\1", c)
    return [c] if alt == c else [c, alt]


@dataclass(frozen=True)
class ObjectIndex:
    raw: bytes  # the file as served to the client
    etag: str
    fields: list[str]
    rows: list[list]
    types: dict[str, str]
    categories: dict[str, str]
    by_key: dict[str, int] = field(default_factory=dict)  # match_keys() of names and aliases -> row

    @classmethod
    def load(cls, path: Path = INDEX_FILE) -> ObjectIndex:
        try:
            raw = path.read_bytes()
            doc = json.loads(raw)
        except (OSError, ValueError) as e:
            log.warning("object index unavailable (%s): search falls back to name resolution only", e)
            raw, doc = b'{"version":1,"fields":[],"rows":[],"types":{},"categories":{}}', {}
        fields = doc.get("fields", [])
        rows = doc.get("rows", [])
        lookup: dict[str, int] = {}
        if rows:
            name_at, alias_at = fields.index("name"), fields.index("aliases")
            # Primary names win over aliases; rows are ordered by how well studied they are.
            for i, row in enumerate(rows):
                for k in match_keys(row[name_at]):
                    lookup.setdefault(k, i)
            for i, row in enumerate(rows):
                for a in row[alias_at]:
                    for k in match_keys(a):
                        lookup.setdefault(k, i)
        etag = '"' + hashlib.sha1(raw).hexdigest()[:20] + '"'
        return cls(raw=raw, etag=etag, fields=fields, rows=rows, types=doc.get("types", {}),
                   categories=doc.get("categories", {}), by_key=lookup)

    def __len__(self) -> int:
        return len(self.rows)

    def record(self, i: int) -> dict:
        return dict(zip(self.fields, self.rows[i]))

    def lookup(self, query: str) -> Target | None:
        """Target for an exact name or alias (case, spacing and punctuation ignored), else None."""
        i = self.by_key.get(compact(query))
        return None if i is None or not query.strip() else self.target(i)

    def target(self, i: int) -> Target:
        r = self.record(i)
        if r.get("preset") in PRESETS:
            return PRESETS[r["preset"]]
        return Target(
            ra=r["ra"], dec=r["dec"], name=r["name"], pmra=r["pmra"] or 0.0, pmdec=r["pmdec"] or 0.0,
            ref_epoch=2000.0, parallax=r.get("plx"), source="SIMBAD (local index)",
            description=describe(r, self.types), fov_arcmin=r.get("fov"),
        )


def describe(r: dict, types: dict[str, str]) -> str:
    """'Brown Dwarf · T6 · 3.6 pc · 4.70″/yr': type, spectral type, distance, motion or size."""
    parts = [types.get(r["otype"], r["otype"])]
    if r.get("sp"):
        parts.append(r["sp"])
    plx = r.get("plx")
    if plx and plx > 1:
        parts.append(f"{1000 / plx:.1f} pc" if plx >= 10 else f"{1000 / plx:.0f} pc")
    pm = math.hypot(r.get("pmra") or 0.0, r.get("pmdec") or 0.0)
    if pm >= 100:
        parts.append(f"{pm / 1000:.2f}″/yr")
    if r.get("size"):
        parts.append(f"{r['size']:g}′")
    return " · ".join(parts)


@lru_cache(maxsize=1)
def index() -> ObjectIndex:
    return ObjectIndex.load()
