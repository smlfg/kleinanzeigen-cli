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


def test_build_url_only_private_maps_sender_type():
    u = searchmod.build_search_url(keywords="iPhone 12", sender_type="PRIVAT")
    assert "senderType=PRIVAT" in u


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
    assert ids == ["2001", "2002", "2003", "2004", "2005", "2006",
                   "2010", "2011", "2012", "2013", "2014", "2015"]


def test_parse_search_prices(synthetic_offers):
    prices = [o["preis_euro"] for o in synthetic_offers]
    assert prices == [200, 420, 80, 380, 320, None, None, 699, None, 1299, None, None]
    # Karten mit None: 2006/2010/2012/2014 (VB/Verschenken/Tausch),
    # 2015 (kein Preisfeld).


def test_parse_search_price_types(synthetic_offers):
    """preis_typ wird für jede Karte korrekt aus dem Preisfeld abgeleitet."""
    types = [o["preis_typ"] for o in synthetic_offers]
    # 2015 hat kein Preisfeld → preis_typ ist None
    assert types == [
        "fest", "fest", "fest", "fest", "fest", "vb",
        "vb", "vb", "verschenken", "fest", "tausch", None,
    ]


def test_parse_search_accept_akzeptanz(synthetic_offers):
    """Akzeptanztest: alle bekannten Live-Preis-Varianten werden
    korrekt erkannt (fest/VB/Verschenken/Tausch/Tausenderpunkt)."""
    by_id = {o["id"]: o for o in synthetic_offers}
    # Tausenderpunkt
    assert by_id["2013"]["preis_euro"] == 1299
    assert by_id["2013"]["preis_typ"] == "fest"
    # VB-only
    assert by_id["2010"]["preis_euro"] is None
    assert by_id["2010"]["preis_typ"] == "vb"
    # VB mit Zahl
    assert by_id["2011"]["preis_euro"] == 699
    assert by_id["2011"]["preis_typ"] == "vb"
    # Zu verschenken
    assert by_id["2012"]["preis_euro"] is None
    assert by_id["2012"]["preis_typ"] == "verschenken"
    # Tausch
    assert by_id["2014"]["preis_euro"] is None
    assert by_id["2014"]["preis_typ"] == "tausch"
    # Karte ohne Preisfeld → beides None (nicht irreführend "fest")
    assert by_id["2015"]["preis_euro"] is None
    assert by_id["2015"]["preis_typ"] is None


def test_parse_search_locations(synthetic_offers):
    paare = [(o["plz"], o["ort"]) for o in synthetic_offers]
    assert paare == [
        ("10115", "Berlin Mitte"),
        ("10405", "Berlin Prenzlauer Berg"),
        ("20095", "Hamburg Altstadt"),
        ("50667", "Köln Innenstadt"),
        ("80331", "München Zentrum"),
        ("70173", "Stuttgart"),
        ("50667", "Köln Innenstadt"),
        ("80331", "München Zentrum"),
        ("20095", "Hamburg Altstadt"),
        ("10115", "Berlin Mitte"),
        ("10405", "Berlin Prenzlauer Berg"),
        ("80331", "München Zentrum"),
    ]


def test_parse_search_sender_types(synthetic_offers):
    types = [o["versender_typ"] for o in synthetic_offers]
    assert types == [
        "privat", "gewerblich", "privat", "privat", "gewerblich", "privat",
        "privat", "privat", "privat", "gewerblich", "privat", "privat",
    ]


def _live_style_search_html(adid: str, poster: str) -> str:
    """Suchseite wie Live (2026): Karte ohne Versender-Span, Typ im RSC-Payload."""
    seo = f"/s-anzeige/test-artikel/{adid}-173-1"
    rsc = (
        f'&quot;seoLink&quot;:[0,&quot;{seo}&quot;],'
        f'&quot;price&quot;:[0,&quot;100 &euro;&quot;],'
        f'&quot;posterType&quot;:[0,&quot;{poster}&quot;]'
    )
    card = (
        f'<article class="flex" data-adid="{adid}" data-href="{seo}">'
        '<script type="application/ld+json">'
        '{"title":"Test","description":"Kurzbeschreibung fuer die Karte"}'
        "</script>"
        '<p class="text-secondary">100 &euro;</p>'
        "<span>10115 Berlin Mitte</span>"
        "</article>"
    )
    return f"<html><body>{rsc}{card}</body></html>"


