# TR Verlusttopf Rechner (FIFO)

Kleines Script, das aus dem Trade-Republic-Timeline-Export (`all_events.json`) die realisierten Gewinne/Verluste je deutscher Verlusttopf (Aktien vs. Sonstige) berechnet.

## Was es tut
- Liest ausgeführte Käufe/Verkäufe aus `all_events.json` (von `pytr dl_docs`).
- FIFO-Kostenbasis pro ISIN, trennt in `stock` (Aktien/ETF) und `other` (Derivate/ETC/Optionsscheine etc.).
- Schreibt Detail-CSV `verlusttopf_<year>_sales.csv` mit Erlös, Kostenbasis, PnL, Topf.
- CLI-Option `--year` (Standard: aktuelles Jahr).

## Voraussetzungen
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) empfohlen (schnelles `pip`/Runner).
- Trade-Republic-Zugang für den Export (Cookies werden lokal gespeichert).

## 1) Login bei TR (einmalig, speichert Cookies)
```bash
uvx --from git+https://github.com/pytr-org/pytr.git pytr login --store_credentials
```
- Phone/PIN eingeben, Code bestätigen. Cookies/credentials landen in `~/.pytr/`.

## 2) Timeline + Events exportieren
```bash
uvx --from git+https://github.com/pytr-org/pytr.git pytr dl_docs tr_export \
  --export-transactions --export-format csv
```
Ergebnis im Ordner `tr_export/`: u.a. `all_events.json`, `account_transactions.csv` und die PDFs.

## 3) Verlusttopf berechnen
```bash
cd tr_export
python compute_verlusttopf.py --events all_events.json --year 2025
```
Ausgabe auf der Konsole und CSV `verlusttopf_2025_sales.csv`.

## Hinweise / Grenzen
- Nur ausgeführte Orders; stornierte/abgebrochene werden ignoriert.
- Wenn frühere Käufe fehlen, wird der Kostensatz zu niedrig → Warnung im Output.
- Keine steuerliche Beratung, keine Teilfreistellungen/Quellensteuer; reiner FIFO.
- Kategorien: alles ohne expliziten `instrumentType` wird als `stock` gedeutet; TR liefert für Derivate meist `derivative` → wandert in `other`.

## Dateien im Repo
- `compute_verlusttopf.py` – das Script
- `.gitignore` – schließt sensible/irrelevante Dateien aus (.pytr, Exporte, Cache)

## Typische Fehler
- "Credentials file not found": `pytr login --store_credentials` erneut ausführen.
- "all_events.json not found": sicherstellen, dass `pytr dl_docs ...` im aktuellen Ordner gelaufen ist.

## Lizenz
MIT (wie das pytr-Projekt, auf dem der Export basiert).

## Tests
- Erfordern nur `pytest` (kein Netz). Beispiel:
```bash
pip install pytest  # oder uv add pytest
pytest -q
```
Tests prüfen das Parsing der Event-Struktur, FIFO-Berechnung für Aktien/Derivate getrennt sowie Warnungen bei fehlendem Bestand.
