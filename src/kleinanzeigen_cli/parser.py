"""HTML-Parser für kleinanzeigen.de.

Strategien (Smoke-getestet am 2026-10-13):

* Liste: jede Karte ist `<article data-adid="…">`. Die wichtigsten Felder
  liegen in einem eingebetteten `<script type="application/ld+json">…</script>`
  (Titel + Beschreibung). Preis, Ort, Versand und Versender-Typ liegen im
  Karten-HTML, gefiltert über `<span>`-Text innerhalb der Karte.

* Detail: der Titel steht im JSON-LD; Preis in `<p class="boxedarticle--price">…</p>`;
  Beschreibung in JSON-LD + `<section class="adnote">`/`.adnote--content`.

Wenn die Seite ihre Struktur ändert, liefern die Parser ParseError.
"""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http_client import ParseError

BASE = "https://www.kleinanzeigen.de"

_PRICE_RE = re.compile(r"(\d[\d.,]*)\s*(?:&euro;|€)")
_PLZ_RE = re.compile(r"\b(\d{5})\s+([A-Za-zäöüÄÖÜß][A-Za-zäöüÄÖÜß \-/]{1,40})\b")
_KAT_RE = re.compile(r"/(s-[a-z0-9\-]+)/")


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _text(node) -> str:
    if node is None:
        return ""
    return _norm_ws(node.get_text(" "))


def _eur_to_int(s: str) -> Optional[int]:
    if not s:
        return None
    m = _PRICE_RE.search(s)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", "")
    try:
        return int(raw)
    except ValueError:
        return None


def _first_json_ld(article_html: str) -> Optional[dict]:
    """Liefert das erste parsebare JSON-LD aus einem HTML-Snippet."""
    for m in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.+?)</script>',
        article_html,
        re.DOTALL,
    ):
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict):
                # echte Karten-JSON-LD hat einen title und description
                if d.get("title") and d.get("description"):
                    return d
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _category_from_card_text(html: str) -> Optional[str]:
    """Sucht den Pfad /s-{slug}/ in der Karte (Slug-Trail)."""
    m = _KAT_RE.search(html)
    if m:
        return m.group(1)
    return None


def _parse_card_top_spans(card_html: str) -> list[str]:
    """Sammelt direkt unter `<article>` enthaltene Text-Strings (Labels)."""
    soup = BeautifulSoup(card_html, "lxml")
    article = soup.find("article")
    if article is None:
        return []
    snippets: list[str] = []
    for sp in article.find_all("span", recursive=True):
        t = _norm_ws(sp.get_text(" "))
        if not t or len(t) > 120:
            continue
        snippets.append(t)
    return snippets


def parse_card(card_html: str) -> Optional[dict]:
    """Parst eine einzelne <article data-adid=…>-Karte."""
    m = re.search(r'data-adid="(\d+)"', card_html)
    if not m:
        return None
    adid = m.group(1)
    jld = _first_json_ld(card_html) or {}
    title = jld.get("title") or None
    description = jld.get("description") or None
    spans = _parse_card_top_spans(card_html)

    # Preis: erste sinnvolle Zahl mit €
    price = None
    for t in spans + [card_html]:
        p = _eur_to_int(t)
        if p is not None:
            price = p
            break

    # Ort (PLZ + Ort)
    ort = None
    plz = None
    for t in spans:
        mm = _PLZ_RE.search(t)
        if mm:
            plz = mm.group(1)
            ort = _norm_ws(mm.group(2).rstrip(" ,|"))
            break

    # Versand
    versand = False
    for t in spans:
        if "Versand" in t and ("möglich" in t.lower() or "versand" in t.lower()):
            versand = True
            break

    # Versender-Typ ("privater Nutzer" / "gewerblicher Verkäufer")
    sender_type = None
    for t in spans:
        if "privater Nutzer" in t or "Privater Nutzer" in t:
            sender_type = "privat"
            break
        if "gewerblicher" in t.lower():
            sender_type = "gewerblich"
            break

    # Kategorie-Pfad
    kategorie_pfad = _category_from_card_text(card_html)

    # URL zur Detailseite
    href_m = re.search(r'href="(/s-anzeige/[^"]+)"', card_html)
    detail_href = href_m.group(1) if href_m else None
    detail_abs_url = urljoin(BASE, detail_href) if detail_href else None

    return {
        "id": adid,
        "url": detail_abs_url or f"{BASE}/s-anzeige/{adid}.html",
        "slug": _slug_from_url(detail_href or "") if detail_href else None,
        "titel": title,
        "preis_euro": price,
        "ort": ort,
        "plz": plz,
        "versand_moeglich": versand,
        "versender_typ": sender_type,
        "kategorie_pfad": kategorie_pfad,
        "beschreibung_kurz": (description[:240] if description else None),
    }


