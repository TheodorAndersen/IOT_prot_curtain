# IoT Automatic Curtain Motor — Quickstart & Operations Guide

Backend, REST API, LoRaWAN v4 codec, React SPA/PWA, and containerized deployment stack for the Smart Curtain Motor prototype.

## Architecture & Container Versions

- **`postgres` (`postgres:19-alpine`)**: PostgreSQL 19 on an isolated `internal: true` bridge network (`db_internal`) with **zero host ports** (never exposed to the host or internet) and `scram-sha-256` password authentication enforced on all connection types.
- **`backend` (`python:3.15-slim`)**: FastAPI + `asyncpg` + TTN MQTTS / `MockTransport` service driven declaratively by [`backend/protocol.json`](backend/protocol.json) (v4 default) or [`backend/protocol.v3.json`](backend/protocol.v3.json) (`PROTOCOL_VERSION=v3`). Exposes internal port `8000` only on `app_net` (zero host ports).
  - Specs & protocol: [`assets/PROTOCOL_FINAL.md`](assets/PROTOCOL_FINAL.md), [`assets/iot_curtain_motor_backend_specification.md`](assets/iot_curtain_motor_backend_specification.md), [`assets/iot_curtain_motor_frontend_specification.md`](assets/iot_curtain_motor_frontend_specification.md) (legacy docs are in [`assets/archived/`](assets/archived/)).
- **`nginx` (`nginx:1.31.9-alpine`)**: Serves the built React SPA/PWA (`/` from [`FrontEnd/`](FrontEnd/)) and reverse-proxies REST calls (`/api/*` and `/health`) to `backend:8000` (pinned to `1.31.9` to avoid CVE-2026-123634). Bound strictly to `127.0.0.1:8080` locally, or `80`/`443` with TLS 1.2/1.3 in GCP production.
- **`certbot` (`certbot/certbot:latest`)**: Let's Encrypt issuance and automatic 12-hour certificate renewal in GCP production.

---

## Ports, Addresses & Routes

### Local Docker Stack (`docker compose up -d --build`)
Bound strictly to `127.0.0.1` on host port `${NGINX_HTTP_PORT:-8080}` (default `8080`):

| Address | What It Serves |
|---|---|
| `http://127.0.0.1:8080/` | React Web App / PWA Control page (curtain drag/presets, staged Manual/Automatic mode, rules & inline sunrise/sunset thresholds, 24h downlink budget) |
| `http://127.0.0.1:8080/login` | Login screen (`demo@example.com` / `curtain` by default, or `DEMO_USER_PASSWORD` from `.env`) |
| `http://127.0.0.1:8080/settings` | Settings screen (outdoor sunlight thresholds, language, theme, command confirmation mode, backend URL override) |
| `http://127.0.0.1:8080/debug` | Debug & Diagnostics page (transport/protocol state, 7-bit flags inspector, last command sent & queue, last uplink received, unified DL/UL wire log, Force State Sync) |
| `http://127.0.0.1:8080/health` & `/api/health` | Health check JSON (`ok`, `transportConnected`, `protocolVersion`) |
| `http://127.0.0.1:8080/api/docs` | Swagger UI interactive API docs (`/api/openapi.json` schema) |
| `http://127.0.0.1:8080/api/*` | REST API proxied to `backend:8000` (`/api/login`, `/api/status`, `/api/position`, `/api/mode`, `/api/auto`, `/api/settings`, `/api/telemetry`, `/api/sync`, `/api/debug`, `/api/openapi.json`) |

### Internal Docker-Only & Outbound Addresses (Zero Host Ports)

| Address / Network | Service & Access |
|---|---|
| `http://backend:8000` (`app_net` bridge) | FastAPI backend container — internal only (not published to the host by `docker-compose.yml`) |
| `postgres:5432` (`db_internal` `internal: true` bridge) | PostgreSQL 19 container — zero host ports; inspect via `docker compose exec postgres psql -U curtain -d curtain` |
| `<TTN_REGION>.cloud.thethings.network:8883` (outbound TLS) | TTN MQTTS broker when `TRANSPORT_MODE=ttn_mqtt` (disabled when `TRANSPORT_MODE=mock`) |

### GCP Production (`deploy/gcp/docker-compose.prod.yml`) & Local Dev / Test Ports

