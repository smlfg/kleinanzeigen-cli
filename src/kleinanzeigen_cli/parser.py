"""HTML-Parser für kleinanzeigen.de.

Strategien (Smoke-getestet am 2026-10-13):

* Liste: jede Karte ist `<article data-adid="…">`. Die wichtigsten Felder
  liegen in einem eingebetteten `<script type="application/ld+json">…</script>`
  (Titel + Beschreibung). Preis, Ort und Versand liegen im Karten-HTML;
  der Versender-Typ steht seit dem Layout-Update (2026) nicht mehr als
  Span-Text in der Karte, sondern im eingebetteten RSC-Payload der Seite
  (`posterType`: PRIVATE/COMMERCIAL, keyed über `seoLink`/Anzeigen-ID).

* Detail: der Titel steht im JSON-LD; Preis in
  `<h2 class="boxedarticle--price">…</h2>` (Live ab ~2026, früher `<p>`);
  Kategorie im Breadcrumb `#vap-brdcrmb`; Beschreibung in JSON-LD +
  `<section class="adnote">`/`.adnote--content`.

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

# RSC-Flight-Daten der Suchseite: posterType je Anzeige (Live ab ~2026-10).
_POSTER_TYPE_RE = re.compile(
    r'(?:&quot;|")seoLink(?:&quot;|"):\[0,(?:&quot;|")/s-anzeige/[^/]+/(\d+)-[^&"]+(?:&quot;|")\].{0,280}?'
    r'(?:&quot;|")posterType(?:&quot;|"):\[0,(?:&quot;|")(PRIVATE|COMMERCIAL)(?:&quot;|")\]',
    re.DOTALL,
)

# Marker für Preis-Typen, die im Preisfeld der Live-Site stehen.
# Reihenfolge ist relevant: "VB" muss vor "verschenken" geprüft werden
# (ein "Zu verschenken VB" ist ungewöhnlich, aber "VB" als alleiniges
# Wort heißt "Verhandlungsbasis").
_PRICE_TYPE_MARKERS: list[tuple[str, str]] = [
    (r"\bzu\s+verschenken\b", "verschenken"),
    (r"\bverschenken\b", "verschenken"),
    (r"\bpreis\s+auf\s+anfrage\b", "anfrage"),
    (r"\bauf\s+anfrage\b", "anfrage"),
    (r"\bvb\b", "vb"),
    (r"\btausch\b|\bzu\s+tauschen\b|\btausche\b", "tausch"),
]


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


def _category_from_detail_html(html: str) -> Optional[str]:
    """Liest die tiefste Kategorie aus dem VIP-Breadcrumb."""
    m = re.search(r'id="vap-brdcrmb"[^>]*>.*?</div>', html, re.DOTALL | re.I)
    if not m:
        return _category_from_card_text(html)
    cats = re.findall(r'href="/(s-[a-z0-9\-]+)/c\d+"', m.group(0))
    return cats[-1] if cats else None


def _detail_price_text(html: str, soup: BeautifulSoup) -> Optional[str]:
    """Preistext der Detailseite (h2 oder p mit ``boxedarticle--price``)."""
    node = soup.find(class_=re.compile(r"boxedarticle--price"))
    if node:
        return _norm_ws(node.get_text(" "))
    m = re.search(r'class="boxedarticle--price"[^>]*>([^<]+)', html)
    if m:
        return _norm_ws(m.group(1))
    return None


def _price_field(card_html: str) -> Optional[str]:
    """Liest den angezeigten Preistext direkt aus dem Preisfeld.

    Auf der Live-Site (Stand 2026-10-13) ist das immer
    `<p class="…text-secondary…">…</p>` – Beispiele:
    ``1.690 €``, ``699 € VB``, ``VB``, ``Zu verschenken``,
    ``Preis auf Anfrage``.

    Im synthetischen Test-Fixture ist es
    ``<p class="aditem-price">…</p>``. Wir akzeptieren beides.

    Wir nehmen den **ersten** Treffer – das Preisfeld steht im DOM
    vor dem Versand/Versender-Bereich.
    """
    m = re.search(
        r'<p[^>]*class="[^"]*(?:text-secondary|aditem-price)[^"]*"[^>]*>([^<]+)</p>',
        card_html,
    )
    if not m:
        return None
    return _norm_ws(m.group(1))


def _price_type(price_text: str) -> str:
    """Leitet aus dem Preistext den Preis-Typ ab.

    Liefert genau einen von: ``fest``, ``vb``, ``verschenken``,
    ``anfrage``, ``tausch``. ``fest`` = konkrete Zahl + €; alles
    ohne Zahl ist ein Marker-Typ.
    """
    if not price_text:
        return "fest"
    # Marker zuerst (Reihenfolge wichtig: "verschenken" vor "vb")
    low = price_text.lower()
    for pat, typ in _PRICE_TYPE_MARKERS:
        if re.search(pat, low):
            return typ
    # Sonst: wenn eine Euro-Zahl drinsteht, ist es ein fester Preis.
    if _PRICE_RE.search(price_text):
        return "fest"
    # Unbekannter Text ohne €-Zahl → konservativ "fest" (kein
    # Marker bekannt). None wäre fürs JSON-Output unhandlich.
    return "fest"


def _poster_types_from_search_html(html: str) -> dict[str, str]:
    """Liest Versender-Typen aus dem Seiten-Payload (RSC), keyed by Anzeigen-ID."""
    out: dict[str, str] = {}
    for adid, poster in _POSTER_TYPE_RE.findall(html):
        if poster == "PRIVATE":
            out[adid] = "privat"
        elif poster == "COMMERCIAL":
            out[adid] = "gewerblich"
    return out


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

    # Preis: direkt aus dem Preisfeld (robuster als Full-Text-Fallback,
    # der bei Anzeigen-IDs und Slugs in die Irre laufen kann).
    # Liefert z.B. "1.690 €", "699 € VB", "VB", "Zu verschenken".
    price_field = _price_field(card_html)
    if price_field is None:
        # Die Karte hat überhaupt kein Preisfeld (z.B. reine Tausch-
        # Anzeige oder kaputter Eintrag). Wir liefern None für beides.
        price = None
        price_type = None
    else:
        price = _eur_to_int(price_field)
        price_type = _price_type(price_field)

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
        "preis_typ": price_type,
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
    poster_types = _poster_types_from_search_html(html)
    results: list[dict] = []
    seen: set[str] = set()
    for art in soup.find_all("article", attrs={"data-adid": True}):
        h = art.decode()  # HTML-String der Karte
        parsed = parse_card(h)
        if not parsed:
            continue
        if parsed.get("versender_typ") is None:
            parsed["versender_typ"] = poster_types.get(parsed["id"])
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

    # Preis + Preis-Typ (gleiche Logik wie Suchkarten)
    price_field = _detail_price_text(html, soup)
    if price_field is None:
        price = None
        price_type = None
    else:
        price = _eur_to_int(price_field)
        price_type = _price_type(price_field)

    kategorie_pfad = _category_from_detail_html(html)

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
        "preis_typ": price_type,
        "ort": ort,
        "plz": plz,
        "versand_moeglich": versand,
        "versender_typ": sender_type,
        "kategorie_pfad": kategorie_pfad,
        "beschreibung_kurz": (description[:240] if description else None),
        "beschreibung_text": beschreibung,
    }