def parse_search(html: str, *, limit: int = 25) -> list[dict]:
    """Parst die gesamte Ergebnisseite. Liefert max. `limit` Karten."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    seen: set[str] = set()
    for art in soup.find_all("article", attrs={"data-adid": True}):
        h = art.decode()  # HTML-String der Karte
        parsed = parse_card(h)
        if not parsed:
            continue
        if parsed["id"] in seen:
            continue
        seen.add(parsed["id"])
        results.append(parsed)
        if len(results) >= limit:
            break
    if not results:
        # vereinzelt liefert die Seite ein leeres Layout ohne <article>
        # Wir werfen ParseError, damit der Aufrufer entscheiden kann.
        raise ParseError("keine <article data-adid=…>-Karten gefunden", url=BASE)
    return results


def _slug_from_url(url: str) -> Optional[str]:
    m = re.search(r"/s-anzeige/([^/]+)/", url or "")
    return m.group(1) if m else None


def parse_detail(html: str, *, adid: str) -> dict:
    """Parst eine Detailseite zu einer Anzeige.

    Die `adid` aus dem Aufrufer hat Vorrang – wir verwenden die im HTML
    gefundene `data-adid` nur, wenn die URL selbst keine enthält
    (manche Detailseiten listen unten ähnliche Anzeigen mit eigener
    `data-adid`).
    """
    soup = BeautifulSoup(html, "lxml")
    # ID: erst aus dem URL-Pfad der kanonischen URL, sonst aus data-adid,
    # sonst die übergebene ID.
    canonical = soup.find("link", rel="canonical")
    canon_url = canonical["href"] if canonical and canonical.get("href") else None
    url_id = None
    if canon_url:
        m_id = re.search(r"/s-anzeige/(?:[^/]+/)?(\d+)-", canon_url)
        if m_id:
            url_id = m_id.group(1)
    if not url_id:
        m = re.search(r'data-adid="(\d+)"', html)
        url_id = m.group(1) if m else adid
    final_id = url_id or adid
    url = canon_url or f"{BASE}/s-anzeige/{adid}.html"

    # Titel aus JSON-LD (erstes echtes ad-JSON-LD)
    title = None
    description = None
    for jm in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.+?)</script>',
        html,
        re.DOTALL,
    ):
        try:
            d = json.loads(jm.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        
        if (
            isinstance(d, dict)
            and d.get("title")
            and d.get("description")
            and not title
        ):
            # wir nehmen das erste, das description + title hat
            title = d.get("title")
            description = d.get("description")
            break
    # Fallback: h1
    if not title:
        h1 = soup.find("h1")
        title = _norm_ws(h1.get_text(" ")) if h1 else None

    # Preis
    price = None
    p_node = soup.find("p", class_=re.compile(r"boxedarticle--price"))
    if p_node:
        price = _eur_to_int(p_node.get_text(" "))
    if price is None:
        # zweiter Versuch: erste €-Zahl im HTML, die im Title-Bereich steht
        m2 = re.search(
            r'class="boxedarticle--price"[^>]*>.{0,80}?(\d[\d.,]*)\s*(?:&euro;|€)',
            html,
            re.DOTALL,
        )
        if m2:
            price = _eur_to_int(m2.group(1))

    # PLZ + Ort (Headline-Bereich)
    ort = None
    plz = None
    for m in re.finditer(_PLZ_RE, html):
        # erste plausible Adresse
        plz = m.group(1)
        ort = _norm_ws(m.group(2).rstrip(" ,|"))
        break

    # Versand
    versand = bool(
        re.search(r"Versand\s*möglich", html, re.I)
        or re.search(r"<span[^>]*>Versand möglich</span>", html)
    )

    # Versender-Typ
    sender_type = None
    if re.search(r"privater Nutzer", html, re.I):
        sender_type = "privat"
    elif re.search(r"gewerblicher Verkäufer", html, re.I):
        sender_type = "gewerblich"

    # Beschreibung (Detailtext)
    beschreibung = description
    if not beschreibung or len(beschreibung) < 120:
        # oft gibt es eine #descriptionText-section
        dt = soup.find(id="descriptionText") or soup.find(
            "div", class_=re.compile(r"adnote--content|description|ad-description")
        )
        if dt:
            t = _norm_ws(dt.get_text("\n"))
            if len(t) > 80:
                beschreibung = t

    return {
        "id": final_id,
        "url": url,
        "titel": title,
        "preis_euro": price,
        "ort": ort,
        "plz": plz,
        "versand_moeglich": versand,
        "versender_typ": sender_type,
        "beschreibung_text": beschreibung,
    }