def test_parse_search_poster_type_from_rsc_payload():
    """Live-Karten tragen keinen Versender-Text mehr; Typ steht im Seiten-Payload."""
    html = _live_style_search_html("99001", "PRIVATE")
    offers = parser.parse_search(html, limit=5)
    assert offers[0]["versender_typ"] == "privat"


def test_parse_search_poster_type_commercial_from_rsc_payload():
    html = _live_style_search_html("99002", "COMMERCIAL")
    offers = parser.parse_search(html, limit=5)
    assert offers[0]["versender_typ"] == "gewerblich"


def test_parse_search_shipping(synthetic_offers):
    versand = [o["versand_moeglich"] for o in synthetic_offers]
    assert versand == [
        True, True, False, True, True, False,
        True, True, False, True, False, False,
    ]


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
    # >=300: 2002 (420), 2004 (380), 2005 (320), 2011 (699), 2013 (1299).
    # None-Werte (2006/2010/2012/2014/2015) bleiben; Original-Reihenfolge.
    assert [o["id"] for o in out] == [
        "2002", "2004", "2005", "2006", "2010", "2011", "2012", "2013", "2014", "2015",
    ]


def test_client_filter_max_price(synthetic_offers):
    out = _client_filter(_ns(max_price=200), synthetic_offers)
    # ≤200: 2001 (200), 2003 (80); None bleibt (2006/2010/2012/2014/2015)
    assert [o["id"] for o in out] == [
        "2001", "2003", "2006", "2010", "2012", "2014", "2015",
    ]


def test_client_filter_only_shipping(synthetic_offers):
    out = _client_filter(_ns(only_shipping=True), synthetic_offers)
    # 2001,2002,2004,2005,2010,2011,2013
    assert [o["id"] for o in out] == ["2001", "2002", "2004", "2005", "2010", "2011", "2013"]


def test_client_filter_only_private(synthetic_offers):
    out = _client_filter(_ns(only_private=True), synthetic_offers)
    # alle außer 2002, 2005, 2013
    assert [o["id"] for o in out] == [
        "2001", "2003", "2004", "2006", "2010", "2011", "2012", "2014", "2015",
    ]


def test_client_filter_only_commercial(synthetic_offers):
    out = _client_filter(_ns(only_commercial=True), synthetic_offers)
    # 2002, 2005, 2013
    assert [o["id"] for o in out] == ["2002", "2005", "2013"]


def test_client_filter_limit(synthetic_offers):
    out = _client_filter(_ns(limit=2), synthetic_offers)
    assert len(out) == 2


# ---------- CLI Subprozess ----------