| Address | Purpose |
|---|---|
| `http://<DOMAIN>:80` | Let's Encrypt ACME challenge (`/.well-known/acme-challenge/`) & HTTP-to-HTTPS redirect |
| `https://<DOMAIN>:443` | Production Web/PWA (`/`, `/login`, `/settings`, `/debug`), `/health`, and `/api/*` over TLS 1.2/1.3 |
| `http://localhost:5173` | Optional Vite dev server (`cd FrontEnd && npm run dev`; proxies `/api` and `/health` to `http://127.0.0.1:8000`) |
| `http://127.0.0.1:8000` | Optional bare-metal FastAPI backend (`APP_HOST` / `APP_PORT` in `.env`, Swagger UI at `http://127.0.0.1:8000/docs`) |
| `http://localhost:8080` | Local USB / TTN bench harness (`python FrontEnd/local_test/server.py`; conflicts with Docker `nginx` if both run on port `8080` simultaneously — change `NGINX_HTTP_PORT` or stop one first) |
| `http://127.0.0.1:18080` | Temporary port used by `backend/tests/test_docker_e2e.py` (`curtain-e2e-test` Compose project) |

---

## 1. Quickstart (Local Docker)

### Configure `.env`
Copy [`.env.example`](.env.example) to `.env` if you don't already have one, and fill in your credentials:
- `TRANSPORT_MODE` (`ttn_mqtt` for live hardware, or `mock` for offline UI/simulator testing without using TTN downlink quota)
- `MQTT_API_KEY`, `TTN_APPID`, `TTN_DEVID`, `TTN_REGION` (required when `TRANSPORT_MODE=ttn_mqtt`)
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (when `POSTGRES_PASSWORD` is set, the backend builds the DSN automatically and safely URL-encodes special characters)
- `DEMO_USER_EMAIL`, `DEMO_USER_PASSWORD` (plaintext login password for the website; optional, takes precedence over `DEMO_USER_PASSWORD_MD5`)

### Start Stack (`postgres` + `backend` + `nginx`)
```powershell
# Uses TRANSPORT_MODE from .env
docker compose up -d --build

# Or override to offline simulator mode (MockTransport — does not touch TTN downlink quota)
$env:TRANSPORT_MODE="mock"; docker compose up -d --build
```

### Logs, Shutdown & Clean Reset
```powershell
# Follow backend logs
docker compose logs -f backend

# Stop all containers (keeps database data)
docker compose down

# Stop and delete persisted Postgres volume
docker compose down -v

# Full reset: remove containers, Postgres volume, and project-built images, then rebuild cleanly
docker compose down -v --rmi local --remove-orphans
docker compose build --no-cache
docker compose up -d
```

---

## 2. Running Tests

From the repository root using the project virtual environment (`.venv`):

```powershell
# Run ALL tests (protocol unit + FastAPI/workflow + local Mosquitto MQTT + full Docker Compose E2E)
.\.venv\Scripts\python.exe -m pytest backend/tests -v

# Fast tests only (skip the Docker container tests)
.\.venv\Scripts\python.exe -m pytest backend/tests -m "not docker" -v

# Pure LoRaWAN codec unit tests only (protocol.py + protocol.json)
.\.venv\Scripts\python.exe -m pytest backend/tests/test_protocol.py -v

# Local Eclipse Mosquitto MQTTS integration test only
.\.venv\Scripts\python.exe -m pytest backend/tests/test_mqtt_local.py -v

# Full-stack Docker Compose E2E & GCP config verification test only
.\.venv\Scripts\python.exe -m pytest backend/tests/test_docker_e2e.py -v
```

*(Ensure Docker Desktop is running before executing `test_mqtt_local.py` or `test_docker_e2e.py`.)*

---

## 3. Using the Web App & REST API

- **Web App / PWA (local Docker):** `http://127.0.0.1:8080/` (routes: `/`, `/settings`, `/debug`)
- **API Base URL (via Nginx):** `http://127.0.0.1:8080/api`
- **Default seeded login:** `demo@example.com` / password `curtain` (MD5: `2805ba35a46fd703543fb61625cd9393`)

