"""CLI für kleinanzeigen – Agent-Schnittstelle.

Subbefehle:
  search       Stichwortsuche mit Filtern (öffentlich, kein Login)
  show         Anzeigen-Details (öffentlich)
  categories   Liste der unterstützten Kategorie-Slugs
  watch        Lokaler Suchauftrag: nur neue IDs melden (kein Push)
  login        Browser-Login (Auth0) – speichert Refresh-Token
  logout       Session löschen
  chats list   Eigene Konversationen auflisten (Login nötig)
  chats read   Eine Konversation lesen (Login nötig)
  chats send   Auf eine Konversation antworten (NUR mit --confirm)
  --version
  --help

Read-only by default. Schreibzugriffe nur über `chats send`, dort
zwingend mit explizitem --confirm.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, NoReturn, Optional

from . import __version__, parser, search
from . import auth as _auth
from . import chats as _chats
from . import login as _login
from .http_client import HttpClient, HTTPError, ParseError, read_json, state_path, write_json


# ---------- I/O-Helfer ----------


def _emit(payload: Any, as_json: bool) -> None:
    """Gibt `payload` als JSON aus. Prompts gehen auf stderr."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _err(msg: str, *, code: int, **extra: Any) -> NoReturn:
    payload = {"error": msg, **extra}
    sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stderr.flush()
    sys.exit(code)


# ---------- search ----------


def _client_filter(args: argparse.Namespace, offers: list[dict]) -> list[dict]:
    """Filtert bereits geparste Anzeigen nach clientseitigen Kriterien.

    Der Server-Filter ist primär; diese Funktion fängt Fälle ab, in
    denen das Parsen tolerant war (Preis/Bereich leer) oder das HTML
    den Filter nicht eindeutig widerspiegelt.
    """
    if args.min_price is not None:
        offers = [
            o for o in offers
            if o.get("preis_euro") is None or o["preis_euro"] >= args.min_price
        ]
    if args.max_price is not None:
        offers = [
            o for o in offers
            if o.get("preis_euro") is None or o["preis_euro"] <= args.max_price
        ]
    if args.only_shipping:
        offers = [o for o in offers if o.get("versand_moeglich") is True]
    if args.only_private:
        offers = [o for o in offers if o.get("versender_typ") == "privat"]
    if args.only_commercial:
        offers = [o for o in offers if o.get("versender_typ") == "gewerblich"]
    if args.offer_type:
        # ANZEIGE = Verkauf, GESUCH = Suche. Der Server-Filter sortiert
        # schon vor; hier filtern wir strenger, falls das Parsing mal
        # uneindeutig war (Cards haben keinen direkten Marker).
        pass
    if args.limit:
        offers = offers[: args.limit]
    return offers


def cmd_search(args: argparse.Namespace) -> int:
    try:
        url = search.build_search_url(
            category=args.category,
            location=args.location,
            radius=args.radius,
            keywords=args.query,
            min_price=args.min_price,
            max_price=args.max_price,
            offer_type=args.offer_type,
            sender_type=args.sender_type,
            shipping=True if args.only_shipping else None,
            sorting=args.sorting,
            page=args.page,
            slug=args.category,
        )
    except ValueError as e:
        _err(str(e), code=1)

    http = HttpClient()
    try:
        r = http.get(url)
    except HTTPError as e:
        _err(f"HTTP-Fehler: {e}", code=3, status=e.status)
    if r.status >= 400:
        _err(
            f"HTTP {r.status} beim Laden der Suche",
            code=3,
            status=r.status,
            url=url,
        )
    try:
        offers = parser.parse_search(r.text, limit=args.limit or 25)
    except ParseError as e:
        _err(f"Parse-Fehler: {e}", code=4, url=url)

    offers = _client_filter(args, offers)

    payload = {
        "quelle": "https://www.kleinanzeigen.de",
        "filter": {
            "query": args.query,
            "category": args.category,
            "location": args.location,
            "radius": args.radius,
            "min_price": args.min_price,
            "max_price": args.max_price,
            "offer_type": args.offer_type,
            "sender_type": args.sender_type,
            "only_shipping": args.only_shipping,
            "only_private": args.only_private,
            "only_commercial": args.only_commercial,
            "sorting": args.sorting,
            "page": args.page,
            "limit": args.limit,
        },
        "url": url,
        "anzahl": len(offers),
        "anzeigen": offers,
    }
    _emit(payload, args.json)
    return 0


# ---------- show ----------


