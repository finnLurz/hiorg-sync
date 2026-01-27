# hiorg-sync

HiOrg ? Active Directory / LDAP Sync (OAuth + LDAP/AD) inkl. Web-UI für Group-Mapping (Standort/OV-basiert).

- **Ziel:** HiOrg-Benutzer/Attribute nach AD synchronisieren  
- **Zusatz:** HiOrg-Gruppen auf AD-Gruppen abbilden (Mapping pro OV/Standort)

---

## Inhaltsverzeichnis

- [Features](#features)
- [Architektur (kurz)](#architektur-kurz)
- [Quickstart (Docker)](#quickstart-docker)
- [Konfiguration](#konfiguration)
- [UI](#ui)
- [API (kurz)](#api-kurz)
- [Daten & Persistenz](#daten--persistenz)
- [Development](#development)
- [Lizenz](#lizenz)
- [Disclaimer](#disclaimer)

---

## Features

- ? AD/LDAP Sync (Create/Update je nach Logik)
- ? Multi-OV Konzept (`ov=...`) mit getrennten Datenständen
- ? Web-UI (Jinja2 + Static Assets):
  - Login
  - OV-Auswahl
  - Gruppen-Mapping (Locations/BaseDN + Gruppen/AD-CN)
- ? CSS/JS frei anpassbar über `/static`
- ? API-Endpunkte für Sync + UI-Backend

---

## Architektur (kurz)

- **FastAPI App** (`create_app()`), Router:
  - `routers/ui.py` (HTML UI)
  - `routers/api.py` (UI-API für Groupmap/Gruppenliste)
  - `routers/sync.py` (Sync-Endpunkte)
  - `routers/oauth.py` (OAuth, falls aktiv)
  - `routers/misc.py` (health etc.)
- **Templates:** `src/hiorg_sync/web/templates/`
- **Static:** `src/hiorg_sync/web/static/`

---

## Quickstart (Docker)

### 1) `.env` anlegen
```bash
cp .env.example .env
# dann Werte anpassen


### 2) Build + Run

```bash
docker compose build --no-cache
docker compose up -d


## 3) UI öffnen

**Login:** `http://<host>:8088/ui/login`  
**OV-Auswahl:** `http://<host>:8088/ui/ov`  
**Groupmap:** `http://<host>:8088/ui/groupmap?ov=<ov>`

---

## Konfiguration

### Wichtige Variablen (Beispiele)

- `SYNC_API_KEY=...`  
  Optional, aber empfohlen (API-Schutz).

- `OV_LIST=obum,obub,obuo,obuw,obus`  
  Liste der verfügbaren OVs (wird im UI angeboten).

Zusätzlich (abhängig vom Setup): LDAP/AD Host, BindDN, Passwort, Search BaseDNs etc.  
?? Siehe `.env.example` für alle Optionen.

---

## Web-UI

### Dateien

#### Templates
- `web/templates/base.html`
- `web/templates/login.html`
- `web/templates/ov.html`
- `web/templates/groupmap.html`

#### Static
- `web/static/styles.css`
- `web/static/custom.css`
- `web/static/groupmap.js`
- `web/static/img/background_fw.png`

---

## Hintergrundbild testen

```bash
curl -I http://127.0.0.1:8088/static/img/background_fw.png