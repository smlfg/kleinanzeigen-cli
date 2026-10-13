"""HTTP-Client (requests-Session) für kleinanzeigen.de."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import requests

UA = "kleinanzeigen-cli/0.1.0 (+https://github.com/smlfg/kleinanzeigen-cli; contact: 186922365+smlfg@users.noreply.github.com)"

# explizite Header – setdefault reicht nicht, wenn die Session schon
# einen "python-requests/x.y"-UA hat (siehe mydealz-Lektion).
_HEADERS = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",  # kein "br" – manche Edges blocken
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
}

BASE = "https://www.kleinanzeigen.de"


class HTTPError(Exception):
    """Netzwerk- oder HTTP-Fehler (Exit 3)."""

    def __init__(self, message: str, status: Optional[int] = None, url: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.url = url


class ParseError(Exception):
    """Antwort konnte nicht wie erwartet geparst werden (Exit 4)."""

    def __init__(self, message: str, url: Optional[str] = None):
        super().__init__(message)
        self.url = url


@dataclass
class Response:
    status: int
    url: str
    text: str
    headers: dict = field(default_factory=dict)
    cookies: dict = field(default_factory=dict)

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise HTTPError(f"HTTP {self.status} for {self.url}", status=self.status, url=self.url)


class HttpClient:
    """Sehr dünner Wrapper über requests.Session.

    * Kein Login: keine Cookies werden persistiert oder per Hand gesetzt.
    * Höflich: User-Agent + optional 0,5 s Pause zwischen 2 Requests.
    """

    def __init__(
        self,
        user_agent: str = UA,
        timeout: float = 30.0,
        session: Optional[requests.Session] = None,
        polite: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._session = session or requests.Session()
        # explizit setzen, nicht setdefault – Session-Defaults haben oft
        # einen python-requests-UA, den die Site dann ablehnt.
        for k, v in _HEADERS.items():
            self._session.headers[k] = v
        self._polite = polite
        self._last_t = 0.0

    def _tick(self) -> None:
        if not self._polite:
            return
        elapsed = time.monotonic() - self._last_t
        if elapsed < 0.6:
            time.sleep(0.6 - elapsed)
        self._last_t = time.monotonic()

    def get(self, url: str, *, allow_redirects: bool = True) -> Response:
        self._tick()
        try:
            r = self._session.get(
                url, timeout=self.timeout, allow_redirects=allow_redirects
            )
        except requests.RequestException as e:
            raise HTTPError(f"GET {url} failed: {e}", url=url) from e
        return Response(
            status=r.status_code,
            url=r.url,
            text=r.text,
            headers=dict(r.headers),
            cookies={c.name: c.value for c in r.cookies},
        )


def state_path() -> str:
    """Pfad für lokale Suchaufträge / Seen-IDs. Nur lokal, keine Cookies/Passwörter."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "kleinanzeigen-cli")


def config_path() -> str:
    """Pfad für **vertrauliche** Daten: Login-Session (Refresh-Token).

    Nach XDG_CONFIG_HOME: standardmäßig ``~/.config/kleinanzeigen-cli``.
    Dort liegt der OAuth-Refresh-Token, der wie ein Passwort zu behandeln
    ist (0600-Berechtigung).
    """
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "kleinanzeigen-cli")


def write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default
