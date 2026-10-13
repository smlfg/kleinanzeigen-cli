"""Tests für kleinanzeigen-cli.

* URL-Bau (search.py): deterministisch.
* HTML-Parser (parser.py): gegen eine kleine, synthetische HTML-Fixture
  mit 6 Karten in 5 Städten, gemischten Versendern / Versand-Flags.
* Client-Filter im `search`-Befehl: filtern nach Preis, Versand,
  Versender-Typ.
* Detail-Parser (parser.parse_detail): gegen eine synthetische Detailseite.

Die Tests sind netz-frei: ein FakeHttpClient in conftest.py
liefert die Fixture-HTMLs aus tests/fixtures/synthetic.py aus.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kleinanzeigen_cli import parser, search as searchmod  # noqa: E402
from kleinanzeigen_cli.cli import _client_filter  # noqa: E402
from kleinanzeigen_cli.http_client import HTTPError  # noqa: E402

# `tests.fixtures.synthetic` als Modul laden, ohne ein tests/-Package
# vorauszusetzen (pip install . kennt `src/`, aber nicht `tests/`).
_FIX_PATH = ROOT / "tests" / "fixtures" / "synthetic.py"
_spec = importlib.util.spec_from_file_location("_kleinanzeigen_synthetic", _FIX_PATH)
_synthetic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_synthetic)
SYNTHETIC_SEARCH = _synthetic.SYNTHETIC_SEARCH
SYNTHETIC_DETAIL = _synthetic.SYNTHETIC_DETAIL


# ---------- URL-Bau ----------


def test_build_url_basic():
    u = searchmod.build_search_url(keywords="iPhone 12")
    assert u.startswith("https://www.kleinanzeigen.de/s-iphone-12/k0?keywords=")
    assert "keywords=iPhone%2012" in u


def test_build_url_category_and_location():
    u = searchmod.build_search_url(
        category="handy-telefon",
        location=3331,
        radius=50,
        min_price=20,
        max_price=500,
    )
    assert "/s-handy-telefon/k0c173l3331r50" in u
    assert "minPrice=20" in u
    assert "maxPrice=500" in u


def test_build_url_filters():
    u = searchmod.build_search_url(
        category="c173",
        offer_type="GESUCH",
        sender_type="PRIVAT",
        shipping=True,
        sorting="PRICE_ASCENDING",
        page=2,
    )
    assert "offerType=GESUCH" in u
    assert "senderType=PRIVAT" in u
    assert "shipping=true" in u
    assert "sorting=PRICE_ASCENDING" in u
    assert "page=2" in u


def test_build_url_rejects_empty():
    with pytest.raises(ValueError):
        searchmod.build_search_url()


def test_build_url_does_not_include_unset():
    u = searchmod.build_search_url(keywords="iPhone", min_price=100)
    for k in ("maxPrice", "offerType", "senderType", "shipping", "sorting", "page"):
        assert k not in u


def test_slugify_handles_umlauts():
    assert searchmod.slugify("Möbel & Wohnen") == "moebel-wohnen"
    assert searchmod.slugify("") == "alles"


def test_category_id_roundtrip():
    assert searchmod.category_id("handy-telefon") == "c173"
    assert searchmod.category_id("c173") == "c173"
    assert searchmod.category_id("unknown") is None


def test_parse_adid():
    assert searchmod.parse_adid_from_url("12345") == "12345"
    assert searchmod.parse_adid_from_url("/s-anzeige/foo-bar/12345-x-1.html") == "12345"
    assert searchmod.parse_adid_from_url(
        "https://www.kleinanzeigen.de/s-anzeige/iphone-12/2001-x-1"
    ) == "2001"
    assert searchmod.parse_adid_from_url("") is None


# ---------- Parser ----------


@pytest.fixture
def synthetic_offers():
    return parser.parse_search(SYNTHETIC_SEARCH, limit=20)


def test_parse_search_basic(synthetic_offers):
    ids = [o["id"] for o in synthetic_offers]
    assert ids == ["2001", "2002", "2003", "2004", "2005", "2006"]


def test_parse_search_prices(synthetic_offers):
    prices = [o["preis_euro"] for o in synthetic_offers]
    assert prices == [200, 420, 80, 380, 320, None]
    # Karte 2006 hat VB → None (kein Match auf _PRICE_RE)


def test_parse_search_locations(synthetic_offers):
    paare = [(o["plz"], o["ort"]) for o in synthetic_offers]
    assert paare == [
        ("10115", "Berlin Mitte"),
        ("10405", "Berlin Prenzlauer Berg"),
        ("20095", "Hamburg Altstadt"),
        ("50667", "Köln Innenstadt"),
        ("80331", "München Zentrum"),
        ("70173", "Stuttgart"),
    ]


def test_parse_search_sender_types(synthetic_offers):
    types = [o["versender_typ"] for o in synthetic_offers]
    assert types == ["privat", "gewerblich", "privat", "privat", "gewerblich", "privat"]


def test_parse_search_shipping(synthetic_offers):
    versand = [o["versand_moeglich"] for o in synthetic_offers]
    assert versand == [True, True, False, True, True, False]


def test_parse_search_titles(synthetic_offers):
    titles = [o["titel"] for o in synthetic_offers]
    assert titles[0] == "iPhone 12 64GB schwarz"
    assert titles[2] == "iPhone 11 defekt zum Basteln"


def test_parse_search_raises_when_empty():
    empty = "<html><body><ul></ul></body></html>"
    from kleinanzeigen_cli.http_client import ParseError
    with pytest.raises(ParseError):
        parser.parse_search(empty, limit=10)


def test_parse_detail_basic():
    d = parser.parse_detail(SYNTHETIC_DETAIL, adid="2001")
    assert d["id"] == "2001"
    assert d["titel"] == "iPhone 12 64GB schwarz"
    assert d["preis_euro"] == 200
    assert d["plz"] == "10115"
    assert d["ort"] == "Berlin Mitte"
    assert d["versand_moeglich"] is True
    assert d["versender_typ"] == "privat"
    assert "Top gepflegt" in d["beschreibung_text"]
    assert d["url"].startswith("https://www.kleinanzeigen.de/")


# ---------- Client-Filter ----------


def _ns(**kwargs):
    defaults = {
        "min_price": None,
        "max_price": None,
        "only_shipping": False,
        "only_private": False,
        "only_commercial": False,
        "offer_type": None,
        "limit": None,
    }
    defaults.update(kwargs)
    return type("NS", (), defaults)()


def test_client_filter_min_price(synthetic_offers):
    out = _client_filter(_ns(min_price=300), synthetic_offers)
    # 2002 (420), 2004 (380), 2005 (320); 2006 ist None → bleibt toleriert
    assert [o["id"] for o in out] == ["2002", "2004", "2005", "2006"]


def test_client_filter_max_price(synthetic_offers):
    out = _client_filter(_ns(max_price=200), synthetic_offers)
    # ≤200: 2001 (200), 2003 (80), 2006 (None → bleibt)
    assert [o["id"] for o in out] == ["2001", "2003", "2006"]


def test_client_filter_only_shipping(synthetic_offers):
    out = _client_filter(_ns(only_shipping=True), synthetic_offers)
    assert [o["id"] for o in out] == ["2001", "2002", "2004", "2005"]


def test_client_filter_only_private(synthetic_offers):
    out = _client_filter(_ns(only_private=True), synthetic_offers)
    assert [o["id"] for o in out] == ["2001", "2003", "2004", "2006"]


def test_client_filter_only_commercial(synthetic_offers):
    out = _client_filter(_ns(only_commercial=True), synthetic_offers)
    assert [o["id"] for o in out] == ["2002", "2005"]


def test_client_filter_limit(synthetic_offers):
    out = _client_filter(_ns(limit=2), synthetic_offers)
    assert len(out) == 2


# ---------- CLI Subprozess ----------


def _run_cli(*args, env_extra=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["ACCEPTANCE_SANDBOX"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "kleinanzeigen_cli.cli", *args],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=30,
    )


def test_cli_help_lists_filters():
    r = _run_cli("search", "--help")
    assert r.returncode == 0
    for opt in (
        "--query", "--category", "--location", "--radius",
        "--min-price", "--max-price",
        "--offer-type", "--sender-type",
        "--only-shipping", "--only-private", "--only-commercial",
        "--sorting", "--page", "--limit", "--json",
    ):
        assert opt in r.stdout, f"missing {opt} in search --help"


def test_cli_help_watch():
    r = _run_cli("watch", "--help")
    assert r.returncode == 0
    for opt in ("--name", "--query", "--category", "--limit"):
        assert opt in r.stdout


def test_cli_categories_emits_json():
    r = _run_cli("categories", "--json")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["quelle"].endswith("kleinanzeigen.de")
    assert payload["anzahl"] >= 10
    handy = next(c for c in payload["kategorien"] if c["slug"] == "handy-telefon")
    assert handy["id"] == "c173"


def test_cli_show_rejects_garbage():
    r = _run_cli("show", "not-an-id", "--json")
    assert r.returncode == 1
    assert "Anzeigen-ID" in r.stderr or "Konnte keine" in r.stderr