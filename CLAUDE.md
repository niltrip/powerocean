# PowerOcean – Hinweise für Claude

Home-Assistant-Integration (HACS) für EcoFlow PowerOcean. Code in
`custom_components/powerocean/`.

## Aufbau

- `api.py` / `ecoflow.py`: HTTP-Abfrage der EcoFlow-Cloud
- `parse.py`, `datapoints.py`, `devices.py`, `flows.py`: Antwort → Datenpunkte, Geräte, berechnete Energieflüsse
- `coordinator.py`: DataUpdateCoordinator, verbindet HTTP-Abfrage und MQTT
- `mqtt.py`: App-MQTT (Protobuf) für Einstellungen und Live-Leistung
- `sensor.py`, `number.py`, `switch.py`: Entitäten
- `strings.json` + `translations/` (en, de, fr) immer gemeinsam ändern

## Tests und Lint

- Tests laufen aus dem Test-Ordner:
  `cd custom_components/powerocean/tests && ../../../.venv/bin/python -m pytest`
- Lokale `.venv`: Python 3.14 + HA laut `requirements_test.txt`
- Lint: `scripts/lint` (ruff format + ruff check --fix). Ruff-Ziel ist py313,
  weil Nutzer mit HA 2025.8 noch Python 3.13 haben.
- CI auf GitHub Actions: Lint, hassfest/HACS, pytest (`.github/workflows/`)

## Regeln

- **Nichts Vertrauliches** in Code, Fixtures, Commits, Issues: keine
  Seriennummern, E-Mails, Passwörter, Tokens. Vor jedem Commit den Diff prüfen.
- **Fixtures sind echte API-Antworten** (anonymisiert). Nicht von Hand
  „passend machen“; Mapping-Änderungen gegen alle Fixtures prüfen.
- **Interface bleibt stabil:** Zustände, Attribute und unique_ids sind das,
  was Nutzer in Automationen verwenden. Änderungen daran zeigen die
  Golden-Master-Dateien in `tests/fixtures/`; deren Diff vor dem Commit
  zeigen. Neu erzeugen: Datei löschen und Tests erneut laufen lassen.
- Neue Funktionen bekommen Tests; Commit-Nachrichten auf Deutsch.
- Änderungen ins CHANGELOG unter *Unreleased*.
- `gh` immer mit `--repo niltrip/powerocean` (es gibt ein upstream-Remote).
- Nie mergen ohne Rückfrage, nie Releases oder Tags anlegen.
- `measurements/` (Mitschnitte, nicht im Repo) nicht löschen.