def _run_cli(*args, env_extra=None, xdg_config=None, xdg_state=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["ACCEPTANCE_SANDBOX"] = "1"
    # XDG-Pfade umlenken, damit Session-Datei in tmp_path landet
    if xdg_config is not None:
        env["XDG_CONFIG_HOME"] = str(xdg_config)
        env["KLEINANZEIGEN_SESSION_PATH"] = str(xdg_config / "session.json")
    if xdg_state is not None:
        env["XDG_STATE_HOME"] = str(xdg_state)
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


def _minimal_search_html(adids: list[str]) -> str:
    cards = []
    for adid in adids:
        cards.append(
            f'<article class="aditem" data-adid="{adid}">'
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"Product",'
            '"title":"t","description":"d"}'
            "</script></article>"
        )
    body = "\n".join(cards)
    return (
        "<!DOCTYPE html><html><body><main id='srchrslt-content'>"
        f"<ul class='item-list'>{body}</ul></main></body></html>"
    )


def _watch_namespace(**overrides):
    defaults = {
        "name": "t",
        "query": "x",
        "category": None,
        "location": None,
        "radius": None,
        "min_price": None,
        "max_price": None,
        "offer_type": None,
        "sender_type": None,
        "only_shipping": False,
        "sorting": None,
        "limit": 3,
        "json": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_cli_watch_keeps_seen_ids_when_pushed_off_page(tmp_path, monkeypatch):
    """Gesehene IDs bleiben gemerkt, auch wenn sie aus den Top-N rutschen."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    pages = [["1", "2", "3"], ["4", "2", "3"], ["1", "2", "3"]]
    calls = {"n": 0}

    class _Http:
        def get(self, url, **k):
            from kleinanzeigen_cli.http_client import Response

            html = _minimal_search_html(pages[calls["n"]])
            calls["n"] += 1
            return Response(status=200, url=url, text=html)

    monkeypatch.setattr(_cli_mod, "HttpClient", _Http)
    captured = []

    def _capture(payload, as_json):
        captured.append(payload)

    monkeypatch.setattr(_cli_mod, "_emit", _capture)
    for _ in range(3):
        assert _cli_mod.cmd_watch(_watch_namespace()) == 0

    assert [p["anzahl_neu"] for p in captured] == [3, 1, 0]
    assert [
        p["neue_anzeigen"][0]["id"] if p["neue_anzeigen"] else None for p in captured
    ] == ["1", "4", None]


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


@pytest.mark.parametrize(
    "argv",
    [
        ["search", "--location", "abc", "--json"],
        ["search", "--radius", "xyz", "--json"],
        ["frobnicate"],
        ["show"],
        ["watch", "--query", "x"],
    ],
)
def test_cli_argparse_errors_exit_1_json(argv):
    r = _run_cli(*argv)
    assert r.returncode == 1
    assert "usage:" not in r.stderr.lower()
    payload = json.loads(r.stderr.strip())
    assert payload.get("error")


# ---------- Auth / Chats (neu) ----------


import importlib
import kleinanzeigen_cli.auth as _auth_mod
import kleinanzeigen_cli.chats as _chats_mod
import kleinanzeigen_cli.cli as _cli_mod


# ---------- Parse-Fehler (kein Login-Exit 4) ----------

_EMPTY_SEARCH_HTML = "<html><body><main>Keine Treffer</main></body></html>"


class _EmptySearchHttp:
    def get(self, url, **k):
        from kleinanzeigen_cli.http_client import Response

        return Response(status=200, url=url, text=_EMPTY_SEARCH_HTML)


def _search_namespace(**overrides):
    import argparse

    defaults = {
        "query": "iPhone",
        "category": None,
        "location": None,
        "radius": None,
        "min_price": None,
        "max_price": None,
        "offer_type": None,
        "sender_type": None,
        "only_shipping": False,
        "only_private": False,
        "only_commercial": False,
        "sorting": None,
        "page": None,
        "limit": 25,
        "json": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_cli_search_only_private_filters_live_style_cards(monkeypatch):
    """--only-private darf Live-Karten nicht wegfiltern, wenn posterType im Payload steht."""
    from kleinanzeigen_cli.http_client import Response

    html = (
        _live_style_search_html("99001", "PRIVATE")
        + _live_style_search_html("99002", "COMMERCIAL").replace("<html><body>", "").replace(
            "</body></html>", ""
        )
    )

    class _Http:
        def get(self, url, **k):
            assert "senderType=PRIVAT" in url
            return Response(status=200, url=url, text=html)

    captured = []

    def _capture(payload, as_json):
        captured.append(payload)

    monkeypatch.setattr(_cli_mod, "HttpClient", _Http)
    monkeypatch.setattr(_cli_mod, "_emit", _capture)
    assert _cli_mod.cmd_search(_search_namespace(only_private=True)) == 0
    assert captured[0]["anzahl"] == 1
    assert captured[0]["anzeigen"][0]["id"] == "99001"
    assert captured[0]["anzeigen"][0]["versender_typ"] == "privat"


def test_cli_search_parse_error_exit_6_not_login(monkeypatch):
    monkeypatch.setattr(_cli_mod, "HttpClient", _EmptySearchHttp)
    with pytest.raises(SystemExit) as exc:
        _cli_mod.cmd_search(_search_namespace())
    assert exc.value.code == 6


def test_cli_show_parse_error_exit_6_not_login(monkeypatch):
    import argparse

    from kleinanzeigen_cli.http_client import ParseError, Response

    class _Http:
        def get(self, url, **k):
            return Response(status=200, url=url, text="<html></html>")

    def _parse_fail(*_a, **_k):
        raise ParseError("Detail-Layout unbekannt")

    monkeypatch.setattr(_cli_mod, "HttpClient", _Http)
    monkeypatch.setattr(_cli_mod.parser, "parse_detail", _parse_fail)
    args = argparse.Namespace(ad="12345", json=True)
    with pytest.raises(SystemExit) as exc:
        _cli_mod.cmd_show(args)
    assert exc.value.code == 6


def test_session_persists_refresh_token(tmp_path, monkeypatch):
    """Session-File wird geschrieben, chmod 0600 angestrebt, logout löscht."""
    sess_path = tmp_path / "session.json"
    monkeypatch.setattr(_auth_mod, "config_path", lambda: str(tmp_path))
    s = _auth_mod.Session(path=str(sess_path))
    assert not s.logged_in
    s.set_refresh_token("RT-FAKE", email="alice@example.com")
    assert s.logged_in
    assert s.refresh_token == "RT-FAKE"
    # Datei existiert
    assert sess_path.exists()
    on_disk = json.loads(sess_path.read_text())
    assert on_disk["refresh_token"] == "RT-FAKE"
    # Frisches Session-Objekt liest die gleiche Datei
    s2 = _auth_mod.Session(path=str(sess_path))
    assert s2.logged_in and s2.email == "alice@example.com"
    # Logout
    s2.clear()
    assert not s2.logged_in
    assert not sess_path.exists()


def test_session_env_fallback(tmp_path, monkeypatch):
    """Wenn keine Datei da ist, KLEINANZEIGEN_REFRESH_TOKEN wird gelesen."""
    monkeypatch.setattr(_auth_mod, "config_path", lambda: str(tmp_path))
    monkeypatch.setenv("KLEINANZEIGEN_REFRESH_TOKEN", "RT-FROM-ENV")
    s = _auth_mod.Session()
    assert s.logged_in
    assert s.refresh_token == "RT-FROM-ENV"


def test_session_get_access_token_refreshes(monkeypatch, tmp_path):
    """Bei abgelaufenem Token wird /oauth/token aufgerufen."""
    monkeypatch.setattr(_auth_mod, "config_path", lambda: str(tmp_path))
    s = _auth_mod.Session()
    s.set_refresh_token("OLD-RT", email="alice@example.com")
    s._t["expires_at"] = 0  # abgelaufen
    # Mock POST gegen /oauth/token
    import requests as _req

    def fake_post(url, json=None, headers=None, timeout=None):
        class R:
            status_code = 200
            text = "{}"
            def json(self_inner):
                return {
                    "access_token": "NEW-AT",
                    "refresh_token": "NEW-RT",
                    "expires_in": 3600,
                    "id_token": _make_id_token("alice@example.com"),
                }
        return R()
    monkeypatch.setattr(_req, "post", fake_post)
    tok = s.get_access_token()
    assert tok == "NEW-AT"
    assert s.refresh_token == "NEW-RT"
    assert s.email == "alice@example.com"


def _make_id_token(email: str) -> str:
    """Baut ein winziges, aber valides id_token (HS-signiert egal,
    wir lesen nur den Payload)."""
    import base64
    payload = {
        "email": email,
        "sub": "auth0|123",
        "iss": "https://login.kleinanzeigen.de/",
        "aud": "uV5j90myVPc2XzEOFuWUD2At17OACEGQ",
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).decode().rstrip("=")
    return f"header.{payload_b64}.signature"


# ----- Chats-API-Mocking -----

def _ok(status=200, body=None, text=None):
    class R:
        pass
    r = R()
    r.status_code = status
    r.text = text or (json.dumps(body) if body is not None else "")
    def _json(self_inner=None):
        return body or {}
    r.json = _json
    return r


def test_chats_list_parses(monkeypatch, tmp_path):
    monkeypatch.setattr(_auth_mod, "config_path", lambda: str(tmp_path))
    s = _auth_mod.Session()
    s.set_refresh_token("RT", email="alice@example.com")
    # user_id ist gesetzt → keine profile-lookup nötig
    s.set_user_id("99")
    # Token gilt als frisch
    s._t["access_token"] = "AT"
    s._t["expires_at"] = 9e18

    def fake_get(url, params=None, headers=None, timeout=None):
        assert "conversations" in url
        return _ok(200, {
            "conversations": [
                {
                    "id": "c1",
                    "adId": "12345",
                    "adTitle": "iPhone 12",
                    "role": "BUYER",
                    "sellerName": "Hans",
                    "unread": True,
                    "unreadMessagesCount": 2,
                    "receivedDate": "2026-10-13T12:34:56+02:00",
                    "textShortTrimmed": "Hallo, ist das noch da?",
                },
                {
                    "id": "c2",
                    "adId": "67890",
                    "adTitle": "Couch",
                    "role": "SELLER",
                    "buyerName": "Erika",
                    "unread": False,
                    "unreadMessagesCount": 0,
                    "receivedDate": "2026-10-12T08:00:00+0200",
                    "textShortTrimmed": "OK, danke!",
                },
            ]
        })

    import requests as _req
    monkeypatch.setattr(_req, "get", fake_get)
    items = _chats_mod.list_conversations(s)
    assert len(items) == 2
    c1 = items[0]
    assert c1["id"] == "c1"
    assert c1["anzeige_id"] == "12345"
    assert c1["rolle"] == "BUYER"
    # Bei BUYER ist die andere Partei der Verkäufer
    assert c1["gegenueber"] == "Hans"
    assert c1["ungelesen"] is True
    assert c1["ungelesen_anzahl"] == 2
    assert "2026-10-13" in c1["letzte_nachricht"]["zeit"]
    c2 = items[1]
    assert c2["rolle"] == "SELLER"
    # Bei SELLER ist die andere Partei der Käufer
    assert c2["gegenueber"] == "Erika"


def test_chats_list_requires_login(tmp_path, monkeypatch):
    monkeypatch.setattr(_auth_mod, "config_path", lambda: str(tmp_path))
    s = _auth_mod.Session()
    with pytest.raises(_auth_mod.NotLoggedIn):
        _chats_mod.list_conversations(s)


def test_chats_read_parses(monkeypatch, tmp_path):
    monkeypatch.setattr(_auth_mod, "config_path", lambda: str(tmp_path))
    s = _auth_mod.Session()
    s.set_refresh_token("RT", email="alice@example.com")
    s.set_user_id("99")
    s._t["access_token"] = "AT"
    s._t["expires_at"] = 9e18

    def fake_put(url, params=None, headers=None, timeout=None):
        return _ok(200, {
            "id": "c1",
            "adId": "12345",
            "adTitle": "iPhone 12",
            "role": "BUYER",
            "sellerName": "Hans",
            "messages": [
                {
                    "boundness": "OUT",
                    "text": "Hallo, ist das noch da?",
                    "receivedDate": "2026-10-13T10:00:00+02:00",
                    "senderName": "Ich",
                    "senderAccountType": "private",
                },
                {
                    "boundness": "IN",
                    "text": "Ja, kannst du vorbeikommen.",
                    "receivedDate": "2026-10-13T11:00:00+02:00",
                    "senderName": "Hans",
                    "senderAccountType": "private",
                },
            ],
        })

    import requests as _req
    monkeypatch.setattr(_req, "put", fake_put)
    out = _chats_mod.read_conversation(s, "c1")
    assert out["id"] == "c1"
    assert out["nachrichten_anzahl"] == 2
    msgs = out["nachrichten"]
    assert msgs[0]["richtung"] == "gesendet"
    assert msgs[1]["richtung"] == "empfangen"
    assert msgs[0]["text"] == "Hallo, ist das noch da?"


def test_chats_send_calls_post_with_json(monkeypatch, tmp_path):
    monkeypatch.setattr(_auth_mod, "config_path", lambda: str(tmp_path))
    s = _auth_mod.Session()
    s.set_refresh_token("RT", email="alice@example.com")
    s.set_user_id("99")
    s._t["access_token"] = "AT"
    s._t["expires_at"] = 9e18

    captured = {}

    def fake_post(url, params=None, headers=None, json=None, data=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["json"] = json
        return _ok(201, {})

    import requests as _req
    monkeypatch.setattr(_req, "post", fake_post)
    out = _chats_mod.send_message(s, "c1", "Hallo zurück")
    assert out["gesendet"] is True
    assert out["konversation_id"] == "c1"
    assert out["text_laenge"] == len("Hallo zurück")
    # An die richtige URL und mit Bearer
    assert captured["url"].endswith("/conversations/c1")
    assert captured["json"] == {"message": "Hallo zurück"}
    assert captured["headers"]["Authorization"].startswith("Bearer ")


def test_cli_chats_list_without_login(tmp_path):
    """Ohne Session: Exit 2, klare Fehlermeldung."""
    r = _run_cli("chats", "list", "--json", xdg_config=tmp_path)
    assert r.returncode == 2
    assert "Nicht eingeloggt" in r.stderr


def _fake_logged_in_session(tmp_path, monkeypatch, *, user_id="99"):
    """Session mit gültigem Access-Token (kein Auth0-Refresh nötig)."""
    monkeypatch.setattr(_auth_mod, "config_path", lambda: str(tmp_path))
    (tmp_path / "session.json").write_text(
        json.dumps(
            {
                "refresh_token": "RT-FAKE",
                "access_token": "AT-FAKE",
                "expires_at": 99999999999,
                "email": "alice@example.com",
                "user_id": user_id,
            }
        ),
        encoding="utf-8",
    )


def test_chats_list_network_error_raises_runtime_error(monkeypatch, tmp_path):
    _fake_logged_in_session(tmp_path, monkeypatch)
    s = _auth_mod.Session()
    import requests as _req

    def _offline(*_a, **_k):
        raise _req.exceptions.ConnectionError("offline")

    monkeypatch.setattr(_req, "get", _offline)
    with pytest.raises(RuntimeError, match="conversations"):
        _chats_mod.list_conversations(s)


def test_cli_chats_list_network_error_exit_3_json(monkeypatch, tmp_path):
    import argparse
    import requests as _req

    _fake_logged_in_session(tmp_path, monkeypatch)

    def _offline(*_a, **_k):
        raise _req.exceptions.ConnectionError("offline")

    monkeypatch.setattr(_req, "get", _offline)
    args = argparse.Namespace(page=0, size=100, json=True)
    with pytest.raises(SystemExit) as exc:
        _cli_mod.cmd_chats_list(args)
    assert exc.value.code == 3


def test_cli_chats_read_network_error_exit_3_json(monkeypatch, tmp_path):
    import argparse
    import requests as _req

    _fake_logged_in_session(tmp_path, monkeypatch)

    def _offline(*_a, **_k):
        raise _req.exceptions.ConnectionError("offline")

    monkeypatch.setattr(_req, "put", _offline)
    args = argparse.Namespace(id="123", json=True)
    with pytest.raises(SystemExit) as exc:
        _cli_mod.cmd_chats_read(args)
    assert exc.value.code == 3


def test_cli_chats_send_network_error_exit_3_json(monkeypatch, tmp_path):
    import argparse
    import requests as _req

    _fake_logged_in_session(tmp_path, monkeypatch)

    def _offline(*_a, **_k):
        raise _req.exceptions.ConnectionError("offline")

    monkeypatch.setattr(_req, "post", _offline)
    args = argparse.Namespace(id="123", text="x", confirm=True, json=True)
    with pytest.raises(SystemExit) as exc:
        _cli_mod.cmd_chats_send(args)
    assert exc.value.code == 3


def test_cli_chats_send_requires_confirm(tmp_path, monkeypatch):
    """Ohne --confirm: Exit 5, kein HTTP-Call."""
    # Session in tmp_path anlegen (via Subprozess-Login mit env-Token)
    r1 = _run_cli(
        "login", "--json",
        xdg_config=tmp_path,
        env_extra={"KLEINANZEIGEN_REFRESH_TOKEN": "RT-FOR-CONFIRM-TEST"},
    )
    assert r1.returncode == 0, r1.stderr
    # HTTP-Calls global abfangen
    import requests as _req
    import kleinanzeigen_cli.auth as _a
    called = {"post": False}
    orig_post = _req.post
    def fake_post(*a, **k):
        called["post"] = True
        return _ok(201, {})
    monkeypatch.setattr(_req, "post", fake_post)
    # User-ID wird in chats.send_message via profile.json geholt, das wir
    # auch mocken.
    orig_get = _req.get
    def fake_get(url, *a, **k):
        if "profile.json" in url:
            return _ok(200, {"data": {"id": 99}})
        return orig_get(url, *a, **k)
    monkeypatch.setattr(_req, "get", fake_get)
    r = _run_cli(
        "chats", "send", "c1", "--text", "Hallo",
        xdg_config=tmp_path,
    )
    assert r.returncode == 5
    assert "confirm" in r.stderr
    assert called["post"] is False, "es darf kein HTTP-Call abgesetzt werden"


def test_cli_login_via_env_token(tmp_path):
    """KLEINANZEIGEN_REFRESH_TOKEN → Login ohne Browser, Session-Datei da."""
    r = _run_cli(
        "login", "--json",
        xdg_config=tmp_path,
        env_extra={
            "KLEINANZEIGEN_REFRESH_TOKEN": "RT-FROM-ENV",
            "KLEINANZEIGEN_EMAIL": "alice@example.com",
        },
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["logged_in"] is True
    assert payload["email"] == "alice@example.com"
    assert payload["quelle"].startswith("env:")
    # Session-Datei persistiert
    assert (tmp_path / "session.json").exists()


def test_cli_logout(tmp_path):
    """logout entfernt die Session-Datei."""
    # erst Session anlegen
    r1 = _run_cli(
        "login", "--json",
        xdg_config=tmp_path,
        env_extra={"KLEINANZEIGEN_REFRESH_TOKEN": "RT"},
    )
    assert r1.returncode == 0, r1.stderr
    r = _run_cli("logout", "--json", xdg_config=tmp_path)
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["logged_out"] is True
    assert not (tmp_path / "session.json").exists()


def test_cli_login_browser_path_errors(tmp_path):
    """login ohne env und ohne playwright → klare Fehlermeldung (Exit 4)."""
    r = _run_cli("login", "--json", xdg_config=tmp_path)
    # playwright ist in venv nicht installiert → expect exit 4
    assert r.returncode == 4
    assert "playwright" in r.stderr.lower()


def test_perform_login_uses_callback_url_for_token_exchange(
    monkeypatch, tmp_path
):
    """Nach Playwright-Redirect muss die Callback-URL (nicht nur code) an complete_login."""
    import contextlib
    import types

    import kleinanzeigen_cli.login as _login_mod
    import requests as _req

    monkeypatch.setattr(_auth_mod, "config_path", lambda: str(tmp_path))

    callback_url = (
        "https://login.kleinanzeigen.de/android/com.ebay.kleinanzeigen/callback"
        "?code=FAKECODE123&state=xyz"
    )

    class _FakePage:
        url = callback_url

        def goto(self, *args, **kwargs):
            pass

        def wait_for_url(self, *args, **kwargs):
            pass

        def content(self):
            return ""

    class _FakeBrowser:
        def new_context(self, **kwargs):
            class _Ctx:
                def new_page(self):
                    return _FakePage()

            return _Ctx()

        def close(self):
            pass

    class _FakeChromium:
        def launch(self, **kwargs):
            return _FakeBrowser()

    class _FakePlaywright:
        chromium = _FakeChromium()

    @contextlib.contextmanager
    def _fake_sync_playwright():
        yield _FakePlaywright()

    _sync_api = types.ModuleType("playwright.sync_api")
    _sync_api.sync_playwright = _fake_sync_playwright
    _playwright = types.ModuleType("playwright")
    _playwright.sync_api = _sync_api
    monkeypatch.setitem(sys.modules, "playwright", _playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", _sync_api)

    monkeypatch.setattr(_login_mod, "_has_playwright", lambda: True)

    exchanged_codes: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        if json and json.get("grant_type") == "authorization_code":
            exchanged_codes.append(json.get("code"))
        class _R:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "access_token": "AT",
                    "refresh_token": "RT-BROWSER",
                    "expires_in": 3600,
                    "id_token": _make_id_token("browser@example.com"),
                }

        return _R()

    monkeypatch.setattr(_req, "post", fake_post)

    info = _login_mod.perform_login()
    assert info["logged_in"] is True
    assert exchanged_codes == ["FAKECODE123"]
    sess_path = tmp_path / "session.json"
    assert sess_path.exists()
    assert json.loads(sess_path.read_text())["refresh_token"] == "RT-BROWSER"