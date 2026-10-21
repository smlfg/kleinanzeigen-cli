"""URL-Bau für kleinanzeigen.de.

Beobachtet 2026-10-13 per Live-Smoke. Standard-Pfad:
    /s-<slug>/k0c{category}l{location}r{radius}
    ?keywords=…&minPrice=…&maxPrice=…&offerType=ANZEIGE|GESUCH
    &senderType=PRIVAT|GEWERBLICH&shipping=true|false
    &sorting=PRICE_ASCENDING|PRICE_DESCENDING|DATE_ASCENDING|…
    /seite:<n>/…  (ab Seite 2; ?page= wird serverseitig ignoriert)

Bei reiner Stichwortsuche (kein Slug) führt die Seite ohnehin auf eine
große Suchseite; wir übergeben einfach den `keywords=`-Parameter.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

DATA_PATH = Path(__file__).resolve().parent / "data" / "categories.json"

BASE = "https://www.kleinanzeigen.de"


def _load() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


# ---- Slug-Helfer ----

_UMLAUT = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)


def slugify(name: str) -> str:
    """Slug für kleinanzeigen: nur Kleinbuchstaben, Ziffern und Bindestriche."""
    s = (name or "").strip().lower().translate(_UMLAUT)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "alles"


def category_id(slug_or_id: str) -> Optional[str]:
    """Liefert die `c{N}`-ID zu einem Slug oder zu `c173` selbst."""
    s = (slug_or_id or "").strip()
    if not s:
        return None
    if re.match(r"^c\d+$", s):
        return s
    data = _load()
    if s in data:
        return data[s]["id"]
    if s.lower() in data:
        return data[s.lower()]["id"]
    for k, v in data.items():
        if k.lower() == s.lower():
            return v["id"]
    return None


def category_name(cid: str) -> Optional[str]:
    for _k, v in _load().items():
        if v["id"] == cid:
            return v["name"]
    return None


def list_categories() -> list[dict]:
    return [{"slug": k, **v} for k, v in _load().items()]


# ---- URL-Bau ----

def build_search_url(
    *,
    category: Optional[str] = None,
    location: Optional[int] = None,
    radius: Optional[int] = None,
    keywords: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    offer_type: Optional[str] = None,  # 'ANZEIGE' / 'GESUCH'
    sender_type: Optional[str] = None,  # 'PRIVAT' / 'GEWERBLICH'
    shipping: Optional[bool] = None,
    sorting: Optional[str] = None,
    page: Optional[int] = None,
    slug: Optional[str] = None,
) -> str:
    """Baut die Such-URL.

    * `category` ist `c173` oder ein Slug ("handy-telefon").
    * `location` ist die numerische Location-ID (z. B. 3331 für Berlin).
    * `radius` in km (5/10/20/50/100/150/200).
    """
    if not category and not slug and not keywords:
        raise ValueError("mindestens category oder keywords wird benötigt")

    # Pfad
    if slug:
        slug_clean = slugify(slug)
    elif category:
        # Wenn der Caller einen Slug aus data genutzt hat, ist das die saubere Variante
        cid = category_id(category)
        if cid and category.lower() in _load():
            slug_clean = slugify(category)
        else:
            slug_clean = slugify(category)
    else:
        slug_clean = slugify(keywords or "")
    path = f"/s-{slug_clean}"

    # k0 + Komponenten
    parts: list[str] = ["k0"]
    if category:
        cid = category_id(category)
        if cid:
            parts.append(cid)
    if location:
        parts.append(f"l{int(location)}")
    if radius:
        parts.append(f"r{int(radius)}")
    k0 = "/" + "".join(parts)
    if page and page > 1:
        path += f"/seite:{int(page)}"
    path += k0

    q: dict[str, str] = {}
    if keywords:
        q["keywords"] = keywords
    if min_price is not None:
        q["minPrice"] = str(int(min_price))
    if max_price is not None:
        q["maxPrice"] = str(int(max_price))
    if offer_type and offer_type.upper() in ("ANZEIGE", "GESUCH"):
        q["offerType"] = offer_type.upper()
    if sender_type and sender_type.upper() in ("PRIVAT", "GEWERBLICH"):
        q["senderType"] = sender_type.upper()
    if shipping is True:
        q["shipping"] = "true"
    if sorting:
        q["sorting"] = sorting

    base = BASE
    if not q:
        return base + path
    qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in q.items())
    return f"{base}{path}?{qs}"


def detail_url(adid: str) -> str:
    """URL, die der Server auf die Anzeige umleitet.

    Beobachtet am 2026-10-13: `/s-anzeige/<ID>.html` (Kurzform ohne
    Slug) antwortet auf der Live-Site mit HTTP 404; die kanonische
    Form `/s-anzeige/<beliebiger-slug>/<ID>-<c>-<l>` antwortet mit
    200 (der Slug wird im Klartext angezeigt, ist aber sonst
    bedeutungslos – der Server sucht nach der ID).
    """
    aid = adid.strip()
    if "/" in aid or aid.endswith(".html"):
        # schon ein Pfad oder eine volle URL → 1:1 zurück
        return aid if aid.startswith("http") else f"{BASE}{aid if aid.startswith('/') else '/' + aid}"
    return f"{BASE}/s-anzeige/x{aid}/{aid}-x-1"


def parse_adid_from_url(s: str) -> Optional[str]:
    """Akzeptiert numerische ID, /s-anzeige/.../<ID>-... oder volle URL."""
    if not s:
        return None
    s = s.strip()
    if s.isdigit():
        return s
    path = s
    if "://" in s:
        path = urlparse(s).path
    m = re.search(r"/s-anzeige/(?:[^/]+/)?(\d+)-", path)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4,})", s)
    if m:
        return m.group(1)
    return None
