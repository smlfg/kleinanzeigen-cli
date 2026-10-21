"""Login-Session für kleinanzeigen-cli.

Speichert **nur** den OAuth-Refresh-Token (nie Passwörter) unter
``$XDG_CONFIG_HOME/kleinanzeigen-cli/session.json`` mit Modus 0600.

Login-Quellen (Reihenfolge):

1. **Manueller Refresh-Token** via Umgebungsvariable
   ``KLEINANZEIGEN_REFRESH_TOKEN``. Praktisch für CI, Container und zum
   einmaligen Übertragen einer bestehenden Session auf einen anderen
   Rechner — kein Browser nötig.
2. **Browser-Login** via Playwright + Auth0 (Authorization Code + PKCE).
   Headless funktioniert nur, wenn Akamai / Cloudflare Turnstile den Bot
   durchlässt; sonst ``--headed`` (oder manueller Token-Import) nötig.

Token-Format ist mit `kleinanzeigen-api` kompatibel (gleiche Felder:
``access_token``, ``refresh_token``, ``expires_at``, ``email``). So kann
eine bestehende ``~/.kleinanzeigen_api/token.json`` 1:1 übernommen
werden, indem man den Inhalt in unsere session.json kopiert.

Wir lesen/wreiben **nur** den Refresh-Token. Die flüchtigen
Access-Tokens werden bei Bedarf über den Auth0-Token-Endpoint geholt
und frisch im Speicher gehalten.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

from .http_client import config_path

AUTH0_DOMAIN = "https://login.kleinanzeigen.de"
AUTH0_AUTHORIZE = f"{AUTH0_DOMAIN}/authorize"
AUTH0_TOKEN = f"{AUTH0_DOMAIN}/oauth/token"
AUTH0_LOGOUT = f"{AUTH0_DOMAIN}/v2/logout"
# Diese Werte sind im offiziellen Android-Client hartkodiert (nicht
# geheim – die mobile App ist im Play Store herunterladbar).
CLIENT_ID = "uV5j90myVPc2XzEOFuWUD2At17OACEGQ"
REDIRECT_URI = "https://login.kleinanzeigen.de/android/com.ebay.kleinanzeigen/callback"
SCOPE = "openid email profile offline_access"
# Auch die Basic-Auth-Daten der App sind öffentlich (sie stecken im APK).
# Wenn die CDN-Antwort 401/403 wird, muss hier ggf. rotiert werden.
BASIC_AUTH = ("android", "TaR60pEttY")
APP_VERSION = "2026.25.0"

SESSION_FILENAME = "session.json"


# ---------- Hilfsfunktionen ----------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _make_pkce() -> tuple[str, str]:
    """Erzeugt (verifier, challenge)."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _session_path() -> str:
    override = os.environ.get("KLEINANZEIGEN_SESSION_PATH")
    if override:
        return override
    return os.path.join(config_path(), SESSION_FILENAME)


