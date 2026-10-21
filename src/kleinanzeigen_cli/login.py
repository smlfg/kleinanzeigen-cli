"""Browser-Login via Playwright + Auth0 (Authorization Code + PKCE).

Das ist die **interaktive** Variante: bei Captcha oder 2FA muss der
Benutzer eingreifen (mit ``--headed``). In headless-Umgebungen ohne
Browser ist es oft einfacher, einen vorhandenen Refresh-Token via
``KLEINANZEIGEN_REFRESH_TOKEN`` zu setzen und den Login zu überspringen.
"""
from __future__ import annotations

import re
import sys
from typing import Optional

from .auth import Session


def _print(msg: str) -> None:
    """Statusmeldungen gehen auf stderr – stdout bleibt für JSON reserviert."""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _has_playwright() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def browser_login(
    session: Session,
    *,
    headed: bool = False,
    timeout: float = 120.0,
) -> str:
    """Öffnet Playwright, startet den Auth0-Flow und gibt den ``code`` zurück.

    Erwartet, dass der Verifier zusammen mit dem URL in derselben
    Browser-Session gebraucht wird; deshalb wird der Verifier hier
    erzeugt und über den Closure dem Caller übergeben.
    """
    if not _has_playwright():
        raise RuntimeError(
            "playwright ist nicht installiert. Installiere via "
            "`pip install playwright && playwright install chromium`."
        )
    from playwright.sync_api import sync_playwright

    auth_url, verifier, state = session.build_authorize_url()
    _print(f"Login-URL: {auth_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
            locale="de-DE",
        )
        page = ctx.new_page()
        page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_url(
                re.compile(r"code=|error=|access_denied|ka-login:"),
                timeout=int(timeout * 1000),
            )
        except Exception as e:
            body = page.content().lower()
            if "captcha" in body or "turnstile" in body:
                raise RuntimeError(
                    "Captcha erkannt. Bitte erneut mit `kleinanzeigen login "
                    "--headed` versuchen (Browser sichtbar – dort manuell lösen)."
                ) from e
            raise RuntimeError(
                f"Login-Timeout: {e!s}. Bei wiederholten Fehlschlägen "
                "vermutlich Captcha/2FA – mit --headed wiederholen."
            ) from e
        final_url = page.url
        browser.close()

    m = re.search(r"[?&]code=([^&]+)", final_url)
    if not m:
        m_err = re.search(r"error=([^&]+)", final_url)
        err = m_err.group(1) if m_err else "unknown"
        raise RuntimeError(
            f"Login fehlgeschlagen (Auth0: {err}). Bitte erneut versuchen."
        )
    # Wir geben code + verifier zurück, weil Session-Instanzen ihren
    # internen Verifier nicht halten.
    return m.group(1)


def perform_login(
    *, headed: bool = False, timeout: float = 120.0
) -> dict:
    """Vollständiger Login-Flow: Browser-Login + Token-Tausch."""
    session = Session()
    # Verifier muss erzeugt und direkt verwendet werden.
    auth_url, verifier, _state = session.build_authorize_url()
    from playwright.sync_api import sync_playwright
    import re as _re

    if not _has_playwright():
        raise RuntimeError(
            "playwright fehlt – installiere via `pip install playwright`."
        )
    _print(f"Login-URL: {auth_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
            locale="de-DE",
        )
        page = ctx.new_page()
        page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_url(
                _re.compile(r"code=|error=|access_denied|ka-login:"),
                timeout=int(timeout * 1000),
            )
        except Exception as e:
            body = page.content().lower()
            if "captcha" in body or "turnstile" in body:
                raise RuntimeError(
                    "Captcha erkannt – bitte erneut mit `login --headed` "
                    "versuchen, dort kannst du es manuell lösen."
                ) from e
            raise RuntimeError(
                f"Login-Timeout: {e!s}. Bei wiederholten Fehlschlägen "
                "wahrscheinlich Captcha/2FA – mit --headed wiederholen."
            ) from e
        final_url = page.url
        browser.close()
    m = _re.search(r"[?&]code=([^&]+)", final_url)
    if not m:
        m_err = _re.search(r"error=([^&]+)", final_url)
        err = m_err.group(1) if m_err else "unknown"
        raise RuntimeError(
            f"Login fehlgeschlagen (Auth0: {err})."
        )
    session.complete_login(final_url, verifier)
    return {
        "logged_in": True,
        "email": session.email,
        "session_file": session.path,
    }
