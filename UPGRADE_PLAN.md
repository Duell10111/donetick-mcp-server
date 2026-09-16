# Upgrade-Plan: Donetick MCP Server → aktuelle Donetick-Version

> Stand der Analyse: 2026-09-16
> Donetick-Referenz: `donetick/donetick` @ `v0.1.79` (Commit `15bbf14`, 2026-08-18)
> MCP-Server-Stand: `v0.3.13` (Commit `fd33f2f`)

Ziel: Den Fork kompatibel mit der aktuellen Donetick-Version machen. Schwerpunkte:
**Authentifizierung** (geprüft: API-Token vs. JWT, Entscheidung: vorerst JWT) und **Things-API**.
Dazu gehören das Dependency-Update und eine überarbeitete CLAUDE.md.

## Fortschritt

| Phase | Status | Branch |
|---|---|---|
| 1 – Stabilisierung und Dependencies | ✅ umgesetzt (2026-09-16) | `chore/phase-1-2-deps-jwt` |
| 2 – JWT-Authentifizierung härten | ✅ umgesetzt (2026-09-16) | `chore/phase-1-2-deps-jwt` |
| 3 – Bugfixes und API-Kompatibilität | ✅ umgesetzt (2026-09-16), inkl. 3.9 | `chore/phase-1-2-deps-jwt` |
| 4 – Things-Integration | ✅ umgesetzt (2026-09-16) | `chore/phase-1-2-deps-jwt` |
| 5 – MCP SDK 2.x (`MCPServer`-Refactor, Option A) | ✅ umgesetzt (2026-09-16) | `chore/phase-1-2-deps-jwt` |
| 6 – Dokumentation | ✅ umgesetzt (2026-09-16) | `chore/phase-1-2-deps-jwt` |
| 7 – API-Token | zurückgestellt | – |

Ergebnis Phase 1+2: `pytest -m "not live_api"` läuft ohne Env-Variablen mit **229 passed**
(vorher 196 passed / 1 failed, nur mit passender Test-URL). Das Docker-Image (Python 3.13, `mcp` 1.30.0)
baut und beantwortet `initialize` und `tools/list` (20 Tools) über stdio.

Abweichungen und Zusatzfunde bei der Umsetzung:
- **Dockerfile war nicht baubar:** `pip install -e .` lief vor `COPY src/`. Jetzt werden `pyproject.toml`, `README.md` und `src/` zuerst kopiert, dann folgt eine normale Installation. Der `apt`/`gcc`-Schritt ist entfernt, weil alle Dependencies Wheels haben.
- `pytz` ist durch `zoneinfo` + `tzdata` ersetzt. Nebeneffekt: Fälligkeitszeiten über Sommer-/Winterzeitwechsel werden jetzt korrekt berechnet (pytz hat den Offset von „jetzt“ beibehalten).
- Ein zweiter `401` nach der Re-Authentifizierung wurde bisher wie ein 5xx mit Backoff wiederholt. Jetzt kommt sofort ein Fehler (Tests angepasst).
- Refresh-Token: Donetick liest ihn bevorzugt aus dem Cookie und sperrt bei Wiederverwendung die ganze Session-Familie. Der Client verwirft deshalb die Cookies, sendet den Token im Body und serialisiert Login/Refresh über einen Lock.
- Neue Testdatei `tests/test_auth.py` (23 Tests: Login-Varianten, MFA, SSO-only, Refresh, paralleler Login, Config).
- Nicht umgesetzt: `ruff`-Bereinigung des Bestandscodes (252 Altfunde, überwiegend N815 für camelCase-Felder, gewollt). Das gehört zu Phase 5/6.

Ergebnis Phase 3+4: **282 passed** (mocked), 23 Live-Tests übersprungen (ohne Instanz nicht ausgeführt).
`tools/list` liefert 26 Tools. Neue Testdateien: `tests/test_api_compat.py` (15), `tests/test_things.py` (37).

Zusätzlich gefundene und behobene Inkompatibilitäten (alle im Donetick-Code `v0.1.79` verifiziert):
- **`create_chore` ignorierte das Fälligkeitsdatum:** Gesendet wurde `dueDate`, Donetick liest nur `nextDueDate`, und zwar als RFC3339. `YYYY-MM-DD` wird jetzt zu 12:00 in der angegebenen Zeitzone (Tool-Parameter `timezone`) bzw. 12:00 UTC im Modell. `dueDate` bleibt als Eingabe-Alias erhalten.
- **`create_chore` ohne `priority` → Panic in Donetick** (`*choreReq.Priority` ohne Nil-Check, `gin.New()` ohne Recovery). `priority` wird jetzt immer gesendet (Default 0).
- **`update_chore` scheiterte bei zugewiesenen Chores:** Die `assignedTo`-Prüfung verglich eine ID mit `{"userId": …}`-Objekten und hängte einen nackten Integer an `assignees` an → `400`. Der zugehörige Test hatte das falsche Format sogar festgeschrieben.
- **`update_chore`/`update_chore_assignee` löschten Thing-Trigger:** `EditChore` entfernt die Verknüpfung immer und legt sie nur aus `thingTrigger` neu an. Der Client wandelt `thingChore` jetzt in `thingTrigger` um.
- **`update_chore_priority` scheiterte immer:** Die Antwort ist `{"message": …}`, kein Chore. Jetzt wird der Chore danach abgerufen.
- **`ChoreHistory`:** `status` kommt als Integer (0–6) und wird auf Namen gemappt (inkl. `started`, `rejected`, `rescheduled`). `notes` statt `note`, `performedAt` kann `null` sein.
- **`ChoreDetail`:** Die echte Antwort hat kein `frequency`, `circleId`, `createdAt`, `updatedAt`. Diese Felder sind jetzt optional; `notes` und `duration` sind ergänzt.
- `frequencyType="interval_based"` wird von Donetick abgelehnt und jetzt auf `interval` abgebildet.
- Things: `GET /api/v1/things` liefert keine `thingChores` (kein Preload), nur die Antwort von `PUT /things/{id}/state`. Die Thing-History liefert 10 Einträge pro Seite.
- Live-Test `test_authentication_failure` rief die nicht existierende Methode `ensure_authenticated()` auf. Jetzt: `login()` → `DonetickAuthError`, und der Test wird ohne Konfiguration übersprungen.

