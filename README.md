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
- **Login + Chats** (optional, mit Account): Konversationen auflisten,
  lesen und *mit explizitem* `--confirm` antworten.

Unterscheidung zu bestehenden Scrappern:

- Stabile JSON-Schnittstelle (`--json`) für jeden Subbefehl.
- **Read-only by default**: kein Login, keine Cookies, kein Senden.
- **Höflich**: expliziter User-Agent + 0,6 s Pause zwischen Requests;
  keine unnötigen Header (kein `br`).
- **Tests ohne Netz**: Pytest-Suite mit synthetischen HTML- und
  API-Fixtures (kein Site-Roundtrip); gegen kleinanzeigen.de wird nur
  optional live gesmoket.

## Installation

```bash
pip install kleinanzeigen-cli
# oder aus dem Repo:
pip install .
# Optional, nur für den Browser-Login (siehe unten):
pip install ".[playwright]" && playwright install chromium
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

# Login + Chats (nur mit Konto)
kleinanzeigen login  [--headed] [--timeout 120] [--json]
kleinanzeigen logout [--json]
kleinanzeigen chats list  [--page 0] [--size 100] [--json]
kleinanzeigen chats read  <conversation-id> [--json]
kleinanzeigen chats send  <conversation-id>
                         --text "Hallo, ist das noch da?"
                         --confirm
                         [--json]
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

### Login + Chats

Der Login nutzt den offiziellen Auth0-Flow (Authorization Code + PKCE)
derselben Client-Konfiguration wie die Android-App – kein eigenes
Passwort-Hacking. Die Session (nur der **Refresh-Token**, niemals das
Passwort) liegt in `$XDG_CONFIG_HOME/kleinanzeigen-cli/session.json`
mit Modus `0600`.

**Variante 1 – Browser-Login (interaktiv):**

```bash
# headless; nur sinnvoll, wenn Akamai / Cloudflare den Bot durchlässt
kleinanzeigen login --json
# Captcha oder 2FA verlangt sichtbaren Browser:
kleinanzeigen login --headed
```

**Variante 2 – vorhandener Refresh-Token (CI, Container, anderer Rechner):**

```bash
export KLEINANZEIGEN_REFRESH_TOKEN="RT-..."   # aus vorherigem Login
export KLEINANZEIGEN_EMAIL="alice@example.com"  # optional
kleinanzeigen login --json                     # schreibt session.json
```

**Chats danach:**

```bash
# alle eigenen Konversationen
kleinanzeigen chats list --json

# eine Konversation lesen (ID aus "chats list")
kleinanzeigen chats read 12345 --json

# antworten – NUR mit --confirm, sonst Exit 5
kleinanzeigen chats send 12345 --text "Ja, ich komme morgen." --confirm --json
```

Das `chats`-JSON enthält pro Konversation: Anzeige (ID + Titel), die
eigene Rolle (`BUYER`/`SELLER`), den Namen der Gegenseite, ob/anzahl
ungelesen, und die letzte Nachricht. `chats read` liefert zusätzlich
alle Nachrichten mit Richtung (`gesendet` / `empfangen`) und Zeitstempel.

## Ausgabe (mit `--json`)

- `search`: `{ quelle, filter, url, anzahl, anzeigen: [...] }`
- `show`:   `{ quelle, anzeige: {...} }`
- `categories`: `{ quelle, anzahl, kategorien: [...] }`
- `watch`:  `{ quelle, watch_name, url, anzahl_aktuell, anzahl_neu,
  neue_anzeigen: [...], alle_ids: [...] }`
- `login`:  `{ logged_in, email, quelle, session_file }`
- `logout`: `{ logged_out, session_file }`
- `chats list`: `{ quelle, email, anzahl, unterhaltungen: [...] }`
- `chats read`: `{ id, anzeige_id, anzeige_titel, rolle, gegenueber,
  nachrichten_anzahl, nachrichten: [...] }`
- `chats send`: `{ gesendet, konversation_id, text_laenge }`

Jede Anzeige enthält mindestens `id`, `url`, `titel`, `preis_euro`,
`preis_typ`, `ort`, `plz`, `versand_moeglich`, `versender_typ`,
`kategorie_pfad`, `beschreibung_kurz`. Auf der Detailseite zusätzlich
`beschreibung_text`.

### Preis-Felder

| Feld         | Typ                     | Bedeutung                                                   |
|--------------|-------------------------|-------------------------------------------------------------|
| `preis_euro` | `int` \| `null`         | numerischer Preis in €; `null` wenn kein Betrag erkennbar   |
| `preis_typ`  | `fest`\|`vb`\|`verschenken`\|`anfrage`\|`tausch`\|`null` | Was im Preisfeld steht |

- `fest`: konkrete Zahl + € (z. B. `1.690 €`, `699 €`, `1.299 €`)
- `vb`: Verhandlungsbasis (`VB` allein oder `699 € VB`)
- `verschenken`: Anzeige ohne Preis, nur „Zu verschenken"
- `anfrage`: „Preis auf Anfrage" / „auf Anfrage"
- `tausch`: reine Tauschanzeige (`Tausche`, im Preisfeld oder Titel)
- `null`: Karte hat gar kein Preisfeld (kaputter Eintrag / reine Tausch-Anzeige ohne Preisfeld)

`preis_euro` und `preis_typ` werden direkt aus dem Preisfeld der
Suchergebnisse gelesen, nicht per Volltext-Fallback (das geriet bei
Anzeigen-IDs und Slugs in die Irre).

## Exit-Codes

| Code | Bedeutung                                                  |
|-----:|------------------------------------------------------------|
| 0    | OK                                                         |
| 1    | Ungültige Argumente / unbekannte ID / leerer Send-Text     |
| 2    | Nicht eingeloggt (für `login`/`logout`/`chats*`)           |
| 3    | Netzwerk- / HTTP-Fehler                                    |
| 4    | Login fehlgeschlagen (Captcha, 2FA, Auth0-Fehler …)        |
| 5    | `chats send` ohne `--confirm`                              |

Fehler werden als einzeiliges JSON-Objekt auf **stderr** geschrieben
(`{"error": "...", ...}`). Erfolgs-Payloads gehen auf **stdout**.

## Sicherheit

- Passwörter werden **nie** gespeichert. Die Session-Datei enthält nur
  den OAuth-Refresh-Token und (wenn verfügbar) die E-Mail.
- `chats send` macht **nichts** ohne explizites `--confirm`. Das ist
  der einzige schreibende Endpoint des CLI.
- Die App-spezifischen Konstanten (Auth0-Client-ID, App-Basic-Auth)
  sind öffentlich (sie stecken im offiziellen Android-APK) und werden
  hier ohne Geheimnisse aus dem Repo benutzt.

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
werden gegen deterministische Karten-IDs geprüft. Auch die Chat-API
wird mit Fake-Responses getestet (`auth.Session` und `chats.*`); die
HTTP-Calls sind monkey-gepatcht, kein Roundtrip.

## Rechtliches

Diese Software ist ein privates Lernprojekt und **nicht** mit
kleinanzeigen.de verbunden. Lesezugriffe sind in Deutschland nach
§ 87b UrhG für Suchmaschinen und vergleichbare Anbieter zulässig –
das ersetzt keine Rechtsberatung. Das CLI folgt dem offiziellen
Auth0-Flow der Android-App, schreibt keine Cookies, loggt nichts
und macht keine Accounts von sich aus.

## Lizenz

MIT
