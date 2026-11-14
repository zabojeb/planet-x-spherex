"""Local object index, name resolution order and the Sesame cache, offline (Sesame is stubbed)."""

import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import services
from spherex_core.targets.model import Target
from spherex_core.targets.objects import ObjectIndex, compact, index, match_keys, normalize

resolve_mod = importlib.import_module("spherex_core.targets.resolve")  # the package re-exports a resolve() function

SESAME_XML = b"""<?xml version="1.0" encoding="UTF-8"?><Sesame><Target><name>HD 12345</name>
<Resolver name="S=Simbad (via url)"><oname>HD  12345</oname><otype>*</otype><jradeg>30.0</jradeg>
<jdedeg>-10.5</jdedeg><pm><pmRA>12.5</pmRA><pmDE>-3.0</pmDE></pm><plx><v>20.0</v></plx><spType>G5V</spType>
</Resolver></Target></Sesame>"""
SESAME_MISS = b"""<?xml version="1.0" encoding="UTF-8"?><Sesame><Target><name>nope</name>
<INFO>*** Nothing found ***</INFO></Target></Sesame>"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    services.configure(cache_root=tmp_path)
    resolve_mod._misses.clear()
    yield
    services.configure(None)


@pytest.mark.parametrize("text,key", [
    ("Barnard’s Star", "barnards star"),
    ("ε Indi B", "epsilon indi b"),
    ("Gliese 229 B", "gj 229 b"),
    ("Přybylski", "prybylski"),
    ("WISE 0855−0714", "wise 0855 0714"),
])
def test_normalize(text, key):
    assert normalize(text) == key


def test_match_keys_unify_spacing_synonyms_and_survey_j():
    assert compact("GJ 699") == compact("gj699") == compact("Gliese 699") == "gj699"
    assert match_keys("WISE J0855-0714") == ["wisej08550714", "wise08550714"]


def test_index_is_built_and_linked_to_presets():
    idx = index()
    assert len(idx) > 3000
    linked = {idx.record(i)["preset"] for i in range(len(idx))} - {None}
    assert linked == {"barnard", "eps-indi-b", "wise-0855", "rw-aur", "r-dra"}
    cats = {idx.record(i)["cat"] for i in range(len(idx))}
    assert {"brown-dwarf", "high-pm", "variable", "galaxy", "nebula", "cluster"} <= cats


@pytest.mark.parametrize("query,name", [
    ("Vega", "Vega"), ("alpha Lyrae", "Vega"), ("α Lyr", "Vega"), ("m 31", "Andromeda Galaxy"),
    ("M31", "Andromeda Galaxy"), ("Orion Nebula", "Orion Nebula"), ("Wolf 359", "Wolf 359"),
])
def test_lookup_names_and_aliases(query, name):
    t = index().lookup(query)
    assert t is not None and t.name == name and t.source == "SIMBAD (local index)"


def test_lookup_of_a_preset_alias_returns_the_preset_with_its_hints():
    t = index().lookup("GJ 699")
    assert t.id == "barnard" and t.default_mode == "diff"
    assert index().lookup("wise 0855-0714").id == "wise-0855"
    assert index().lookup("not a real object") is None and index().lookup("  ") is None


def test_extended_objects_suggest_a_field_of_view():
    assert index().lookup("M 31").fov_arcmin == 16
    assert index().lookup("Vega").fov_arcmin is None


def test_missing_index_file_degrades_to_empty(tmp_path):
    idx = ObjectIndex.load(tmp_path / "missing.json")
    assert len(idx) == 0 and idx.lookup("Vega") is None and json.loads(idx.raw)["rows"] == []


def _no_network(*_a, **_k):
    raise AssertionError("Sesame must not be called")


def test_resolve_prefers_presets_and_the_index_over_sesame(monkeypatch):
    monkeypatch.setattr(resolve_mod, "query_sesame", _no_network)
    assert resolve_mod.resolve_name("barnard").id == "barnard"
    assert resolve_mod.resolve_name("HD 209100").name == "ε Ind"


def test_sesame_answer_is_parsed_and_cached_on_disk(monkeypatch):
    calls = []

    def ask(base, name, timeout):
        calls.append(base)
        return resolve_mod._parse_sesame(SESAME_XML, name)

    monkeypatch.setattr(resolve_mod, "sesame_urls", lambda: ["https://one.example/sesame"])
    monkeypatch.setattr(resolve_mod, "_ask", ask)
    t = resolve_mod.resolve_name("HD 12345")
    assert t.name == "HD 12345"  # SIMBAD pads catalogue numbers: 'HD  12345'
    assert (t.ra, t.dec, t.pmra, t.pmdec, t.parallax) == (30.0, -10.5, 12.5, -3.0, 20.0)
    assert t.description == "* · G5V" and t.source.startswith("Sesame (S=Simbad")
    monkeypatch.setattr(resolve_mod, "_ask", _no_network)
    assert resolve_mod.resolve_name("hd12345") == t  # same key, from the disk cache
    assert len(calls) == 1


def test_unknown_names_are_remembered_for_a_while(monkeypatch):
    calls = []

    def ask(base, name, timeout):
        calls.append(name)
        return resolve_mod._parse_sesame(SESAME_MISS, name)

    monkeypatch.setattr(resolve_mod, "sesame_urls", lambda: ["https://one.example/sesame"])
    monkeypatch.setattr(resolve_mod, "_ask", ask)
    for _ in range(2):
        with pytest.raises(LookupError, match="Unknown object"):
            resolve_mod.resolve_name("nope nope")
    assert calls == ["nope nope"]


def test_mirrors_are_raced_and_failures_fall_through(monkeypatch):
    monkeypatch.setattr(resolve_mod, "sesame_urls", lambda: ["https://down.example", "https://up.example"])

    def ask(base, name, timeout):
        if "down" in base:
            raise RuntimeError("down.example: timed out")
        return Target(ra=1.0, dec=2.0, name=name)

    monkeypatch.setattr(resolve_mod, "_ask", ask)
    assert resolve_mod.query_sesame("x").ra == 1.0

    def all_down(base, name, timeout):
        raise RuntimeError(f"{base}: timed out")

    monkeypatch.setattr(resolve_mod, "_ask", all_down)
    with pytest.raises(LookupError, match="Name resolver unavailable"):
        resolve_mod.query_sesame("x")


def test_sesame_urls_accept_a_list_and_add_the_mirror(monkeypatch):
    monkeypatch.setattr(resolve_mod, "settings", SimpleNamespace(sesame_url="https://a.example/s, https://b.example/s"))
    urls = resolve_mod.sesame_urls()
    assert urls[:2] == ["https://a.example/s", "https://b.example/s"] and resolve_mod.SESAME_MIRRORS[0] in urls


def test_objects_endpoint_serves_the_index_with_an_etag():
    c = TestClient(create_app(web_dist=None))
    r = c.get("/api/objects")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=3600"
    doc = r.json()
    assert doc["fields"][:2] == ["name", "aliases"] and len(doc["rows"]) > 3000
    r2 = c.get("/api/objects", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304 and r2.content == b""


def test_resolve_endpoint_uses_the_index(monkeypatch):
    monkeypatch.setattr(resolve_mod, "query_sesame", _no_network)
    c = TestClient(create_app(web_dist=None))
    r = c.get("/api/resolve", params={"q": "M 42"})
    assert r.status_code == 200 and r.json()["name"] == "Orion Nebula" and r.json()["fov_arcmin"] == 16
