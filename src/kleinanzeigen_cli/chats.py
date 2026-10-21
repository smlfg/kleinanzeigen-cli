"""Chats: list, read, send.

Wir benutzen die mobile Message-Box-API auf ``gateway.kleinanzeigen.de``.
Erfordert einen gültigen Access-Token (siehe :mod:`.auth`).

Felder in der JSON-Ausgabe:

* ``anzeige_id`` / ``anzeige_titel`` – zu welcher Anzeige der Chat gehört
* ``rolle`` – "BUYER" oder "SELLER"
* ``gegenueber`` – Name der anderen Partei
* ``ungelesen`` / ``ungelesen_anzahl`` – boolesch / int
* ``letzte_nachricht`` – Vorschau-Text + Zeitstempel
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import requests

from . import auth
from .auth import Session

GATEWAY = "https://gateway.kleinanzeigen.de"
API = "https://api.kleinanzeigen.de"

# Headergift für die App-Authentifizierung.
APP_VERSION = "2026.25.0"


def _net_call(label: str, fn):
    """Führt einen requests-Call aus; Netzwerkfehler → RuntimeError (Exit 3 im CLI)."""
    try:
        return fn()
    except requests.RequestException as e:
        raise RuntimeError(f"{label}: {e}") from e


def _user_id(session: Session) -> str:
    """Liefert die numerische Account-ID (lazy lookup)."""
    if session.user_id:
        return str(session.user_id)
    email = session.email
    if not email:
        # Token-Import (CI): E-Mail kann beim Refresh aus id_token kommen.
        session.get_access_token()
        email = session.email
    if not email:
        raise auth.NotLoggedIn(
            "E-Mail fehlt in der Session – KLEINANZEIGEN_EMAIL setzen "
            "oder Refresh mit id_token nutzen."
        )
    # Profile-API (Basic-Auth wie die App).
    r = _net_call(
        "profile.json",
        lambda: requests.get(
            f"{API}/api/users/{email}/profile.json",
            headers={
                "Authorization": "Basic " + _basic_b64(),
                "Accept": "application/json",
                "User-Agent": f"Kleinanzeigen/{APP_VERSION} (Android 13; Pixel 7)",
            },
            timeout=30,
        ),
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"profile.json → {r.status_code}: {r.text[:200]}"
        )
    body = r.json()
    uid = (body.get("data") or {}).get("id") or body.get("id")
    if not uid:
        raise RuntimeError(
            f"Unerwartete profile.json-Antwort: {json.dumps(body)[:200]}"
        )
    session.set_user_id(uid)
    return str(uid)


def _basic_b64() -> str:
    import base64
    return base64.b64encode(
        f"{auth.BASIC_AUTH[0]}:{auth.BASIC_AUTH[1]}".encode()
    ).decode()


def _parse_iso(s: str) -> Optional[str]:
    """Normalisiert einen ISO-8601-String (z. B. "+0200" → "+02:00")."""
    if not s:
        return None
    if len(s) > 5 and s[-5] in "+-" and ":" not in s[-5:]:
        s = s[:-2] + ":" + s[-2:]
    try:
        return _dt.datetime.fromisoformat(s).isoformat()
    except ValueError:
        return s


# ---------- Conversations (chats list) ----------


def list_conversations(session: Session, *, page: int = 0, size: int = 100) -> list[dict]:
    """Liefert die Chat-Inbox des eingeloggten Nutzers (neueste zuerst)."""
    if not session.logged_in:
        raise auth.NotLoggedIn("Nicht eingeloggt – bitte zuerst `login`.")
    uid = _user_id(session)
    r = _net_call(
        "conversations",
        lambda: requests.get(
            f"{GATEWAY}/messagebox/api/users/{uid}/conversations",
            params={"page": page, "size": size},
            headers=session.auth_headers(),
            timeout=30,
        ),
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"conversations → {r.status_code}: {r.text[:200]}"
        )
    body = r.json()
    items = body.get("conversations") or body.get("data") or []
    if isinstance(items, dict):
        items = items.get("conversations") or items.get("items") or []
    out = []
    for c in items:
        if not isinstance(c, dict):
            continue
        role = (c.get("role") or "").upper()
        # Die andere Partei ist relativ zur eigenen Rolle.
        if role == "BUYER":
            other = c.get("sellerName") or ""
        elif role == "SELLER":
            other = c.get("buyerName") or ""
        else:
            other = c.get("sellerName") or c.get("buyerName") or ""
        out.append({
            "id": str(c.get("id", "")),
            "anzeige_id": str(c.get("adId", "")),
            "anzeige_titel": c.get("adTitle") or "",
            "rolle": role or None,
            "gegenueber": other or None,
            "ungelesen": bool(c.get("unread")),
            "ungelesen_anzahl": int(c.get("unreadMessagesCount") or 0),
            "letzte_nachricht": {
                "text": c.get("textShortTrimmed") or "",
                "zeit": _parse_iso(c.get("receivedDate") or ""),
            },
        })
    return out


# ---------- read (Konversation lesen) ----------


def read_conversation(session: Session, conversation_id: str) -> dict:
    """Liefert eine Konversation samt aller Nachrichten.

    Die PUT-Operation öffnet den Thread (so macht es die App) und liefert
    ein Objekt mit ``messages`` (Liste) und Metadaten.
    """
    if not session.logged_in:
        raise auth.NotLoggedIn("Nicht eingeloggt – bitte zuerst `login`.")
    uid = _user_id(session)
    if not conversation_id:
        raise ValueError("conversation_id fehlt")
    r = _net_call(
        f"conversation {conversation_id}",
        lambda: requests.put(
            f"{GATEWAY}/messagebox/api/users/{uid}/conversations/{conversation_id}",
            params={"contentWarnings": "true"},
            headers=session.auth_headers(),
            timeout=30,
        ),
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"conversation {conversation_id} → {r.status_code}: {r.text[:200]}"
        )
    conv = r.json()
    raw_msgs = (
        conv.get("messages")
        or (conv.get("data") or {}).get("messages")
        or []
    )
    messages = []
    for m in raw_msgs if isinstance(raw_msgs, list) else []:
        if not isinstance(m, dict):
            continue
        bound = (m.get("boundness") or m.get("direction") or "").upper()
        if "IN" in bound:
            direction = "empfangen"
        elif "OUT" in bound:
            direction = "gesendet"
        else:
            direction = bound.lower() or None
        text = (
            m.get("text")
            or m.get("textShort")
            or m.get("title")
            or ""
        )
        messages.append({
            "text": text,
            "richtung": direction,
            "zeit": _parse_iso(m.get("receivedDate") or ""),
            "absender_name": m.get("senderName") or None,
            "absender_typ": m.get("senderAccountType") or None,
        })
    # Konversations-Metadaten
    role = (conv.get("role") or "").upper() or None
    if role == "BUYER":
        other = conv.get("sellerName")
    elif role == "SELLER":
        other = conv.get("buyerName")
    else:
        other = conv.get("sellerName") or conv.get("buyerName")
    return {
        "id": str(conv.get("id", conversation_id)),
        "anzeige_id": str(conv.get("adId", "")),
        "anzeige_titel": conv.get("adTitle") or "",
        "rolle": role,
        "gegenueber": other or None,
        "nachrichten_anzahl": len(messages),
        "nachrichten": messages,
    }


# ---------- send ----------


def send_message(
    session: Session, conversation_id: str, text: str
) -> dict:
    """Sendet eine Nachricht in einen bestehenden Chat.

    **Achtung:** Schreibender Endpoint. Darf NUR mit explizitem
    ``--confirm`` aufgerufen werden (im CLI-Layer).
    """
    if not session.logged_in:
        raise auth.NotLoggedIn("Nicht eingeloggt – bitte zuerst `login`.")
    if not text or not text.strip():
        raise ValueError("Leerer Text – nichts zu senden.")
    uid = _user_id(session)
    r = _net_call(
        f"send {conversation_id}",
        lambda: requests.post(
            f"{GATEWAY}/messagebox/api/users/{uid}/conversations/{conversation_id}",
            params={
                "warnPhoneNumber": "false",
                "warnEmail": "false",
                "warnBankDetails": "false",
            },
            json={"message": text},
            headers=session.auth_headers(),
            timeout=30,
        ),
    )
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(
            f"send {conversation_id} → {r.status_code}: {r.text[:200]}"
        )
    return {
        "gesendet": True,
        "konversation_id": str(conversation_id),
        "text_laenge": len(text),
    }