Bewusste Abweichungen vom Plan:
- **3.8 `update_chore_assignee`** bleibt beim Full-PUT: `PUT /{id}/assignee` akzeptiert nur User, die schon in `assignees` stehen. Das Tool ersetzt die Zuweisung aber komplett.
- **3.8 Due Date:** `PUT /{id}/dueDate` wird nur genutzt, wenn `nextDueDate` die einzige Änderung ist (dann als „rescheduled“ in der Historie).
- **3.9 (optionale Chore-Tools)** zunächst zurückgestellt, dann nachgezogen (siehe unten).
- **Thing-Trigger bei `update_chore`:** `frequencyType` wird nicht automatisch auf `trigger` gesetzt (nur bei `create_chore`), damit ein bestehender Zeitplan nicht stillschweigend geändert wird.

Ergebnis 3.9 (zusätzliche Chore-Tools): 10 neue Tools, insgesamt **36 Tools**, **300 passed**
(neue Testdatei `tests/test_chore_actions.py`, 18 Tests):
`list_archived_chores`, `archive_chore`, `unarchive_chore`, `undo_chore_action`, `approve_chore`,
`reject_chore`, `start_chore_timer`, `pause_chore_timer`, `nudge_chore`, `list_projects`,
dazu `project_id` bei `create_chore` bzw. `projectId` bei `update_chore`.
- Umbenennung: `undo_chore_completion` → **`undo_chore_action`**, da Donetick auch Skip, Approval-Einreichung und Ablehnung rückgängig macht (nur eigene Aktion, max. 5 Minuten).
- **Keine automatischen Wiederholungen** bei `nudge`, `undo`, `approve`, `reject`, Timer und Archivierung (neuer Parameter `retry_server_errors` in `_request`). Ein Timeout oder 5xx kann schon angewendet worden sein, z. B. ein doppelter Push. Die Re-Authentifizierung nach `401` bleibt.
- Archivieren/Wiederherstellen darf nur der Ersteller. Donetick antwortet sonst mit `500`; der Client meldet das verständlich.
- `_request` liefert `{}` bei `200` ohne Body (kommt bei `PUT /start` vor, wenn ein pausierter Chore keine Session hat).
- `GET /api/v1/projects` liefert ein nacktes Array statt `{"res": …}`. Projekte anlegen/ändern ist bewusst nicht umgesetzt.
- `403`-Fehler zeigen jetzt die Donetick-Meldung an (z. B. „Only admins can approve chores“).

Ergebnis Phase 5 (`MCPServer`-Refactor): **309 passed** mit `mcp` 2.2.0. Die stdio-Verbindung ist geprüft mit
dem MCP-2.x-Client, mit einem klassischen `initialize`-Handshake (Protokoll `2025-06-18`) und im Docker-Image
(Python 3.13).
- `server.py` schrumpft von ca. 2.000 auf 88 Zeilen: `MCPServer` mit `lifespan` (erstellt und schließt den `DonetickClient`) und `instructions`. Die Serverversion kommt aus `__version__` (vorher wurde die SDK-Version gemeldet).
- Tools liegen jetzt pro Domäne in `src/donetick_mcp/tools/`: `chores`, `chore_actions`, `labels`, `circle`, `history`, `things`. Das JSON-Schema wird aus Type-Hints (`Annotated[..., Field(description=...)]`) erzeugt.
- `tools/_common.py`: Der Decorator `handle_errors` übernimmt die bisherigen Fehlermeldungen mit Hinweisen und wirft `ToolError`. **Fehler kommen jetzt als `isError=true`** (vorher als normale Textantwort).
- Tool-Annotationen: `readOnlyHint` für Lesetools, `destructiveHint` für `delete_chore`, `delete_label`, `delete_thing`, `idempotentHint` für Updates.
- Argumente werden vom SDK gegen das Schema validiert, bevor ein HTTP-Request rausgeht (z. B. `priority` 0–4).
- Tests laufen über den In-Memory-`Client` (`call_tool`/`list_tools`-Fixtures in `tests/conftest.py`). Neue Datei `tests/test_mcp_server.py` (9 Tests).

**Breaking Changes für Tool-Aufrufer:**
- `update_chore` nutzt snake_case wie alle anderen Tools: `next_due_date`, `is_active`, `is_private`, `require_approval`, `frequency_type`, `frequency_metadata`, `is_rolling`, `assign_strategy`, `notification_metadata`, `completion_window`, `project_id` (vorher camelCase).
- Entfernte Parameter ohne Wirkung: `create_chore.labels` (von Donetick ignoriert, stattdessen `label_names`/`labels_v2`), `create_chore.nagging`/`predue` (wurden nie ausgewertet, stattdessen `enable_nagging`/`enable_predue`), `deadline_offset`/`deadlineOffset` (existiert in Donetick nicht).
- Neu im Schema (wurden vorher gelesen, waren aber nicht deklariert): `labels_v2`, `notification_metadata`, `completion_window`, `require_approval`.
- `get_chore` mit unbekannter ID liefert jetzt einen Tool-Fehler statt normalem Text.

Ergebnis Phase 6 (Dokumentation), Version **0.5.0**:
- `README.md` neu: Fork-Hinweis (das PyPI-Paket ist Upstream), Installation per `uvx --from git+…`, Docker (`docker run -i --rm --env-file`) und pip. Außerdem Voraussetzungen (HTTPS, kein MFA, kein SSO-only), Begründung für JWT statt API-Token, Tool-Tabellen für alle 36 Tools und Troubleshooting.
- `CLAUDE.md` neu (ca. 190 statt ca. 800 Zeilen): Befehle, Architektur mit Tool-Pattern, Auth-Flow, Donetick-API-Quirks, Test-Konventionen (`call_tool`-Fixture, Registrierungsreihenfolge bei pytest-httpx), Checkliste „Adding a Tool“. Ohne Zeilennummern und ohne Versionshistorie.
- `CHANGELOG.md`: Eintrag 0.5.0 mit Breaking Changes, Added, Fixed, Changed.
- `tests/integration/README.md`: `TestThings`, Skip-Verhalten, korrigierter Beispielbefehl.
- Version 0.5.0 in `pyproject.toml` und `__init__.py`. Verifiziert: `docker compose build` und `docker run --env-file` melden `0.5.0` mit 36 Tools.

Offen:
- Der Live-Test `TestThings::test_thing_trigger_lifecycle` (und die übrigen Live-Tests) müssen noch gegen eine echte Instanz laufen.
- Die Installation per `uvx --from git+https://github.com/Duell10111/donetick-mcp-server` ist ungetestet (`uv` war lokal nicht installiert) und funktioniert erst, wenn der Branch auf `main` gemergt ist.
- Optional: Bereinigung der Ruff-Altfunde (`Optional[...]` → `X | None`, N815 für camelCase-Modelfelder per Konfiguration ignorieren).