```powershell
# 1. Health check (no auth)
Invoke-RestMethod http://127.0.0.1:8080/health

# 2. Login -> Bearer token
$login = Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/login `
  -ContentType "application/json" `
  -Body '{"username":"demo@example.com","password":"2805ba35a46fd703543fb61625cd9393"}'
$headers = @{ Authorization = "Bearer $($login.token)" }

# 3. Get current curtain status, saved settings & rolling 24h downlink budget
Invoke-RestMethod http://127.0.0.1:8080/api/status -Headers $headers

# 4. Move curtain (allowed in manual or automatic mode; 0 = fully open, 100 = fully closed)
Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/position -Headers $headers `
  -ContentType "application/json" -Body '{"position":40}'

# 5. Switch mode ("manual" or "automatic")
Invoke-RestMethod -Method Put http://127.0.0.1:8080/api/mode -Headers $headers `
  -ContentType "application/json" -Body '{"mode":"automatic"}'

# 6. Update automatic settings (all 6 fields required; 0/1 strict integers)
Invoke-RestMethod -Method Put http://127.0.0.1:8080/api/auto -Headers $headers `
  -ContentType "application/json" `
  -Body '{"autoOpen":0,"autoOpenTime":"07:30","autoClose":1,"autoCloseTime":"21:00","maintainLight":1,"targetLightLevel":50}'

# 7. Update outdoor sun thresholds (outsideThresholdClose < outsideThresholdOpen)
Invoke-RestMethod -Method Put http://127.0.0.1:8080/api/settings -Headers $headers `
  -ContentType "application/json" -Body '{"outsideThresholdOpen":80,"outsideThresholdClose":25}'

# 8. Fetch optional FPort 2 outdoor light telemetry (hours: 1..168)
Invoke-RestMethod "http://127.0.0.1:8080/api/telemetry?hours=24" -Headers $headers

# 9. Force a 1-downlink state sync without toggling mode (useful on a fresh DB)
Invoke-RestMethod -Method Post http://127.0.0.1:8080/api/sync -Headers $headers

# 10. Inspect live device state, bitfields, command queue, and uplink wire log
Invoke-RestMethod "http://127.0.0.1:8080/api/debug?limit=20" -Headers $headers
```

> **Live TTN fresh-DB tip:** On a brand-new database, `calibrated` starts `false` until the first `FPort 1` uplink arrives. If your curtain is already calibrated in EEPROM, call `POST /api/sync` (or set `SYNC_ON_STARTUP=true` in `.env` so the backend sends a 1-downlink `set_mode` automatically when `last_seen_at IS NULL`).

---

## 4. Frontend (React Web, PWA, Capacitor Mobile) & Local USB Harness

- **React Web, PWA & Mobile App ([`FrontEnd/`](FrontEnd/)):** Implements [`assets/iot_curtain_motor_frontend_specification.md`](assets/iot_curtain_motor_frontend_specification.md) using React 19, TypeScript, Vite 6, TanStack Query, `vite-plugin-pwa`, and Capacitor (`FrontEnd/capacitor.config.ts`). Built automatically into the `nginx` container by [`FrontEnd/Dockerfile`](FrontEnd/Dockerfile).
  - **Local frontend dev:** In [`FrontEnd/`](FrontEnd/), run `npm install`, `npm run dev` (proxies `/api` and `/health` to `http://127.0.0.1:8000`), `npm test`, and `npm run build`.
  - **Native mobile (Capacitor):** Set `VITE_API_BASE_URL=https://<your-domain>/api` when building, then run `npx cap add ios` / `npx cap add android` and `npx cap sync` from [`FrontEnd/`](FrontEnd/).
- **Local USB / TTN Bench Tool:** [`FrontEnd/local_test/`](FrontEnd/local_test/README.md) remains available for direct USB serial testing (`BTN U|D|S`, `TICK`, `CAL`, `LINK U|L`) against the Arduino Pro Mini in [`firmware/smart_curtain/`](firmware/smart_curtain/smart_curtain.ino).

---

## 5. GCP Production Deployment (HTTPS)

See [`deploy/gcp/README.md`](deploy/gcp/README.md) for the complete GCP Compute Engine VM, firewall, Let's Encrypt (`init-letsencrypt.sh`), and `systemd` setup (`deploy/gcp/docker-compose.prod.yml`). Ensure `POSTGRES_PASSWORD`, `DOMAIN`, and `CERTBOT_EMAIL` are set in `.env` before running `./deploy/gcp/init-letsencrypt.sh`.