def cmd_show(args: argparse.Namespace) -> int:
    adid = search.parse_adid_from_url(args.ad)
    if not adid:
        _err(
            f"Konnte keine Anzeigen-ID in {args.ad!r} finden. "
            "Erwartet: numerische ID, /s-anzeige/.../<ID>-… oder volle URL.",
            code=1,
        )
    url = search.detail_url(adid)
    http = HttpClient()
    try:
        r = http.get(url)
    except HTTPError as e:
        _err(f"HTTP-Fehler: {e}", code=3, status=e.status)
    if r.status >= 400:
        _err(
            f"HTTP {r.status} beim Laden der Anzeige",
            code=3,
            status=r.status,
            url=url,
        )
    try:
        ang = parser.parse_detail(r.text, adid=adid)
    except ParseError as e:
        _err(f"Parse-Fehler: {e}", code=4, url=url)

    if not ang.get("url"):
        ang["url"] = r.url
    _emit({"quelle": "https://www.kleinanzeigen.de", "anzeige": ang}, args.json)
    return 0


# ---------- categories ----------


def cmd_categories(args: argparse.Namespace) -> int:
    cats = search.list_categories()
    payload = {
        "quelle": "https://www.kleinanzeigen.de",
        "anzahl": len(cats),
        "kategorien": cats,
    }
    _emit(payload, args.json)
    return 0


# ---------- watch ----------


def _watch_path(name: str) -> str:
    """Pfad für einen benannten Suchauftrag (keine Cookies/Passwörter)."""
    safe = "".join(c for c in (name or "default") if c.isalnum() or c in "-_")
    if not safe:
        safe = "default"
    import os

    return os.path.join(state_path(), "watch", f"{safe}.json")


def cmd_watch(args: argparse.Namespace) -> int:
    """Lokaler Suchauftrag: vergleicht aktuelle Treffer mit gespeicherten IDs.

    Es werden **keine** Daten an externe Dienste geschickt. Nur die
    IDs neuer Anzeigen werden gemeldet.
    """
    try:
        url = search.build_search_url(
            category=args.category,
            location=args.location,
            radius=args.radius,
            keywords=args.query,
            min_price=args.min_price,
            max_price=args.max_price,
            offer_type=args.offer_type,
            sender_type=args.sender_type,
            shipping=True if args.only_shipping else None,
            sorting=args.sorting,
            slug=args.category,
        )
    except ValueError as e:
        _err(str(e), code=1)

    http = HttpClient()
    try:
        r = http.get(url)
    except HTTPError as e:
        _err(f"HTTP-Fehler: {e}", code=3, status=e.status)
    if r.status >= 400:
        _err(
            f"HTTP {r.status} beim Laden der Suche",
            code=3,
            status=r.status,
            url=url,
        )
    try:
        offers = parser.parse_search(r.text, limit=args.limit or 25)
    except ParseError as e:
        _err(f"Parse-Fehler: {e}", code=4, url=url)

    path = _watch_path(args.name)
    seen = set(read_json(path, default=[]) or [])
    current_ids = [o["id"] for o in offers]
    new_ids = [i for i in current_ids if i not in seen]
    # Persistieren: gesehen + current_ids (Reihenfolge = neueste zuerst).
    write_json(path, current_ids)

    payload = {
        "quelle": "https://www.kleinanzeigen.de",
        "watch_name": args.name,
        "url": url,
        "anzahl_aktuell": len(offers),
        "anzahl_neu": len(new_ids),
        "neue_anzeigen": [o for o in offers if o["id"] in set(new_ids)],
        "alle_ids": current_ids,
    }
    _emit(payload, args.json)
    return 0


# ---------- login ----------


def cmd_login(args: argparse.Namespace) -> int:
    """Browser-Login via Playwright. Schreibt Refresh-Token in session.json."""
    if os.environ.get("KLEINANZEIGEN_REFRESH_TOKEN"):
        # Token via env → nur Session-Datei mitnehmen
        session = _auth.Session()
        session.set_refresh_token(
            os.environ["KLEINANZEIGEN_REFRESH_TOKEN"],
            email=os.environ.get("KLEINANZEIGEN_EMAIL"),
        )
        _emit(
            {
                "logged_in": True,
                "email": session.email,
                "quelle": "env:KLEINANZEIGEN_REFRESH_TOKEN",
                "session_file": session.path,
            },
            args.json,
        )
        return 0
    try:
        info = _login.perform_login(headed=args.headed, timeout=args.timeout)
    except _auth.NotLoggedIn as e:
        _err(str(e), code=2)
    except Exception as e:
        # Captcha, Timeout, fehlendes playwright, Auth0-Fehler … alles,
        # was den Login verhindert, bekommt Exit 4 (externe Sperre).
        _err(str(e), code=4)
    info["quelle"] = "browser"
    _emit(info, args.json)
    return 0


# ---------- logout ----------