def _ensure_secret_file(path: str) -> None:
    """Stellt sicher, dass die Session-Datei nur vom Besitzer lesbar ist."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows: kein chmod, ignorieren.
        pass


# ---------- Session (Token-Datei) ----------


class NotLoggedIn(RuntimeError):
    """Wird ausgelöst, wenn eine chat-Operation ohne aktive Session läuft."""


class Session:
    """Liest und schreibt die Login-Session (Refresh-Token + E-Mail).

    Persistiert wird ausschließlich ``refresh_token`` (manchmal auch
    ``email``, falls beim Login abrufbar) — Passwörter werden **nie**
    gespeichert.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _session_path()
        self._t: dict = self._load()

    # --- Persistence ----------------------------------------------------- #

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
        # Fallback: expliziter Token via env (für CI / Container).
        rt = os.environ.get("KLEINANZEIGEN_REFRESH_TOKEN")
        if rt:
            return {"refresh_token": rt}
        return {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._t, fh, indent=2)
        os.replace(tmp, self.path)
        _ensure_secret_file(self.path)

    def clear(self) -> None:
        self._t = {}
        try:
            os.remove(self.path)
        except OSError:
            pass

    # --- State ----------------------------------------------------------- #

    @property
    def logged_in(self) -> bool:
        return bool(self._t.get("refresh_token"))

    @property
    def email(self) -> Optional[str]:
        return self._t.get("email")

    @property
    def refresh_token(self) -> Optional[str]:
        return self._t.get("refresh_token")

    @property
    def access_token(self) -> Optional[str]:
        return self._t.get("access_token")

    @property
    def user_id(self) -> Optional[str]:
        return self._t.get("user_id")

    def set_refresh_token(self, token: str, email: Optional[str] = None) -> None:
        """Setzt den Refresh-Token (z. B. nach externem Login)."""
        self._t["refresh_token"] = token
        if email:
            self._t["email"] = email
        self.save()

    def set_user_id(self, user_id: str) -> None:
        self._t["user_id"] = str(user_id)
        self.save()

    # --- Auth0-Token holen --------------------------------------------- #

    def _exchange(self, payload: dict, *, timeout: float = 30.0) -> dict:
        """POST /oauth/token und gibt das JSON-Antwort-Dict zurück.

        Wirft ``RuntimeError`` bei Nicht-200-Antwort (mit Body fürs Log).
        """
        try:
            r = requests.post(
                AUTH0_TOKEN,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "User-Agent": f"Kleinanzeigen/{APP_VERSION} (Android 13; Pixel 7)",
                },
                timeout=timeout,
            )
        except requests.RequestException as e:
            raise RuntimeError(f"Auth0 /oauth/token: {e}") from e
        if r.status_code != 200:
            raise RuntimeError(
                f"Auth0 /oauth/token → {r.status_code}: {r.text[:300]}"
            )
        return r.json()

    def get_access_token(self) -> str:
        """Liefert einen frischen Access-Token (refresh nötigenfalls)."""
        if not self.logged_in:
            raise NotLoggedIn(
                "Keine Session. Bitte zuerst `kleinanzeigen login` aufrufen "
                "oder KLEINANZEIGEN_REFRESH_TOKEN setzen."
            )
        now = time.time()
        if (
            self._t.get("access_token")
            and now < self._t.get("expires_at", 0) - 60
        ):
            return self._t["access_token"]
        data = self._exchange(
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": self._t["refresh_token"],
            }
        )
        self._t["access_token"] = data["access_token"]
        self._t["expires_at"] = now + int(data.get("expires_in", 600))
        if data.get("refresh_token"):
            # Auth0 rotiert den Refresh-Token manchmal → neuen merken.
            self._t["refresh_token"] = data["refresh_token"]
        # id_token enthält die E-Mail im Payload.
        idt = data.get("id_token")
        if idt:
            try:
                payload_b64 = idt.split(".")[1]
                payload_b64 += "=" * (-len(payload_b64) % 4)
                claims = json.loads(
                    base64.urlsafe_b64decode(payload_b64.encode("ascii"))
                )
                if claims.get("email"):
                    self._t["email"] = claims["email"]
            except Exception:
                pass
        self.save()
        return self._t["access_token"]

    def auth_headers(self) -> dict:
        """Header für authentifizierte API-Calls."""
        token = self.get_access_token()
        h = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": f"Kleinanzeigen/{APP_VERSION} (Android 13; Pixel 7)",
        }
        if self.email:
            h["X-ECG-Authorization-User"] = f"email={self.email},access={token}"
        return h

    # --- OAuth-Authorization-Code-URL ----------------------------------- #

    def build_authorize_url(self) -> tuple[str, str, str]:
        """Erzeugt (authorize_url, code_verifier, state)."""
        verifier, challenge = _make_pkce()
        state = _b64url(secrets.token_bytes(16))
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        return f"{AUTH0_AUTHORIZE}?{urlencode(params)}", verifier, state

    def complete_login(self, redirect_url: str, code_verifier: str) -> dict:
        """Tauscht den ``?code=…``-Redirect gegen Access+Refresh-Token."""
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(redirect_url.strip()).query)
        if "error" in qs:
            raise RuntimeError(
                f"Auth0-Fehler: {qs.get('error', [''])[0]} – "
                f"{qs.get('error_description', [''])[0]}"
            )
        code = (qs.get("code") or [None])[0]
        if not code:
            raise ValueError(
                "Kein ?code= in der URL gefunden. Bitte die *vollständige* URL "
                "aus der Adresszeile nach dem Login kopieren."
            )
        data = self._exchange(
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": REDIRECT_URI,
            }
        )
        self._t["access_token"] = data["access_token"]
        self._t["refresh_token"] = data["refresh_token"]
        self._t["expires_at"] = time.time() + int(data.get("expires_in", 600))
        idt = data.get("id_token")
        if idt:
            try:
                payload_b64 = idt.split(".")[1]
                payload_b64 += "=" * (-len(payload_b64) % 4)
                claims = json.loads(
                    base64.urlsafe_b64decode(payload_b64.encode("ascii"))
                )
                if claims.get("email"):
                    self._t["email"] = claims["email"]
            except Exception:
                pass
        self.save()
        return data