---

## 0. Zusammenfassung (TL;DR)

| # | Befund | Schwere |
|---|--------|---------|
| 1 | **`mcp>=1.20.0` hat keine Obergrenze.** Eine frische Installation zieht heute MCP SDK **2.2.0**. Dort gibt es die Decorators `@app.list_tools()` und `@app.call_tool()` nicht mehr, deshalb **startet der Server nicht** (`AttributeError: 'Server' object has no attribute 'list_tools'`). | 🔴 kritisch |
| 2 | `docker-compose.yml` setzt *nur* `DONETICK_API_TOKEN`, der Code braucht aber `DONETICK_USERNAME` und `DONETICK_PASSWORD`. Das Docker-Setup startet so nicht. | 🔴 kritisch |
| 3 | Mit API-Token funktionieren nur **12 von 20 Tools voll** und 2 eingeschränkt. 6 Tools brauchen JWT, weil Labels (`/api/v1/labels`) und Users (`/api/v1/users/*`) in Donetick **nur JWT** akzeptieren. **Entscheidung: vorerst bei JWT bleiben.** Damit laufen alle Tools; API-Token kommt später als optionale Phase 7. | 🟢 entschieden |
| 4 | Die Things-API (`/api/v1/things`) akzeptiert API-Token **und** JWT. Im MCP-Server ist sie noch nicht angebunden. Das Chore-Feld heißt im Request `thingTrigger`, das Modell sendet aber `thingChore`. | 🟠 hoch |
| 5 | `complete_chore` sendet einen POST ohne Body und `completedBy` als Query-Parameter. Donetick bindet den Request aber per `ShouldBindJSON`: Ein leerer Body wird abgelehnt, und `completedBy` gehört in den Body. | 🟠 hoch (live verifizieren) |
| 6 | Bei `get_all_chores_history` ist `limit` in Donetick eine **Zeitspanne in Tagen** und keine Anzahl. `offset` wird ignoriert. | 🟡 mittel |
| 7 | Die Tests lassen sich ohne gesetzte Env-Variablen nicht einmal sammeln, weil `config = Config()` beim Import validiert. Außerdem sind zwei verschiedene Test-URLs fest verdrahtet. Baseline mit `mcp` 1.30: 196 passed, 1 failed. | 🟡 mittel |
| 8 | CLAUDE.md ist an vielen Stellen veraltet oder falsch (Details in Abschnitt 6). | 🟡 mittel |

---

## 1. Analyse: Authentifizierung in Donetick (aktuell)

### 1.1 Mechanismen

Quelle: `internal/auth/multiauthmiddleware.go`, `internal/auth/api_middleware.go`, `docs/swagger.yaml`

| Mechanismus | Header | Lebensdauer | Erzeugung |
|---|---|---|---|
| JWT (Access Token) | `Authorization: Bearer <jwt>` | kurz (`jwt.session_time`), Refresh über `POST /api/v1/auth/refresh` | `POST /api/v1/auth/login` |
| **API-Token (Long-Lived)** | **`secretkey: <token>`** | unbegrenzt, bis zum Löschen | UI: *Settings → Access Tokens*, oder `POST /api/v1/users/tokens` (JWT-only) |

`MultiAuthMiddleware` prüft zuerst den `secretkey`-Header und fällt danach auf JWT zurück.
Achtung: Ist ein `secretkey` gesetzt, aber ungültig, gibt es **keinen** JWT-Fallback, sondern direkt `401`.

Zusätzlich gibt es:
- `X-Impersonate-User-ID` (Chore-Routen, nur für Circle-Admins). Damit lässt sich z. B. im Namen eines Kindes abhaken.
- `X-MFA-Code` (nur relevant für `eapi`-Routen mit `RequireMFAMiddleware`; aktuell nicht im Einsatz)
- Login-Response (`EnhancedLoginHandler`): `access_token`, `refresh_token`, `access_token_expiry`, `refresh_token_expiry`, `token_type`, sowie die Legacy-Felder `token` und `expire`. Der Client liest bisher nur `token`, das funktioniert weiterhin.
- Mit `disable_password_auth` antwortet `POST /api/v1/auth/login` mit `403` (SSO-only-Instanzen). **Dort funktioniert nur der API-Token.**

### 1.2 Routen-Gruppen und akzeptierte Auth

| Routen-Gruppe | Middleware | API-Token | JWT |
|---|---|:---:|:---:|
| `/api/v1/chores/*` | MultiAuth + Impersonation | ✅ | ✅ |
| `/api/v1/circles/*` | MultiAuth | ✅ | ✅ |
| `/api/v1/things/*` | MultiAuth | ✅ | ✅ |
| `/api/v1/projects/*` | MultiAuth | ✅ | ✅ |
| `/api/v1/filters/*` | MultiAuth | ✅ | ✅ |
| `/api/v1/sync/changes` | MultiAuth | ✅ | ✅ |
| `/api/v1/labels/*` | **JWT-only** | ❌ | ✅ |
| `/api/v1/users/*` (profile, users, tokens …) | **JWT-only** | ❌ | ✅ |
| `/api/v1/devices`, `/assets`, `/files`, `/realtime` | JWT-only | ❌ | ✅ |
| `/eapi/v1/chore` (GET/POST/DELETE) | APIToken-only | ✅ | ❌ |
| `/eapi/v1/chore/:id/complete`, `PUT /:id`, `/eapi/v1/circle/members` | APIToken + **Plus** | ✅ (Plus) | ❌ |
| `/eapi/v1/things/*` | APIToken-only | ✅ | ❌ |

**Fazit:** Der MCP-Server bleibt bei `/api/v1/`. Die `eapi` bringt keinen Mehrwert, weil sie
funktional kleiner und teilweise Plus-gebunden ist. Mit JWT sind **alle** `/api/v1`-Gruppen
erreichbar; mit `secretkey` fehlen Labels und Users (siehe 1.3).

### 1.3 Feature-Matrix: Welche MCP-Tools funktionieren mit API-Token?