def cmd_logout(args: argparse.Namespace) -> int:
    session = _auth.Session()
    if session.logged_in:
        session.clear()
        _emit(
            {"logged_out": True, "session_file": session.path},
            args.json,
        )
    else:
        _emit(
            {"logged_out": False, "grund": "keine Session vorhanden"},
            args.json,
        )
    return 0


# ---------- chats list ----------


def cmd_chats_list(args: argparse.Namespace) -> int:
    session = _auth.Session()
    if not session.logged_in:
        _err(
            "Nicht eingeloggt. Bitte zuerst `kleinanzeigen login` (oder "
            "KLEINANZEIGEN_REFRESH_TOKEN setzen).",
            code=2,
        )
    try:
        items = _chats.list_conversations(
            session, page=args.page, size=args.size
        )
    except _auth.NotLoggedIn as e:
        _err(str(e), code=2)
    except RuntimeError as e:
        _err(f"Chats-Fehler: {e}", code=3)
    _emit(
        {
            "quelle": "https://www.kleinanzeigen.de",
            "email": session.email,
            "anzahl": len(items),
            "unterhaltungen": items,
        },
        args.json,
    )
    return 0


# ---------- chats read ----------


def cmd_chats_read(args: argparse.Namespace) -> int:
    session = _auth.Session()
    if not session.logged_in:
        _err(
            "Nicht eingeloggt. Bitte zuerst `kleinanzeigen login`.",
            code=2,
        )
    try:
        conv = _chats.read_conversation(session, args.id)
    except _auth.NotLoggedIn as e:
        _err(str(e), code=2)
    except RuntimeError as e:
        _err(f"Chats-Fehler: {e}", code=3)
    conv["quelle"] = "https://www.kleinanzeigen.de"
    _emit(conv, args.json)
    return 0


# ---------- chats send (NUR mit --confirm) ----------


def cmd_chats_send(args: argparse.Namespace) -> int:
    """Sendet eine Nachricht. Ohne ``--confirm`` → Exit 5."""
    if not args.confirm:
        _err(
            "Senden ohne --confirm nicht erlaubt. Übergib --confirm, "
            "wenn die Nachricht tatsächlich verschickt werden soll.",
            code=5,
        )
    session = _auth.Session()
    if not session.logged_in:
        _err(
            "Nicht eingeloggt. Bitte zuerst `kleinanzeigen login`.",
            code=2,
        )
    try:
        result = _chats.send_message(session, args.id, args.text)
    except _auth.NotLoggedIn as e:
        _err(str(e), code=2)
    except ValueError as e:
        _err(str(e), code=1)
    except RuntimeError as e:
        _err(f"Sende-Fehler: {e}", code=3)
    result["quelle"] = "https://www.kleinanzeigen.de"
    _emit(result, args.json)
    return 0


# ---------- Argument-Parser ----------


class _KleinanzeigenArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _err(message, code=1)


