"""Synthetische HTML-Fixtures für kleinanzeigen-cli-Tests.

Wir bauen das Schema nach, das der Parser tatsächlich beobachtet
(<article data-adid="…"> + JSON-LD + Span-Labels + Detailseite mit
<p class="boxedarticle--price">), aber mit kleinen, deterministischen
Inhalten. So müssen wir die Site nicht für jeden Test erreichen.
"""

from __future__ import annotations

from html import escape


def _card(adid: str, *, title: str, plz: str, ort: str,
          versand: bool, sender: str, beschreibung: str,
          slug_path: str = "s-handy-telefon",
          price: int | None = None,
          price_label: str | None = None,
          has_price_field: bool = True) -> str:
    """Baut eine <article data-adid=…>-Karte.

    `price_label` (optional) überschreibt den angezeigten Preistext. Wenn
    gesetzt, wird der Wert 1:1 in das `<p class="aditem-price">`
    geschrieben (typische Live-Varianten: "VB", "Zu verschenken",
    "1 € VB", "Preis auf Anfrage", "1.299 €"). `price` (int|None) wird
    dann ignoriert – er wird aus `price_label` abgeleitet, falls eine
    Zahl drin steht.

    `has_price_field=False` lässt das Preisfeld weg (Live-Sonderfall:
    reine Tausch-/Gesuch-Anzeigen haben teils kein Preisfeld).
    """
    sender_label = "Privater Nutzer" if sender == "privat" else "Gewerblicher Verkäufer"
    versand_label = '<span class="badge-shipping">Versand möglich</span>' if versand else ""
    if not has_price_field:
        price_html = ""
    elif price_label is not None:
        # genau so, wie die Live-Site das Feld rendert
        price_html = f'<p class="aditem-price">{price_label}</p>'
    elif price is not None:
        price_html = f'<p class="aditem-price">{price} €</p>'
    else:
        price_html = '<p class="aditem-price">VB</p>'
    jld = {
        "@context": "https://schema.org",
        "@type": "Product",
        "title": title,
        "description": beschreibung,
        "name": title,
    }
    import json as _json
    jld_html = (
        '<script type="application/ld+json">'
        + _json.dumps(jld, ensure_ascii=False)
        + '</script>'
    )
    # Eindeutiger Suffix, damit jede href anders ist (kein kosmetischer
    # Defaultwert im f-string).
    href_suffix = adid[-3:]
    return f"""
<article class="aditem" data-adid="{adid}">
  <h2 class="text-module-begin">
    <a class="ellipsis" href="/s-anzeige/{slug_path}/{adid}-{href_suffix}.html">{escape(title)}</a>
  </h2>
  {price_html}
  <div class="aditem-main">
    <span class="aditem-location">{plz} {escape(ort)}</span>
    <span class="aditem-details">{versand_label}</span>
  </div>
  <span class="aditem-addon">{sender_label}</span>
  {jld_html}
</article>
"""


def _ad(adid: int, **kw) -> str:
    return _card(str(adid), **kw)


SYNTHETIC_SEARCH = f"""<!DOCTYPE html>
<html lang="de">
<head><title>Synth</title></head>
<body>
<main id="srchrslt-content">
  <ul class="item-list">
{_ad(2001, title="iPhone 12 64GB schwarz",        plz="10115", ort="Berlin Mitte",         price=200, versand=True,  sender="privat",     beschreibung="Top gepflegt, Originalakten Tasche dabei.")}
{_ad(2002, title="iPhone 12 Pro 256GB blau",      plz="10405", ort="Berlin Prenzlauer Berg", price=420, versand=True,  sender="gewerblich", beschreibung="Händler mit MwSt, 1 Jahr Garantie.")}
{_ad(2003, title="iPhone 11 defekt zum Basteln",  plz="20095", ort="Hamburg Altstadt",      price=80,  versand=False, sender="privat",     beschreibung="Display kaputt, Rest geht. Nur Selbstabholung.")}
{_ad(2004, title="iPhone 13 mini 128GB",          plz="50667", ort="Köln Innenstadt",       price=380, versand=True,  sender="privat",     beschreibung="Kaum benutzt, OVP vorhanden.")}
{_ad(2005, title="Samsung Galaxy S22",            plz="80331", ort="München Zentrum",       price=320, versand=True,  sender="gewerblich", beschreibung="Generalüberholt, 24 Monate Gewährleistung.")}
{_ad(2006, title="Defektes iPhone 12 (Akzeptanz)",  plz="70173", ort="Stuttgart",            price=None, versand=False, sender="privat",   beschreibung="Zu verschenken, geht nicht mehr an.")}
    {_ad(2010, title="MacBook Pro 13 M1 VB-only",      plz="50667", ort="Köln Innenstadt",       price_label="VB",                  versand=True,  sender="privat",     beschreibung="Suche Interessenten.")}
    {_ad(2011, title="MacBook Pro 13 M1 VB-mit-Zahl",  plz="80331", ort="München Zentrum",       price_label="699 € VB",            versand=True,  sender="privat",     beschreibung="Kann auch weniger sein.")}
    {_ad(2012, title="iPhone 12 (zu verschenken)",     plz="20095", ort="Hamburg Altstadt",      price_label="Zu verschenken",      versand=False, sender="privat",     beschreibung="Verschenke das Gerät.")}
    {_ad(2013, title="iPhone 12 Pro Max",              plz="10115", ort="Berlin Mitte",          price_label="1.299 €",            versand=True,  sender="gewerblich", beschreibung="Tausenderpunkt mit Punkt.")}
    {_ad(2014, title="MacBook Pro 13 Tausch",          plz="10405", ort="Berlin Prenzlauer Berg", price_label="Tausche",            versand=False, sender="privat",     beschreibung="Tausche gegen Roller.")}
    {_ad(2015, title="MacBook Pro ohne Preisfeld",      plz="80331", ort="München Zentrum",       price=None, versand=False, sender="privat",     beschreibung="Nur Tausch, kein Preis.", has_price_field=False)}
  </ul>
</main>
</body>
</html>
"""

SYNTHETIC_DETAIL = """<!DOCTYPE html>
<html lang="de">
<head>
<title>iPhone 12 64GB schwarz in der Homedetail</title>
<link rel="canonical" href="https://www.kleinanzeigen.de/s-anzeige/iphone-12-64gb-schwarz/2001-x-1"/>
</head>
<body>
<article class="aditem" data-adid="2001">
  <h1>iPhone 12 64GB schwarz</h1>
  <p class="boxedarticle--price">200 €</p>
  <section class="adnote">
    <div class="adnote--content" id="descriptionText">
      Top gepflegt, Originaltasche dabei. Nichtraucher, keine Haustiere.
      Versand möglich oder Selbstabholung in Berlin.
    </div>
  </section>
  <div class="boxedarticle--details">
    <span class="detail-address">10115 Berlin Mitte</span>
  </div>
  <div class="boxedarticle--contact">
    <span>Privater Nutzer</span>
  </div>
  <span class="boxedarticle--shipping">Versand möglich</span>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Product",
    "title": "iPhone 12 64GB schwarz",
    "description": "Top gepflegt, Originaltasche dabei.",
    "name": "iPhone 12 64GB schwarz"
  }
  </script>
</article>
</body>
</html>
"""