| Tool | Endpoint (aktuell im Client) | API-Token | Anmerkung / Maßnahme |
|---|---|:---:|---|
| `list_chores` | `GET /api/v1/chores/` | ✅ | optional `includeArchived`, `includeSubtasks` ergänzen |
| `get_chore` | `GET /api/v1/chores/{id}` | ✅ | – |
| `create_chore` | `POST /api/v1/chores/` | ⚠️ | ✅ mit User-Namen (nutzt `circles/members`). ❌ **mit Label-Namen**, weil `lookup_label_ids` intern `GET /api/v1/labels` aufruft. Fallback nötig (siehe 3.3). |
| `update_chore` | `GET` + `PUT /api/v1/chores/` | ⚠️ | Wie `create_chore` bei Label-Namen |
| `complete_chore` | `POST /api/v1/chores/{id}/do` | ✅* | *Body-Bug beheben (siehe 3.5) |
| `update_chore_priority` | `PUT /api/v1/chores/{id}/priority` | ✅ | – |
| `update_chore_assignee` | `GET` + `PUT /api/v1/chores/` | ✅ | optional auf `PUT /{id}/assignee` (`{assignee, updatedAt}`) umstellen |
| `delete_chore` | `DELETE /api/v1/chores/{id}` | ✅ | – |
| `skip_chore` | `POST /api/v1/chores/{id}/skip` | ✅ | Client korrekt (POST); CLAUDE.md sagt fälschlich PUT |
| `update_subtask_completion` | `GET` + `PUT /api/v1/chores/` | ✅ | besser: dedizierter Endpoint `PUT /{id}/subtask` (`{id, choreId, completedAt}`) |
| `get_chore_history` | `GET /api/v1/chores/{id}/history` | ✅ | – |
| `get_all_chores_history` | `GET /api/v1/chores/history` | ✅* | *Parameter-Semantik falsch (siehe 3.6) |
| `get_chore_details` | `GET /api/v1/chores/{id}/details` | ✅ | – |
| `get_circle_members` | `GET /api/v1/circles/members/` | ✅ | – |
| `list_labels` | `GET /api/v1/labels` | ❌ | Fallback: Labels aus `labelsV2` aller Chores ableiten (read-only, nur benutzte Labels) |
| `create_label` | `POST /api/v1/labels` | ❌ | nur JWT |
| `update_label` | `PUT /api/v1/labels` | ❌ | nur JWT |
| `delete_label` | `DELETE /api/v1/labels/{id}` | ❌ | nur JWT |
| `list_circle_users` | `GET /api/v1/users/` | ❌ | Fallback: auf `GET /api/v1/circles/members/` umleiten (liefert dieselben User plus Rolle/Punkte) |
| `get_user_profile` | `GET /api/v1/users/profile` | ❌ | nur JWT, kein gleichwertiger Ersatz |

**Ergebnis:** Mit reinem API-Token funktionieren **12 Tools voll** und **2 eingeschränkt**
(`create_chore`/`update_chore` mit Label-Namen). **6 Tools gehen nur mit JWT.** Davon lassen sich
`list_circle_users` und `list_labels` (read-only) per Fallback abdecken. Ohne JWT bleiben
`create_label`, `update_label`, `delete_label` und `get_user_profile` nicht möglich.

**Entscheidung (2026-09-16): Auth bleibt vorerst JWT (Username/Passwort).**
Mit JWT funktionieren alle 20 bestehenden Tools **und** die Things-API. Ein reiner Token-Modus würde
dagegen Features verlieren. Die JWT-Anbindung wird in Phase 2 gehärtet. Der API-Token wird als
optionale **Phase 7** zurückgestellt: sinnvoll, sobald Donetick Labels und Users auf
`MultiAuthMiddleware` umstellt (möglicher kleiner Upstream-PR) oder falls eine der Einschränkungen
unten relevant wird.

**Grenzen von JWT, die man kennen sollte** (im Donetick-Code verifiziert):
- **MFA:** Hat der Account MFA aktiviert, liefert `POST /api/v1/auth/login` statt eines Tokens nur
  `{"mfaRequired": true, "sessionToken": …}`. Der Client scheitert dann mit „missing token“, und ein
  headless MCP-Server kann den TOTP-Code nicht eingeben. → Für den MCP-Server einen Account ohne MFA
  verwenden. Der Client soll diesen Fall mit einer klaren Fehlermeldung erkennen.
- **SSO-only-Instanzen** (`disable_password_auth: true`): Password-Login liefert `403`, dort ginge nur
  der API-Token.
- Das Passwort liegt im Klartext in der Env bzw. Config. Ein API-Token wäre einzeln widerrufbar.

---

## 2. Analyse: Things-API

Quelle: `internal/thing/handler.go`, `internal/thing/model/model.go`, `internal/thing/helper.go`

### 2.1 Modell

```jsonc
// Thing
{ "id": 1, "userID": 3, "circleId": 1, "name": "Waschmaschine",
  "state": "true", "type": "boolean",           // text | number | boolean (action: nicht validierbar)
  "thingChores": [ { "thingId": 1, "choreId": 42, "triggerState": "false", "condition": "eq" } ],
  "createdAt": "…", "updatedAt": "…" }

// ThingHistory
{ "id": 7, "thingId": 1, "state": "false", "createdAt": "…", "updatedAt": "…" }
```

Validierung des States (`isValidThingState`):
- `number`: muss als Integer parsebar sein
- `boolean`: nur `"true"` oder `"false"`
- `text`: beliebig
- **Alle anderen Typen, auch `action`, werden als ungültig abgelehnt.**

Trigger-Bedingungen (`EvaluateThingChore`): `eq` (Default), `neq`, `gt`, `lt`, `gte`, `lte`
(die letzten vier nur bei numerischen States). Trifft ein Trigger, bekommt der verknüpfte Chore
das Fälligkeitsdatum *jetzt*, sofern er noch keins hat (`SetDueDateIfNotExisted`).

### 2.2 Endpoints (`/api/v1/things`, API-Token ✅)

| Methode | Pfad | Request | Response |
|---|---|---|---|
| `GET` | `/api/v1/things` | – | `{"res": [Thing]}` (nur eigene Things des Users) |
| `POST` | `/api/v1/things` | `{"name", "type", "state"?}` | `201 {"res": Thing}` |
| `PUT` | `/api/v1/things` | `{"id", "name", "type", "state"?}` (**ID im Body**) | `{"res": Thing}` |
| `PUT` | `/api/v1/things/{id}/state?value=<state>` | Query-Param `value` | `{"res": Thing}`, wertet Trigger aus und feuert den Webhook |
| `GET` | `/api/v1/things/{id}/history?offset=<n>` | `offset` **Pflicht** | `{"res": [ThingHistory]}` |
| `DELETE` | `/api/v1/things/{id}` | – | `{}`, bzw. `405`, wenn Chores verknüpft sind |

Chore-Verknüpfung in `POST` und `PUT /api/v1/chores/`:
```json
{ "frequencyType": "trigger",
  "thingTrigger": { "thingID": 1, "triggerState": "false", "condition": "eq" } }
```
Beim Lesen eines Chores heißt das Feld dagegen `thingChore` (`{thingId, choreId, triggerState, condition}`).