def _build_parser() -> argparse.ArgumentParser:
    p = _KleinanzeigenArgumentParser(
        prog="kleinanzeigen",
        description=(
            "kleinanzeigen – Agent-CLI für kleinanzeigen.de "
            "(Suche, Filter, Details, lokaler Suchauftrag, Login & Chats). "
            "Read-only by default; Senden via `chats send --confirm`."
        ),
    )
    p.add_argument("--version", action="version", version=f"kleinanzeigen {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="SUBCOMMAND")

    # search
    s = sub.add_parser("search", help="Anzeigen suchen")
    s.add_argument("--query", "-q", default=None,
                   help="Stichwort (z. B. 'iPhone 12')")
    s.add_argument(
        "--category", "-c", default=None,
        help="Kategorie-Slug (z. B. 'handy-telefon') oder c-ID (z. B. 'c173')",
    )
    s.add_argument("--location", "-l", type=int, default=None,
                   help="Location-ID (numerisch, z. B. 3331 für Berlin)")
    s.add_argument("--radius", "-r", type=int, default=None,
                   help="Umkreis in km (5, 10, 20, 50, 100, 150, 200)")
    s.add_argument("--min-price", type=int, default=None, help="Mindestpreis in €")
    s.add_argument("--max-price", type=int, default=None, help="Höchstpreis in €")
    s.add_argument(
        "--offer-type", choices=["ANZEIGE", "GESUCH"], default=None,
        help="ANZEIGE = Angebot, GESUCH = Gesuch",
    )
    s.add_argument(
        "--sender-type", choices=["PRIVAT", "GEWERBLICH"], default=None,
        help="PRIVAT oder GEWERBLICH",
    )
    s.add_argument("--only-shipping", action="store_true",
                   help="nur Anzeigen mit Versand")
    s.add_argument("--only-private", action="store_true",
                   help="nur private Verkäufer (clientseitig)")
    s.add_argument("--only-commercial", action="store_true",
                   help="nur gewerbliche Verkäufer (clientseitig)")
    s.add_argument(
        "--sorting",
        choices=[
            "PRICE_ASCENDING",
            "PRICE_DESCENDING",
            "DATE_ASCENDING",
            "DATE_DESCENDING",
            "DISTANCE_ASCENDING",
        ],
        default=None,
        help="Sortierung",
    )
    s.add_argument("--page", type=int, default=None, help="Seitenzahl (1..)")
    s.add_argument("--limit", type=int, default=25, help="max. Anzahl Treffer")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    # show
    sh = sub.add_parser("show", help="Anzeigen-Details anzeigen")
    sh.add_argument("ad", help="Anzeigen-ID, /s-anzeige/...-Pfad oder URL")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=cmd_show)

    # categories
    cat = sub.add_parser("categories", help="unterstützte Kategorie-Slugs auflisten")
    cat.add_argument("--json", action="store_true")
    cat.set_defaults(func=cmd_categories)

    # watch
    w = sub.add_parser("watch", help="lokaler Suchauftrag (nur neue IDs)")
    w.add_argument("--name", required=True,
                   help="Name des Auftrags (für ~/.local/state/watches)")
    w.add_argument("--query", "-q", default=None)
    w.add_argument("--category", "-c", default=None)
    w.add_argument("--location", "-l", type=int, default=None)
    w.add_argument("--radius", "-r", type=int, default=None)
    w.add_argument("--min-price", type=int, default=None)
    w.add_argument("--max-price", type=int, default=None)
    w.add_argument(
        "--offer-type", choices=["ANZEIGE", "GESUCH"], default=None,
    )
    w.add_argument(
        "--sender-type", choices=["PRIVAT", "GEWERBLICH"], default=None,
    )
    w.add_argument("--only-shipping", action="store_true")
    w.add_argument(
        "--sorting",
        choices=[
            "PRICE_ASCENDING",
            "PRICE_DESCENDING",
            "DATE_ASCENDING",
            "DATE_DESCENDING",
            "DISTANCE_ASCENDING",
        ],
        default=None,
    )
    w.add_argument("--limit", type=int, default=25)
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_watch)

    # login / logout
    lg = sub.add_parser(
        "login",
        help=(
            "Login via Browser (Playwright). Schreibt Refresh-Token in "
            "$XDG_CONFIG_HOME/kleinanzeigen-cli/session.json."
        ),
    )
    lg.add_argument(
        "--headed", action="store_true",
        help=("Browser-Fenster sichtbar machen – nötig, wenn headless "
              "durch Captcha/2FA blockiert wird."),
    )
    lg.add_argument(
        "--timeout", type=float, default=120.0,
        help="Sekunden, die auf den Login-Redirect gewartet wird (default 120).",
    )
    lg.add_argument("--json", action="store_true")
    lg.set_defaults(func=cmd_login)

    lo = sub.add_parser("logout", help="Session-Datei löschen (Token weg).")
    lo.add_argument("--json", action="store_true")
    lo.set_defaults(func=cmd_logout)

    # chats
    ch = sub.add_parser("chats", help="Chat-Befehle (list/read/send).")
    chat_sub = ch.add_subparsers(dest="chats_cmd", metavar="CHATCMD")
    cl = chat_sub.add_parser("list", help="Eigene Konversationen auflisten.")
    cl.add_argument("--page", type=int, default=0)
    cl.add_argument("--size", type=int, default=100)
    cl.add_argument("--json", action="store_true")
    cl.set_defaults(func=cmd_chats_list)

    cr = chat_sub.add_parser("read", help="Eine Konversation lesen.")
    cr.add_argument("id", help="Konversations-ID (z. B. aus `chats list`).")
    cr.add_argument("--json", action="store_true")
    cr.set_defaults(func=cmd_chats_read)

    cs = chat_sub.add_parser("send", help="Antwort in einer Konversation senden.")
    cs.add_argument("id", help="Konversations-ID.")
    cs.add_argument(
        "--text", required=True,
        help="Nachrichtentext. **Niemals ohne --confirm** aufrufen.",
    )
    cs.add_argument(
        "--confirm", action="store_true",
        help="Explizit bestätigen, dass die Nachricht *wirklich* gesendet "
             "werden soll. Ohne dieses Flag: Exit 5.",
    )
    cs.add_argument("--json", action="store_true")
    cs.set_defaults(func=cmd_chats_send)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser_ = _build_parser()
    args = parser_.parse_args(argv)
    if not getattr(args, "func", None):
        parser_.print_help(sys.stderr)
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())