# kleinanzeigen-cli

`kleinanzeigen` ist eine Kommandozeilen-Schnittstelle für
https://www.kleinanzeigen.de, gedacht für KI-Agenten und Menschen:

- **Suche** (öffentlich, kein Login): Stichwortsuche, Kategorie, Ort +
  Umkreis, Preis min/max, Angebot/Gesuch, privat/gewerblich, Versand,
  Sortierung.
- **Anzeigen-Details** (öffentlich): Titel, Preis, Ort, Beschreibung,
  Versender-Typ.
- **Kategorien**: kuratierte Liste der wichtigsten Slugs (Auto,
  Elektronik, Möbel, Immobilien …).
- **Lokaler Suchauftrag** (`watch`): nur die **IDs neuer** Anzeigen
  werden in `~/.local/state/kleinanzeigen-cli/watch/<name>.json`
  gemerkt; es wird **nichts** nach außen geschickt.

Unterscheidung zu bestehenden Scrappern:

- Stabile JSON-Schnittstelle (`--json`) für jeden Subbefehl.
- **Nur Lesezugriffe**: kein Login, keine Cookies, kein Senden.
- **Höflich**: expliziter User-Agent + 0,6 s Pause zwischen Requests;
  keine unnötigen Header (kein `br`).
- **Tests ohne Netz**: Pytest-Suite mit synthetischen HTML-Fixtures
  (kein Site-Roundtrip); gegen kleinanzeigen.de wird nur optional live
  gesmoket.

## Installation

```bash
pip install kleinanzeigen-cli
# oder aus dem Repo:
pip install .
```

Danach steht `kleinanzeigen` zur Verfügung.

## Befehle

```text
kleinanzeigen search [--query "iPhone 12"]
                     [--category handy-telefon | c173]
                     [--location 3331] [--radius 50]
                     [--min-price 20] [--max-price 500]
                     [--offer-type ANZEIGE|GESUCH]
                     [--sender-type PRIVAT|GEWERBLICH]
                     [--only-shipping] [--only-private] [--only-commercial]
                     [--sorting PRICE_ASCENDING|PRICE_DESCENDING|
                                 DATE_ASCENDING|DATE_DESCENDING|
                                 DISTANCE_ASCENDING]
                     [--page 2] [--limit 25] [--json]
kleinanzeigen show  AD_ID | /s-anzeige/.../ID-… | https://www.kleinanzeigen.de/…  [--json]
kleinanzeigen categories [--json]
kleinanzeigen watch  --name NAME [--query …] [--category …] [--location …]
                     [--radius …] [--min-price …] [--max-price …]
                     [--offer-type …] [--sender-type …]
                     [--only-shipping] [--sorting …]
                     [--limit 25] [--json]
kleinanzeigen --version | --help
```

`--location` ist die numerische kleinanzeigen-Location-ID (z. B.
`3331` für Berlin). Beliebte IDs findest du, indem du auf
kleinanzeigen.de einen Ort in der Sidebar auswählst und den Wert aus
der URL kopierst.

## Beispiele

```bash
# iPhone 12 in Berlin, zwischen 20 und 500 €, nur privat, mit Versand
kleinanzeigen search --query "iPhone 12" --location 3331 --radius 50 \
    --min-price 20 --max-price 500 --only-private --only-shipping --json

# Alle Anzeigen in "Handy & Telefon" (c173) – Hamburg – Umkreis 20 km
kleinanzeigen search --category handy-telefon --location 2009 --radius 20 --json

# Detail zu einer Anzeige
kleinanzeigen show 2001234567 --json
kleinanzeigen show /s-anzeige/iphone-12/2001234567-x-1.html --json

# Verfügbare Kategorien
kleinanzeigen categories --json

# Lokaler Suchauftrag: neue iPhones in München melden (kein Push!)
kleinanzeigen watch --name "muenchen-iphone" \
    --query "iPhone" --location 8031 --radius 30 --json
```

## Ausgabe (mit `--json`)

- `search`: `{ quelle, filter, url, anzahl, anzeigen: [...] }`
- `show`:   `{ quelle, anzeige: {...} }`
- `categories`: `{ quelle, anzahl, kategorien: [...] }`
- `watch`:  `{ quelle, watch_name, url, anzahl_aktuell, anzahl_neu,
  neue_anzeigen: [...], alle_ids: [...] }`

Jede Anzeige enthält mindestens `id`, `url`, `titel`, `preis_euro`
(kann `null` sein bei „VB"), `ort`, `plz`, `versand_moeglich`,
`versender_typ`, `kategorie_pfad`, `beschreibung_kurz`. Auf der
Detailseite zusätzlich `beschreibung_text`.

## Exit-Codes

| Code | Bedeutung                                          |
|-----:|----------------------------------------------------|
| 0    | OK                                                 |
| 1    | Ungültige Argumente / unbekannte ID                |
| 2    | (reserviert – kein Login im read-only-Modus)       |
| 3    | Netzwerk- / HTTP-Fehler                            |
| 4    | Parse-Fehler                                       |
| 5    | (reserviert – kein Send im read-only-Modus)        |

Fehler werden als einzeiliges JSON-Objekt auf **stderr** geschrieben
(`{"error": "...", ...}`).

## Höflichkeit

`kleinanzeigen-cli` schickt einen ehrlichen User-Agent
(`kleinanzeigen-cli/0.1.0 (+https://github.com/smlfg/kleinanzeigen-cli; …)`),
hält 0,6 s Pause zwischen zwei Requests, fragt keine unnötigen Ressourcen
nach und folgt Redirects defensiv (`Allow-Encoding: gzip, deflate`,
kein `br`).

## Tests

```bash
pip install -e ".[dev]"
python -m pytest -q
```

Die Tests sind netz-frei: die HTML wird in `tests/fixtures/synthetic.py`
zusammengebaut und über den `parser` gefüttert; die Client-Filter
werden gegen deterministische Karten-IDs geprüft.

## Rechtliches

Diese Software ist ein privates Lernprojekt und **nicht** mit
kleinanzeigen.de verbunden. Lesezugriffe sind in Deutschland nach
§ 87b UrhG für Suchmaschinen und vergleichbare Anbieter zulässig –
das ersetzt keine Rechtsberatung. Die Bibliothek schreibt keine
Cookies, loggt nichts und macht keine Accounts.

## Lizenz

MIT