### 2.3 API-Quirks

- Things sind **user-scoped** (`thing.UserID == currentUser.ID`) und nicht circle-scoped. Andere Circle-Mitglieder bekommen `403`.
- `PUT /{id}/state`: Donetick liest `thing.State`, *bevor* es den Fehler von `GetThingByID` prüft. Eine ungültige ID kann deshalb einen `500`/Panic auslösen. Der Client sollte die ID vorher validieren oder den Fehler abfangen.
- `GET /{id}/history` ohne `offset` liefert `400`. Also immer `offset=0` senden.
- Die `eapi`-Variante (`/eapi/v1/things/{id}/state/change?op=+1`) kann Zahlen inkrementieren. In `/api/v1` gibt es das nicht; dort muss der Client lesen, rechnen und dann setzen.

### 2.4 Geplante MCP-Tools

| Tool | Beschreibung |
|---|---|
| `list_things` | alle Things mit State, Typ und verknüpften Chores |
| `create_thing` | `name`, `type` (`text`/`number`/`boolean`), `state` optional; clientseitige Validierung wie im Server |
| `update_thing` | Name, Typ, State (fetch-modify-send, weil die ID im Body steht) |
| `set_thing_state` | State setzen; optional `increment` für `number` (lesen → addieren → setzen); gibt aus, welche Chores ausgelöst wurden |
| `get_thing_history` | mit `offset`-Pagination |
| `delete_thing` | mit verständlicher Meldung bei `405` (verknüpfte Chores) |
| `create_chore` / `update_chore` | neue Parameter `thing_id`, `thing_trigger_state`, `thing_trigger_condition`; setzen automatisch `frequencyType="trigger"` |

---

## 3. Umsetzungsplan

### Phase 1: Stabilisierung und Dependencies (sofort)

**3.0.1 Dependencies**

| Paket | aktuell (Constraint) | neueste Version | Plan |
|---|---|---|---|
| `mcp` | `>=1.20.0` | **2.2.0** (Major) | Übergangsweise `mcp>=1.26,<2` pinnen (Hotfix). Danach **Migration auf 2.x** (Phase 4) und `mcp>=2.2,<3`. |
| `httpx` | `>=0.27.0` | 0.28.1 | `>=0.28.1,<1` |
| `pydantic` | `>=2.0.0` | 2.13.5 | `>=2.10,<3` |
| `python-dotenv` | `>=1.0.0` | 1.2.3 | `>=1.1` |
| `pytz` | `>=2024.1` | 2026.3 | **entfernen** und durch stdlib `zoneinfo` ersetzen (Python ≥ 3.11) |
| `pytest` | `>=8.0.0` | 9.1.1 | `>=9` |
| `pytest-asyncio` | `>=0.23.0` | 1.4.0 | `>=1.0`; Event-Loop-Fixtures prüfen (Breaking in 1.0) |
| `pytest-httpx` | `>=0.30.0` | 0.36.2 | `>=0.36`; `is_optional`/`assert_all_responses_were_requested` prüfen |
| `pytest-mock` | `>=3.12.0` | 3.15.1 | `>=3.15` |
| *neu* `pytest-cov` | – | 7.1.0 | in `dev` aufnehmen (CLAUDE.md nutzt `--cov`) |
| *neu* `ruff` | – | 0.16.8 | in `dev` aufnehmen (CLAUDE.md nutzt `ruff`) |

Weitere Punkte:
- `Dockerfile`: `python:3.11-slim` → `python:3.13-slim` (oder 3.12). Das Build-Stage-Keyword `as` → `AS`.
- `requires-python`: `>=3.11` bleibt (MCP 2.x verlangt ≥ 3.10).
- `pyproject.toml`: `[project.urls]` und `authors` auf den Fork `Duell10111/donetick-mcp-server` umstellen. Die Beschreibung erwähnt aktuell „JWT authentication“.
- Optional: `uv.lock` bzw. einen Lockfile einführen, damit Builds reproduzierbar sind. Genau das Fehlen eines Lockfiles hat Befund #1 verursacht.

**3.0.2 Testbarkeit**

Ermittelte Baseline am 2026-09-16 (Python 3.14, neueste pytest-Plugins, `test_performance.py` ausgelassen):

| Setup | Ergebnis |
|---|---|
| `mcp` 2.2.0 (heutiger Default bei frischer Installation) | `tests/test_server.py` bricht beim Sammeln ab: `'Server' object has no attribute 'list_tools'` |
| `mcp` 1.30.0, ohne `DONETICK_*`-Env-Variablen | alle 7 Testmodule brechen beim Sammeln ab (`Configuration validation failed`) |
| `mcp` 1.30.0, Env-Variablen gesetzt, `DONETICK_BASE_URL` = beliebige URL | 42 failed, 45 errors: Die Mocks erwarten eine fest verdrahtete URL |
| `mcp` 1.30.0, `DONETICK_BASE_URL=https://donetick.jason1365.duckdns.org` | **196 passed, 1 failed** (`test_integration.py::test_full_chore_lifecycle`, Mock für `GET /chores/1` auf `https://test.donetick.com`) |

Maßnahmen:
- `config.py`: `config = Config()` nicht mehr beim Import validieren. Stattdessen lazy (`get_config()`) oder erst in `DonetickClient.__init__` bzw. `main()`.
- `tests/conftest.py`: Base-URL und Credentials per autouse-Fixture setzen. Die zwei fest verdrahteten URLs (`https://donetick.jason1365.duckdns.org`, `https://test.donetick.com`) durch **eine** Konstante `https://donetick.test` ersetzen.
- `test_full_chore_lifecycle` reparieren.
- `coverage.json` aus dem Repo entfernen und in `.gitignore` aufnehmen.

### Phase 2: JWT-Authentifizierung härten (statt API-Token)

**2.1 Konfiguration aufräumen**
- `docker-compose.yml`: `DONETICK_API_TOKEN` durch `DONETICK_USERNAME` und `DONETICK_PASSWORD` ersetzen, passend zu `.env.example`.
- `config.py`: die irreführende Meldung „DONETICK_API_TOKEN is deprecated in v2.0.0“ entfernen (kaputter Link `yourusername`). Stattdessen, falls die Variable gesetzt ist: „API-Token wird aktuell nicht unterstützt, bitte Username/Passwort verwenden.“

