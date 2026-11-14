#!/usr/bin/env python3
"""Build the local object index used for instant search suggestions and the Random button.

Queries SIMBAD over TAP for objects worth looking at with SPHEREx (brown dwarfs, nearby and fast-moving
stars, variables, young stars, bright stars, Messier objects, galaxies, nebulae, clusters, and well-studied
named objects), keeps a few useful identifiers per object as search aliases, and writes
backend/spherex_core/targets/objects.json (one row per line, so diffs stay readable).

    python scripts/build_object_index.py
    python scripts/build_object_index.py --tap https://simbad.harvard.edu/simbad/sim-tap/sync

Positions are ICRS at J2000 with SIMBAD proper motions. Only the standard library is needed; the backend
package is imported to link rows to the challenge presets.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from spherex_core.targets.presets import PRESETS  # noqa: E402

OUT = ROOT / "backend" / "spherex_core" / "targets" / "objects.json"
TAP_URLS = ["https://simbad.cds.unistra.fr/simbad/sim-tap/sync", "https://simbad.harvard.edu/simbad/sim-tap/sync"]

COLUMNS = ("b.oid, b.main_id, b.ra, b.dec, b.pmra, b.pmdec, b.plx_value, b.otype, b.sp_type, "
           "b.galdim_majaxis, b.nbref, f.V, f.K")
FROM = "basic AS b LEFT OUTER JOIN allfluxes AS f ON b.oid = f.oidref"
FROM_IDENT = FROM + " JOIN ident AS i ON b.oid = i.oidref"

# (selection name, FROM clause, WHERE clause). Every selection adds candidates; categories are assigned
# afterwards from the object type and properties, not from the selection that found the object.
SELECTIONS = [
    ("brown dwarfs within 25 pc", FROM, "b.otype = 'BD*' AND b.plx_value > 40"),
    ("stars within 10 pc", FROM, "b.plx_value > 100"),
    ("proper motion over 1″/yr", FROM, "b.pmra * b.pmra + b.pmdec * b.pmdec > 1000000"),
    ("bright Miras", FROM, "b.otype = 'Mi*' AND f.K < 2"),
    ("bright Cepheids", FROM, "b.otype = 'cC*' AND f.V < 7"),
    ("bright RR Lyrae", FROM, "b.otype = 'RR*' AND f.V < 10"),
    ("studied R CrB, RV Tau, type II Cepheids", FROM, "b.otype IN ('RC*', 'RV*', 'WV*') AND b.nbref > 60"),
    ("bright long-period variables and carbon stars", FROM, "b.otype IN ('LP*', 'C*') AND f.K < 0.5"),
    ("young stars", FROM, "b.otype IN ('TT*', 'Or*', 'Ae*', 'Y*O') AND f.K < 8 AND b.nbref > 40"),
    ("bright stars", FROM, "f.V < 3.5"),
    ("large galaxies and nebulae", FROM, "b.galdim_majaxis > 4 AND b.nbref > 100"),
    ("planetary nebulae", FROM, "b.otype = 'PN' AND b.nbref > 120"),
    ("supernova remnants", FROM, "b.otype = 'SNR' AND b.nbref > 80"),
    ("studied nebulae and clouds",
     FROM, "b.otype IN ('HII', 'RNe', 'DNe', 'MoC', 'SFR', 'Cld', 'GNe', 'CGb', 'bub') AND b.nbref > 250"),
    ("studied open clusters", FROM, "b.otype = 'OpC' AND b.nbref > 250"),
    ("very well studied objects", FROM, "b.nbref > 1500"),
    ("Galactic globular clusters",
     FROM_IDENT, "b.otype = 'GlC' AND (i.id LIKE 'NGC %' OR i.id LIKE 'Pal %' OR i.id LIKE 'Terzan %')"),
    ("named objects", FROM_IDENT, "i.id LIKE 'NAME %' AND b.nbref > 300"),
    ("Messier objects", FROM_IDENT, "i.id LIKE 'M %'"),
]
MESSIER = re.compile(r"^M\s+\d+$")

# Identifiers kept as search aliases, in order of preference (at most MAX_ALIASES per object).
ALIAS_PREFIXES = ["NAME ", "M ", "NGC ", "IC ", "* ", "V* ", "GJ ", "Gl ", "HD ", "HIP ", "LHS ", "Wolf ", "Ross ",
                  "LAL ", "Gmb ", "LP ", "Luhman ", "TRAPPIST", "Teegarden", "Kapteyn", "Barnard", "Sh 2-", "Mel ",
                  "Cl ", "3C ", "Arp ", "Sgr ", "SN "]
# Survey designations: kept only for stars (that is how brown dwarfs are named), at most two surveys each,
# with the short form people type ("WISE J0855-0714"). The client also matches them without the "J".
SURVEY_PREFIXES = ["WISE ", "WISEA ", "WISEPC ", "WISEP ", "CWISE ", "2MASS J", "2MASSW J", "2MASSI J", "DENIS",
                   "SDSS J", "ULAS J", "PSO J"]
SURVEY_CATS = {"brown-dwarf", "high-pm", "nearby", "young", "star"}
SPOKEN = {"LAL ": "Lalande ", "Gmb ": "Groombridge "}  # SIMBAD abbreviations of classic catalogue names
# Names of nearby stars that people use instead of a variable-star designation (CN Leo is Wolf 359).
CLASSIC_NAMES = ("Wolf ", "Ross ", "Lalande ", "Groombridge ")
MAX_ALIASES = 8
IDENT_FILTER = " OR ".join(f"id LIKE '{p.strip()}%'" for p in ALIAS_PREFIXES + SURVEY_PREFIXES)
NOT_PLANET = "b.otype NOT IN ('Pl', 'Pl?')"  # exoplanets share their host's position and would duplicate it

GREEK = {"alf": ("α", "alpha"), "bet": ("β", "beta"), "gam": ("γ", "gamma"), "del": ("δ", "delta"),
         "eps": ("ε", "epsilon"), "zet": ("ζ", "zeta"), "eta": ("η", "eta"), "tet": ("θ", "theta"),
         "iot": ("ι", "iota"), "kap": ("κ", "kappa"), "lam": ("λ", "lambda"), "mu.": ("μ", "mu"), "nu.": ("ν", "nu"),
         "ksi": ("ξ", "xi"), "omi": ("ο", "omicron"), "pi.": ("π", "pi"), "rho": ("ρ", "rho"), "sig": ("σ", "sigma"),
         "tau": ("τ", "tau"), "ups": ("υ", "upsilon"), "phi": ("φ", "phi"), "chi": ("χ", "chi"), "psi": ("ψ", "psi"),
         "ome": ("ω", "omega")}

CONSTELLATIONS = {
    "And": "Andromedae", "Ant": "Antliae", "Aps": "Apodis", "Aqr": "Aquarii", "Aql": "Aquilae", "Ara": "Arae",
    "Ari": "Arietis", "Aur": "Aurigae", "Boo": "Bootis", "Cae": "Caeli", "Cam": "Camelopardalis", "Cnc": "Cancri",
    "CVn": "Canum Venaticorum", "CMa": "Canis Majoris", "CMi": "Canis Minoris", "Cap": "Capricorni",
    "Car": "Carinae", "Cas": "Cassiopeiae", "Cen": "Centauri", "Cep": "Cephei", "Cet": "Ceti",
    "Cha": "Chamaeleontis", "Cir": "Circini", "Col": "Columbae", "Com": "Comae Berenices",
    "CrA": "Coronae Australis", "CrB": "Coronae Borealis", "Crv": "Corvi", "Crt": "Crateris", "Cru": "Crucis",
    "Cyg": "Cygni", "Del": "Delphini", "Dor": "Doradus", "Dra": "Draconis", "Equ": "Equulei", "Eri": "Eridani",
    "For": "Fornacis", "Gem": "Geminorum", "Gru": "Gruis", "Her": "Herculis", "Hor": "Horologii", "Hya": "Hydrae",
    "Hyi": "Hydri", "Ind": "Indi", "Lac": "Lacertae", "Leo": "Leonis", "LMi": "Leonis Minoris", "Lep": "Leporis",
    "Lib": "Librae", "Lup": "Lupi", "Lyn": "Lyncis", "Lyr": "Lyrae", "Men": "Mensae", "Mic": "Microscopii",
    "Mon": "Monocerotis", "Mus": "Muscae", "Nor": "Normae", "Oct": "Octantis", "Oph": "Ophiuchi", "Ori": "Orionis",
    "Pav": "Pavonis", "Peg": "Pegasi", "Per": "Persei", "Phe": "Phoenicis", "Pic": "Pictoris", "Psc": "Piscium",
    "PsA": "Piscis Austrini", "Pup": "Puppis", "Pyx": "Pyxidis", "Ret": "Reticuli", "Sge": "Sagittae",
    "Sgr": "Sagittarii", "Sco": "Scorpii", "Scl": "Sculptoris", "Sct": "Scuti", "Ser": "Serpentis",
    "Sex": "Sextantis", "Tau": "Tauri", "Tel": "Telescopii", "Tri": "Trianguli", "TrA": "Trianguli Australis",
    "Tuc": "Tucanae", "UMa": "Ursae Majoris", "UMi": "Ursae Minoris", "Vel": "Velorum", "Vir": "Virginis",
    "Vol": "Volantis", "Vul": "Vulpeculae",
}

# Category of an object (first rule that matches wins). Shown in the UI and used by the Random button.
CATEGORIES = {
    "brown-dwarf": "Brown dwarf",
    "young": "Young star",
    "variable": "Variable star",
    "high-pm": "Fast-moving star",
    "nearby": "Nearby star",
    "bright": "Bright star",
    "galaxy": "Galaxy",
    "cluster": "Star cluster",
    "nebula": "Nebula",
    "star": "Star",
    "other": "Other",
}
YOUNG = {"TT*", "Or*", "Ae*", "Y*O"}
VARIABLE = {"Mi*", "LP*", "cC*", "Ce*", "RR*", "RC*", "RV*", "WV*", "C*"}
CLUSTER = {"GlC", "OpC", "Cl*", "Gl?", "Cl?"}
NEBULA = {"PN", "SNR", "SR?", "HII", "RNe", "DNe", "MoC", "SFR", "Cld", "GNe", "CGb", "bub", "glb", "ISM", "sh",
          "cor", "flt"}

FOV_OPTIONS = [6, 8, 10, 12, 16]


def tap_query(urls: list[str], adql: str, retries: int = 1) -> list[dict]:
    """Run a synchronous ADQL query; a service that fails is dropped from `urls` for the rest of the run."""
    body = urllib.parse.urlencode({"request": "doQuery", "lang": "adql", "format": "csv", "query": adql}).encode()
    last: Exception | None = None
    for url in list(urls):
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": "planet-x-spherex index builder"})
                with urllib.request.urlopen(req, timeout=300) as r:
                    text = r.read().decode("utf-8")
                if text.lstrip().startswith("<"):
                    raise RuntimeError(f"error page: {text[:120]!r}")
                return list(csv.DictReader(io.StringIO(text)))
            except Exception as e:  # noqa: BLE001 - retry, then try the next mirror
                last = e
                if isinstance(e, urllib.error.HTTPError) and e.code in (400, 404):
                    break
                time.sleep(2 * (attempt + 1))
        print(f"  {url} failed ({last}); not using it again", file=sys.stderr, flush=True)
        urls.remove(url)
    raise RuntimeError(f"all TAP services failed: {last}")


def fnum(v: str | None) -> float | None:
    try:
        x = float(v) if v not in (None, "") else None
    except ValueError:
        return None
    return x if x is not None and math.isfinite(x) else None


def clean_id(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def short_designation(ident: str) -> list[str]:
    """'WISE J085510.83-071442.5' -> ['WISE J0855-0714', 'WISE 0855-0714'] (the short forms people type)."""
    m = re.match(r"^(WISEA|WISEPC|WISEP|WISE|CWISE|2MASSW|2MASSI|2MASS|SDSS|DENIS-P|DENIS|ULAS|PSO) ?J"
                 r"(\d{4})\d*(?:\.\d+)?([+-]\d{4})", ident)
    if not m:
        return []
    p = m.group(1)
    return [f"{p} J{m.group(2)}{m.group(3)}", f"{p} {m.group(2)}{m.group(3)}"]


def star_forms(ident: str) -> tuple[str, list[str]]:
    """Display form and spoken aliases of '* alf Lyr', '* 61 Cyg A', 'V* R Dra'."""
    body = re.sub(r"^(\*|V\*)\s+", "", ident)
    parts = body.split(" ")
    aliases = [body]
    display = body
    if len(parts) >= 2:
        first, con, rest = parts[0], parts[1], parts[2:]
        m = re.match(r"^([a-z]{2}\.?|[a-z]{3})(\d*)$", first)
        greek = GREEK.get(m.group(1)) if m else None
        if greek is None and m and m.group(1) + "." in GREEK:
            greek = GREEK[m.group(1) + "."]
        num = m.group(2).lstrip("0") if m else ""
        genitive = CONSTELLATIONS.get(con)
        tail = (" " + " ".join(rest)) if rest else ""
        if greek:
            display = f"{greek[0]}{num} {con}{tail}"
            if genitive:
                aliases.append(f"{greek[1]}{num} {genitive}{tail}")
            aliases.append(f"{greek[1]}{num} {con}{tail}")
        elif genitive:
            aliases.append(f"{first} {genitive}{tail}")
    return display, aliases


ABBREVIATED = set(CONSTELLATIONS) - {"Sgr"} | {"Gal", "Neb", "NEB", "SN", "PSR", "MCLD", "GMC", "Cl", "Gr"}
TYPE_WORDS = {"nebula", "galaxy", "cluster", "cloud", "center", "centre", "pulsar", "remnant", "dwarf",
              "nova", "supernova", "association", "complex", "region", "arc", "loop", "bubble", "veil", "pillars"}


def name_rank(name: str) -> tuple:
    """Sort key for choosing among SIMBAD's NAME identifiers: spelled out beats abbreviated ('Orion Nebula'
    over 'Ori Nebula', 'Centaurus A' over 'Cen-A'), a type word beats none ('Crab Nebula' over 'Crab'; for
    stars only the possessive "Barnard's Star"), short beats long ('Orion Nebula' over 'Great Orion Nebula',
    'Polaris' over 'North Star'), and a component letter loses ('Sirius' over 'Sirius A')."""
    words = name.split()
    parts = [w.strip(",.") for w in re.split(r"[\s-]+", name)]
    abbreviated = any(w in ABBREVIATED for w in parts) or (name.isupper() and len(name) > 4)
    component = len(words) > 1 and len(words[-1]) == 1 and words[-1].isupper()
    typed = any(w.lower() in TYPE_WORDS for w in parts) or bool(re.search(r"'s [Ss]tar$", name))
    return (abbreviated, component, not typed, len(name.replace("'s", "").replace("'", "")), -len(name), name)


def pick_fov(size_arcmin: float | None, extended: bool) -> int | None:
    if not extended or not size_arcmin:
        return None
    want = size_arcmin * 1.2
    for f in FOV_OPTIONS:
        if f >= want:
            return f if f > 6 else None
    return FOV_OPTIONS[-1]


def category(otype: str, path: str, pm: float, plx: float | None, vmag: float | None) -> str:
    if otype == "BD*":
        return "brown-dwarf"
    if otype in YOUNG:
        return "young"
    if otype in VARIABLE:
        return "variable"
    is_star = path.startswith("*")
    if is_star and pm > 1000:
        return "high-pm"
    if is_star and plx is not None and plx > 100:
        return "nearby"
    if is_star and vmag is not None and vmag < 3.5:
        return "bright"
    if path.startswith("G"):
        return "galaxy"
    if otype in CLUSTER or path.startswith("Cl*"):
        return "cluster"
    if otype in NEBULA or path.startswith("ISM"):
        return "nebula"
    return "star" if is_star else "other"


def fetch(urls: list[str]) -> dict:
    """Everything the index is built from: object types, candidate objects, their identifiers, presets."""
    types = {r["otype"]: (r["otype_longname"], r["path"])
             for r in tap_query(urls, "SELECT otype, otype_longname, path FROM otypedef")}
    selections = list(SELECTIONS)
    # Each challenge preset: the best-studied object within 5″ of its J2000 position.
    for t in PRESETS.values():
        ra, dec = (float(x) for x in t.position_at(51544.5))
        selections.append((f"preset {t.id}", FROM, f"CONTAINS(POINT('ICRS', b.ra, b.dec), "
                           f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, 0.0014)) = 1"))
    objects: dict[str, dict] = {}
    presets: dict[str, str] = {}
    for label, frm, where in selections:
        extra = ", i.id" if "ident" in frm else ""
        rows = tap_query(urls, f"SELECT {COLUMNS}{extra} FROM {frm} WHERE ({where}) AND {NOT_PLANET}")
        if label == "Messier objects":
            rows = [r for r in rows if MESSIER.match(clean_id(r["id"]))]
        if label.startswith("preset ") and rows:
            best = max(rows, key=lambda r: int(r["nbref"] or 0))
            presets[label[7:]] = best["oid"]
            rows = [best]
        new = sum(1 for r in rows if r["oid"] not in objects)
        for r in rows:
            if fnum(r["ra"]) is not None and fnum(r["dec"]) is not None:
                objects.setdefault(r["oid"], r)
        print(f"{label:48s} {len(rows):5d} rows, {new:5d} new, {len(objects):5d} total", flush=True)
    idents: dict[str, list[str]] = {}
    oids = list(objects)
    for k in range(0, len(oids), 400):
        chunk = ",".join(oids[k:k + 400])
        for r in tap_query(urls, f"SELECT oidref, id FROM ident WHERE oidref IN ({chunk}) AND ({IDENT_FILTER})"):
            idents.setdefault(r["oidref"], []).append(clean_id(r["id"]))
    print(f"identifiers for {len(idents)} objects", flush=True)
    return {"types": types, "objects": objects, "idents": idents, "presets": presets}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tap", action="append", help="SIMBAD TAP sync URL (repeat for fallbacks)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--cache", type=Path, help="keep the raw SIMBAD answers in this file and reuse them")
    args = ap.parse_args()
    urls = list(args.tap or TAP_URLS)

    raw = fetch(urls) if args.cache is None or not args.cache.exists() else json.loads(args.cache.read_text())
    if args.cache is not None and not args.cache.exists():
        args.cache.write_text(json.dumps(raw))
    types = {k: tuple(v) for k, v in raw["types"].items()}
    objects, idents = raw["objects"], raw["idents"]
    preset_oids: dict[str, str] = raw["presets"]

    rows_out = []
    used_types: dict[str, str] = {}
    for oid, r in objects.items():
        otype = r["otype"].strip()
        longname, path = types.get(otype, (otype, ""))
        ra, dec = fnum(r["ra"]), fnum(r["dec"])
        pmra, pmdec = fnum(r["pmra"]) or 0.0, fnum(r["pmdec"]) or 0.0
        plx = fnum(r["plx_value"])
        vmag, kmag = fnum(r["V"]), fnum(r["K"])
        size = fnum(r["galdim_majaxis"])
        cat = category(otype, path, math.hypot(pmra, pmdec), plx, vmag)
        extended = cat in ("galaxy", "cluster", "nebula")

        main_id = clean_id(r["main_id"])
        ids = sorted(set(idents.get(oid, [])) | {main_id}, key=lambda x: (len(x), x))
        ids += [SPOKEN[p] + i[len(p):] for i in ids for p in SPOKEN if i.startswith(p)]
        names = sorted((i[5:] for i in ids if i.startswith("NAME ")), key=name_rank)
        messier = next((i for i in ids if MESSIER.match(i)), None)
        star_id = next((i for i in ([main_id] + ids) if re.match(r"^(\*|V\*) ", i)), None)
        star_display, spoken = star_forms(star_id) if star_id else (None, [])
        classic = next((i for i in ids if i.startswith(CLASSIC_NAMES)), None)

        # Display name: a good common name, else the designation people use, else SIMBAD's main identifier.
        good = [n for n in names if not name_rank(n)[0]]
        main_clean = None if main_id.startswith(("NAME ", "* ", "V* ")) else main_id
        bayer = star_display if star_id and star_id.startswith("* ") else None
        display = (good[0] if good else None) or bayer \
            or (classic if cat in SURVEY_CATS | {"variable", "bright"} else None) \
            or star_display or messier or main_clean or (names[0] if names else None) \
            or re.sub(r"^(NAME|\*|V\*)\s+", "", main_id)

        aliases: list[str] = [n for n in names if n != display]
        if star_display:
            aliases += [star_display] + spoken
        for prefix in ALIAS_PREFIXES:
            aliases += [i for i in ids if i.startswith(prefix) and not i.startswith(("NAME ", "* ", "V* "))]
        aliases += [i for i in ids if i.startswith(tuple(SPOKEN.values()))]
        if cat in SURVEY_CATS:
            families = 0
            for prefix in SURVEY_PREFIXES:
                hit = next((i for i in ids if i.startswith(prefix)), None)
                if hit and families < 2:
                    families += 1
                    aliases += [hit] + short_designation(hit)[:1]
        if not re.match(r"^(NAME|\*|V\*|WISE|2MASS|SDSS|CWISE|DENIS|ULAS|PSO) ?", main_id):
            aliases.append(main_id)
        seen = {display.lower()}
        uniq = []
        for a in aliases:
            if a.lower() not in seen:
                seen.add(a.lower())
                uniq.append(a)
        aliases = uniq[:MAX_ALIASES]

        used_types[otype] = longname
        preset = next((pid for pid, poid in preset_oids.items() if poid == oid), None)
        rows_out.append([
            display, aliases, otype, cat, round(ra, 5), round(dec, 5), round(pmra, 1), round(pmdec, 1),
            round(plx, 2) if plx is not None else None, round(vmag, 2) if vmag is not None else None,
            round(kmag, 2) if kmag is not None else None, (r["sp_type"] or "").strip() or None,
            round(size, 1) if size and extended else None, pick_fov(size, extended), int(r["nbref"] or 0), preset,
        ])
    rows_out.sort(key=lambda x: (-x[14], x[0]))

    header = {
        "version": 1,
        "generated": date.today().isoformat(),
        "source": "SIMBAD astronomical database (CDS, Strasbourg) via TAP; built by scripts/build_object_index.py",
        "fields": ["name", "aliases", "otype", "cat", "ra", "dec", "pmra", "pmdec", "plx", "vmag", "kmag", "sp",
                   "size", "fov", "refs", "preset"],
        "categories": CATEGORIES,
        "types": dict(sorted(used_types.items())),
    }
    lines = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows_out]
    text = json.dumps(header, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"rows":[\n' + ",\n".join(lines) + "\n]}\n"
    json.loads(text)  # sanity check
    args.out.write_text(text, encoding="utf-8")
    counts: dict[str, int] = {}
    for row in rows_out:
        counts[row[3]] = counts.get(row[3], 0) + 1
    print(f"wrote {len(rows_out)} objects ({args.out.stat().st_size / 1e3:.0f} kB) to {args.out}")
    print("  " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
    print("  linked presets: " + ", ".join(sorted(x[15] for x in rows_out if x[15])))
    missing = sorted(set(PRESETS) - {x[15] for x in rows_out})
    if missing:
        print("  presets without a SIMBAD match: " + ", ".join(missing))


if __name__ == "__main__":
    main()