**2.2 Client** (`client.py`)
- Die neue Login-Response nutzen: `access_token`, `refresh_token`, `access_token_expiry`. `token` bleibt als Fallback für ältere Instanzen.
- Proaktiv erneuern: kurz vor `access_token_expiry` zuerst `POST /api/v1/auth/refresh` (Refresh-Token im JSON-Body oder als Cookie), erst danach Re-Login. So gibt es weniger Logins, und der Rate-Limiter auf `/auth` greift seltener.
- Ein `asyncio.Lock` um `login()`/`refresh()`, damit parallele Tool-Calls nicht mehrere Logins auslösen.
- `mfaRequired: true` erkennen und mit klarer Meldung abbrechen („Account hat MFA aktiviert, für den MCP-Server einen Account ohne MFA verwenden“).
- `403` beim Login (SSO-only-Instanz) mit klarer Meldung behandeln.
- Beim `401`-Retry die Fehlermeldung von Donetick durchreichen.

**2.3 Tests**
- Login mit neuer und alter Response-Form, Refresh-Flow, MFA-Response, 403 bei SSO-only, paralleler Login (nur ein Request).

> Die folgenden Abschnitte 3.1 bis 3.4 beschreiben die **zurückgestellte** API-Token-Unterstützung (Phase 7). Sie bleiben als Referenz für später im Plan.

### Phase 7 (zurückgestellt): API-Token-Authentifizierung

**3.1 Konfiguration** (`config.py`, `.env.example`, `docker-compose.yml`)

```dotenv
# Variante A (empfohlen): API-Token (Settings → Access Tokens in Donetick)
DONETICK_API_TOKEN=xxxxxxxx

# Variante B: Username/Passwort (JWT). Nötig für Labels, User-Profil
DONETICK_USERNAME=…
DONETICK_PASSWORD=…

# Optional: Auth-Modus erzwingen (auto | token | jwt), Default auto
DONETICK_AUTH_MODE=auto
```

Regeln:
- Es muss mindestens `DONETICK_API_TOKEN` **oder** `USERNAME`+`PASSWORD` gesetzt sein.
- Die „deprecated“-Warnung für `DONETICK_API_TOKEN` fällt weg.
- `auto`: Token vorhanden → Token für alle MultiAuth-Routen. Sind zusätzlich Credentials vorhanden, wird JWT **nur** für JWT-only-Routen genutzt (Hybrid).
- `DONETICK_ALLOW_HTTP=true` (optional) für lokale Instanzen im LAN. Die HTTPS-Pflicht bleibt Default.

**3.2 Client** (`client.py`)
- Eine `AuthStrategy`-Abstraktion einführen:
  - `ApiTokenAuth`: setzt den Header `secretkey`, kein Login. Ein `401` wird **nicht** wiederholt, sondern mit klarer Fehlermeldung beantwortet („API-Token ungültig/gelöscht“).
  - `JwtAuth`: wie bisher, aber mit `access_token` und `refresh_token` aus der neuen Login-Response. Bei Ablauf zuerst `POST /api/v1/auth/refresh`, erst dann Re-Login. Den `access_token_expiry` für proaktiven Refresh nutzen.
- `_request(..., auth="multi" | "jwt")`: Für JWT-only-Routen (`/labels`, `/users/*`) wird JWT erzwungen. Gibt es keine Credentials, kommt eine verständliche `AuthModeError`:
  > „`list_labels` benötigt Username/Passwort (Donetick erlaubt für /api/v1/labels keinen API-Token).“
- `401`-Body (`"Authentication required. Provide either a valid JWT token or API key."`) in der Fehlermeldung durchreichen.
- Das Login ist heute nicht gegen parallele Aufrufe gesperrt. Ein `asyncio.Lock` um `login()`/`refresh()` verhindert parallele Logins.

**3.3 Fallbacks für JWT-only-Endpoints im Token-Modus**
- `list_circle_users` → `GET /api/v1/circles/members/` und auf das `User`-Modell mappen.
- `list_labels` (read-only) → Labels aus `labelsV2` von `GET /api/v1/chores/?includeArchived=true` deduplizieren. Im Tool-Output kennzeichnen: „nur verwendete Labels“.
- `lookup_label_ids` (für `create_chore`/`update_chore` mit Label-Namen) → denselben Fallback nutzen. Wird ein Name nicht gefunden, Hinweis ausgeben, dass neue Labels einen JWT-Login brauchen.
- `create_label`, `update_label`, `delete_label`, `get_user_profile` → ohne JWT eine klare Fehlermeldung. Optional die Tools bei `list_tools` im reinen Token-Modus ausblenden. Entscheidung offen, Empfehlung: anzeigen, aber mit Hinweis in der Beschreibung.

**3.4 Optional: Impersonation**
- Neuer optionaler Parameter `as_user_id` für `complete_chore` und `skip_chore` → Header `X-Impersonate-User-ID` (nur Circle-Admins).

### Phase 3: Bugfixes aus der API-Analyse

**3.5 `complete_chore`**
- Einen JSON-Body immer senden: `{}` oder `{"notes": …, "completedBy": …, "completedTime": …}`.
- `completedBy` aus den Query-Params in den Body verschieben.
- Neue optionale Tool-Parameter: `notes`, `completed_at`.
- Live gegen Donetick prüfen, ob ein leerer Body tatsächlich `400` liefert (`ShouldBindJSON` → `EOF`).

**3.6 `get_all_chores_history`**
- Donetick: `limit` = **Anzahl Tage** (Default 7), `members=true` = ganzer Circle statt nur eigener Einträge. `offset` wird nicht unterstützt.
- Tool-Parameter umbenennen bzw. neu: `days` (Default 7) und `include_circle_members` (bool). `offset` und die Pagination-Hinweise im Output entfernen.

**3.7 Modelle an die aktuelle Donetick-API anpassen** (`models.py`)
- `ChoreCreate`/`ChoreUpdate`: `thingChore` → **`thingTrigger`** (`{thingID, triggerState, condition}`); `projectId` ergänzen.
- `frequencyType` um `trigger` erweitern (Donetick-Enum: `once daily weekly monthly yearly adaptive interval days_of_the_week day_of_the_month trigger no_repeat`).
- `Chore`: neue Felder `projectId`, `project`, `syncVersion`, `attachments`, `status` (0 = none, 1 = in progress, 2 = paused, 3 = pending approval), `thingChore`.
- `ChoreHistory`: `status` (0 = started, 1 = completed, 2 = skipped, …), `duration`, `syncVersion`.
- Unbekannte Felder tolerieren (`model_config = ConfigDict(extra="allow")`), damit neue API-Felder nicht brechen.
- `EditChore` vergleicht `updatedAt` (optimistic locking). Der Client entfernt `updatedAt` aktuell über `FIELDS_TO_REMOVE`. Das beibehalten, aber im Test dokumentieren.

**3.8 Dedizierte Endpoints nutzen** (weniger fetch-modify-send, weniger Konflikte)
- `update_subtask_completion` → `PUT /api/v1/chores/{id}/subtask` mit `{"id", "choreId", "completedAt"}`
- `update_chore_assignee` → `PUT /api/v1/chores/{id}/assignee` mit `{"assignee", "updatedAt"}`
- Due-Date-Änderung → `PUT /api/v1/chores/{id}/dueDate` mit `{"dueDate", "updatedAt"}`

**3.9 Optionale neue Chore-Tools** (alle API-Token-fähig)
- `archive_chore` / `unarchive_chore` (`PUT /{id}/archive|unarchive`)
- `undo_chore_completion` (`POST /{id}/undo`)
- `approve_chore` / `reject_chore` (`POST /{id}/approve|reject`)
- `start_chore_timer` / `pause_chore_timer` (`PUT /{id}/start|pause`)
- `nudge_chore` (`POST /{id}/nudge`)
- `list_projects` (`GET /api/v1/projects`) plus `project_id` bei `create_chore`
- `list_archived_chores` (`GET /api/v1/chores/archived`)

### Phase 4: Things-Integration

Siehe Abschnitt 2.4.
1. `models.py`: `Thing`, `ThingChore`, `ThingHistory`, `ThingCreate`, `ThingTrigger` plus Validatoren (Typ/State-Kombination, Condition-Enum, numerische Condition nur bei `number`)
2. `client.py`: `list_things`, `create_thing`, `update_thing`, `set_thing_state`, `get_thing_history`, `delete_thing`
3. `server.py`: 6 neue Tools plus `thing_*`-Parameter bei `create_chore`/`update_chore`
4. Tests: Unit (httpx-Mock), Server-Tests, Live-Test (Thing anlegen → Trigger-Chore anlegen → State setzen → `nextDueDate` prüfen → aufräumen)

### Phase 5: Migration auf MCP SDK 2.x

In einem Scratch-venv verifiziert (mcp 2.2.0):
- `from mcp.server import Server`, `from mcp.types import TextContent, Tool` und `mcp.server.stdio.stdio_server` funktionieren weiterhin.
- **Entfernt:** die Decorators `Server.list_tools()` und `Server.call_tool()`. Handler werden jetzt im Konstruktor übergeben:
  `Server(name, version=…, instructions=…, on_list_tools=…, on_call_tool=…)`.
  Signaturen: `(ctx, params) -> ListToolsResult` bzw. `(ctx, CallToolRequestParams) -> CallToolResult`.
- `mcp.server.fastmcp.FastMCP` → `mcp.server.mcpserver.MCPServer`.
- Protokolltypen liegen im eigenen Paket `mcp-types`.

Vorgehen, zwei Optionen:
- **A (empfohlen): Umstieg auf `MCPServer`** (High-Level, Decorator-basiert, Schema aus Type-Hints und Pydantic).
  Damit entfallen ca. 700 Zeilen handgeschriebenes JSON-Schema in `server.py` und das große `if/elif` in `call_tool`. Tools werden pro Domäne in Module aufgeteilt: `tools/chores.py`, `tools/labels.py`, `tools/circle.py`, `tools/history.py`, `tools/things.py`.
  Nebeneffekte: Tool-Annotations (`readOnlyHint`, `destructiveHint`) und Structured Output bekommt man gratis.
- **B (minimal):** beim Low-Level-`Server` bleiben und nur die Registrierung auf `on_list_tools`/`on_call_tool` umstellen. Die Rückgabe wird in `ListToolsResult`/`CallToolResult` gewrappt. Weniger Aufwand, aber `server.py` bleibt groß.

Außerdem:
- `main()`/`cleanup()` vereinfachen: Client-Lebenszyklus über den `lifespan`-Parameter statt globaler Variable und `get_event_loop`-Workarounds.
- Tests in `tests/test_server.py` auf die neue Registrierung umstellen (In-Memory-`Client` aus MCP 2.x).

### Phase 6: Dokumentation

- `README.md`: Abschnitt „Authentication“ überarbeiten (Username/Passwort; Hinweise zu MFA und SSO-only; kurzer Vermerk, warum noch kein API-Token), Things-Tools, Tool-Liste vollständig (aktuell nur 6 von 20 dokumentiert), uvx/Docker-Configs konsistent mit `DONETICK_USERNAME`/`DONETICK_PASSWORD`
- `CHANGELOG.md`: Einträge für die neue Version (Vorschlag **v0.5.0**, weil Tool-Parameter brechen: `get_all_chores_history`)
- `.env.example`, `docker-compose.yml`: konsistente Auth-Variablen
- `CLAUDE.md`: siehe Abschnitt 6

---

## 4. Test- und Verifikationsplan

1. **Baseline** (vor Änderungen, siehe 3.0.2): 196 passed / 1 failed mit `mcp<2` und passender Test-URL. Nach Phase 1 muss `pytest -m "not live_api"` ohne jede Env-Variable grün laufen.
2. **Pro Phase:** Unit-Tests für jede neue oder geänderte Client-Methode mit `pytest-httpx`. Explizit prüfen:
   - JWT: Refresh vor Ablauf, nur ein Login bei parallelen Calls, MFA- und SSO-only-Fehlermeldungen
   - `complete_chore` sendet JSON-Body
3. **Live-Tests** (`tests/integration/test_live_api.py`): neue Live-Tests für Things, Trigger-Chores und `complete_chore` mit Body. Erst in Phase 7 über `auth_mode ∈ {token, jwt}` parametrisieren.
4. **Docker-Smoke-Test:** `docker compose up`, danach `tools/list` per MCP-Inspector bzw. MCP-2.x-`Client` gegen stdio.
5. `ruff check src tests` und `ruff format --check`.
6. Optional: GitHub Actions Workflow (`.github/workflows/test.yml`) mit Matrix Python 3.11–3.13, ohne Live-Tests.

---

## 5. Reihenfolge und Aufwand (Schätzung)

| Phase | Inhalt | Aufwand | Abhängig von |
|---|---|---|---|
| 1 | Hotfix `mcp<2`, Dependencies, Testbarkeit, Docker-Image | 0,5 Tag | – |
| 2 | JWT härten (Refresh-Token, Lock, MFA/SSO-Erkennung), docker-compose fixen | 0,5 Tag | 1 |
| 3 | Bugfixes (`complete`, `history`), Modelle, dedizierte Endpoints | 1 Tag | 1 |
| 4 | Things-API und Trigger-Chores (mit JWT) | 1 Tag | 2, 3 |
| 5 | MCP SDK 2.x (Option A) | 1–1,5 Tage | 1–4 (am besten nach den fachlichen Änderungen) |
| 6 | README, CHANGELOG, CLAUDE.md | 0,5 Tag | alle |
| 7 | *(zurückgestellt)* API-Token- und Hybrid-Auth | 1 Tag | 2 |

Vorgeschlagene Branches/PRs: `chore/deps-hotfix` → `fix/jwt-auth` → `fix/api-compat` → `feat/things` → `refactor/mcp-v2` → `docs/update`

### Entscheidungen

- ✅ **Auth:** vorerst JWT (Username/Passwort); API-Token als Phase 7 zurückgestellt (2026-09-16)

### Offene Entscheidungen

1. **MCP 2.x:** Option A (`MCPServer`-Refactor) oder Option B (minimale Umstellung)?
2. **HTTP erlauben** für lokale Instanzen (`DONETICK_ALLOW_HTTP`)?
3. **Optionale Chore-Tools (3.9):** welche davon sind gewünscht?
4. *(für Phase 7)* Upstream-PR bei Donetick, der Labels und `users/profile` auf MultiAuth umstellt?

---

## 6. CLAUDE.md: Befunde und geplante Änderungen

Die CLAUDE.md ist **nicht mehr aktuell**. Konkrete Abweichungen zum Code bzw. zur Donetick-API:

| Stelle in CLAUDE.md | Aussage | Tatsächlich |
|---|---|---|
| Architektur-Diagramm | „Exposes 5 tools“ | 20 Tools |
| Running the Server | Entry-Point `donetick-mcp` | `pyproject.toml`: `donetick-mcp-server` |
| Tools / API Endpoints | `skip_chore` = `PUT /skip` | `POST /api/v1/chores/{id}/skip` (Client ist korrekt) |
| Tools | `complete_chore` „(Premium feature)“ | bei `/api/v1` nicht Plus-gebunden (nur `eapi`); widerspricht auch „No Premium restrictions“ |
| API Client | „Caching for individual chore fetches (60s TTL)“ | Im Client gibt es kein Caching |
| JWT Token Management | „Automatic refresh before expiration“ | nur Re-Login nach `401`; kein Refresh-Token-Flow |
| Configuration | nur Username/Passwort, API-Token „deprecated“ | Donetick unterstützt API-Token (`secretkey`) offiziell, er ist hier nur bewusst nicht umgesetzt. `docker-compose.yml` nutzt fälschlich nur `DONETICK_API_TOKEN` |
| All Features Supported | „No Premium/Plus membership restrictions“, „Labels (create, update, delete)“ | stimmt nur mit JWT; mit API-Token keine Labels |
| Update Chore | entfernt „assignees“ | `FIELDS_TO_REMOVE` enthält `assignees` nicht |
| API Endpoints | Labels, History, Details, Users fehlen in der Liste | ergänzen, inkl. Auth-Spalte |
| Project Structure / Testing | nur `test_client.py`, `test_server.py` | außerdem `test_models.py`, `test_integration.py`, `test_performance.py`, `test_transformations.py`, `test_notification_transform.py` |
| Testing | „requires no Donetick instance“ | Collection bricht ohne gesetzte `DONETICK_*`-Env-Variablen ab |
| Linting | `ruff check src/` | `ruff` ist keine Dev-Dependency |
| Important File Locations | z. B. `server.py:34-150` Tool-Definitionen, `client.py:118-199` Retry | tatsächlich ca. `server.py:37-727` / `729-1560`, `client.py:185-284` usw. Zeilennummern komplett veraltet |
| Recent Enhancements | v0.4.0 „Phase 1 Foundation Complete“ | Paketversion ist 0.3.13; die Reihenfolge der Versionen ist durcheinander |
| Key Technologies | `MCP SDK (>=1.20.0)` | faktisch mit 2.x inkompatibel |
| Notifications | Beispiele in Python-Aufrufsyntax | ok, aber `notificationMetadata`-Format mit aktueller API abgleichen |

### Geplante Struktur der neuen CLAUDE.md

1. **Project Overview:** kurz; Fork-Hinweis (Upstream `jason1365`, Fork `Duell10111`); unterstützte Donetick-Version
2. **Commands:** Setup, Tests (inkl. Env-Hinweis), Lint, Docker; alles verifiziert
3. **Architecture:** Module inkl. `tools/`-Aufteilung nach Phase 5; Auth-Strategien
4. **Authentication:** JWT-Flow (Login, Refresh, Re-Login), MFA- und SSO-Einschränkungen, kurze Auth-Matrix (aus 1.2) mit Begründung, warum kein API-Token
5. **Donetick API Quirks:** kompakt: ID im Body bei `PUT /chores/` und `PUT /things`, `updatedAt`-Locking, `assignedTo ∈ assignees`, Trailing Slashes, `res`-Wrapping, `history?limit` = Tage, Things user-scoped, `thingTrigger` vs. `thingChore`
6. **Adding a Tool:** Checkliste inkl. Auth-Anforderung (multi/jwt) und Tests
7. **Conventions:** `tmp/` für Ad-hoc-Skripte, camelCase in Modellen, keine Zeilennummern (veralten schnell), stattdessen Symbolnamen
8. **Changelog-Historie entfernen** (steht in `CHANGELOG.md`)

Ziel: CLAUDE.md von ca. 800 auf ca. 250 Zeilen kürzen. Nur Infos, die nicht direkt aus dem Code ablesbar sind, plus verifizierte Befehle.

---

## Anhang: Quellen

- Donetick Source: https://github.com/donetick/donetick (`v0.1.79`)
  - `internal/auth/multiauthmiddleware.go`, `internal/auth/api_middleware.go`: Auth
  - `internal/chore/handler.go` (`Routes`, `ChoreReq`, `CompleteChoreReq`, `GetChoresHistory`)
  - `internal/thing/handler.go`, `internal/thing/model/model.go`, `internal/thing/helper.go`
  - `internal/label/handler.go`, `internal/user/handler.go`: JWT-only-Routen
  - `docs/swagger.yaml`: `securityDefinitions` (`APIKeyAuth` = Header `secretkey`)
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk/releases, Migration Guide: https://py.sdk.modelcontextprotocol.io/v2/migration/
- PyPI-Versionen abgefragt am 2026-09-16
