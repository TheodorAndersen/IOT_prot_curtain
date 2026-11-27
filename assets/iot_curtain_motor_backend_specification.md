# IoT Automatic Curtain Motor — Backend, REST API & LoRaWAN v4 Specification

> **Document Alignment:**
> 1. **Canonical Wire Protocol (`assets/PROTOCOL_FINAL.md`, 22 Nov 2026):** LoRaWAN Protocol **v4** on `FPort 1` is the **source of truth for the bytes** (`PROTOCOL_VERSION=v4` default via `backend/protocol.json`). Legacy `PROTOCOL_VERSION=v3` (`backend/protocol.v3.json`, restricting `cfg <= 7`) is retained strictly for backward compatibility with firmware older than 22 Nov. *(Older drafts `ARCHIVED_Smart_Curtains_LoRaWAN_Protocol_v3.pdf` and `ARCHIVED_Suggested_Smart_Curtains_LoRaWAN_Protocol_v4.md` are in `assets/archived/`.)*
> 2. **Frontend Ownership (`assets/iot_curtain_motor_frontend_specification.md`):** We now own the frontend and replace Node-RED with a single **React** codebase targeting Web, PWA, and Mobile (iOS/Android), including a dedicated **Debug Page** powered by `GET /debug`. The backend remains **100% backward-compatible** with the 6-endpoint REST contract in `assets/archived/ARCHIVED_ui_spec/ARCHIVED_communication_spec.md` (19 Nov) and `assets/archived/ARCHIVED_frontend_app/`. *(Legacy Node-RED exports were deleted.)*
> 3. **GCP Deployment Target:** Single Python service backed by **PostgreSQL**, fronted by **Nginx + Let's Encrypt SSL/HTTPS** serving both the React SPA (`/`) and the REST API (`/api/*`).

> [!WARNING]
> **Firmware Audit Callout (`firmware/`):**
> - **Obsolete v1 Firmware (`08_Curtain_Controller_older/` — deleted):** Incompatible v1 prototype (used FPort/downlink port 2, 6-byte big-endian frames, inverted `0 = closed / 100 = open` scale, 51-byte telemetry, and Class A polling only).
> - **Relevant Firmware (`firmware/smart_curtain/smart_curtain.ino`):** Matches the Group 7 LoRaWAN hardware architecture (Pro Mini 3.3 V + RN2483 EU868, `% closed` convention, 4-byte FPort 1 uplinks, USB/LoRa `LINK U|L`, EEPROM persistence). **However, the `.ino` snapshot currently checked into the repo is the 19 Nov v3 build** (`# Smart Curtains fw v3` at line 487; line 207 rejects `b[2] > 7`, and line 233 guards `decide()` with `!dir`). Before running `PROTOCOL_VERSION=v4` against physical hardware, **verify the board is flashed with the 22 Nov v4 build** described in `PROTOCOL_FINAL.md`; if the board still runs the 19 Nov sketch, set `PROTOCOL_VERSION=v3` in `.env` so the backend never transmits `cfg` bits 3–6.

---

## 0. Implementation Philosophy (Keep It Simple & Protocol-Decoupled)

* **Prototype Project — Clarity First:** Prioritize readable modules, short functions, explicit state transitions, and inspectable diagnostics over enterprise abstractions or ORM boilerplate.
* **Clean Separation Without Over-Engineering:** Keep the backend small and flat (`config.py`, `protocol.json` + `protocol.v3.json`, `protocol.py`, `models.py`, `transport.py`, `db.py`, `device_manager.py`, `main.py`), strictly isolating **device orchestration** (`device_manager.py`) from **wire byte formats** (`protocol.py` + `protocol.json`) and **network transport** (`transport.py`).
* **Plain PostgreSQL & Startup DDL:** Use PostgreSQL with plain parameterized SQL (`asyncpg`) and idempotent `CREATE TABLE IF NOT EXISTS` statements executed at startup. Generate UUIDs in Python (`uuid.uuid4()`) so no PostgreSQL extensions are needed.
* **Simple Auth & Auto-Seeding:** Auto-seed the demo account and single device from `.env` on startup. Use opaque random Bearer tokens persisted in PostgreSQL and SHA-256 verification of the client-sent 32-char lowercase MD5 hex string.
* **Backend-Owned Clock & Golden Rule:** The curtain has **no clock** and **10 downlinks/device/day** fair-use headroom on TTN. The backend owns time schedules, calculates `plan_sync` diffs **just-in-time when a command's turn comes**, and **never sends a downlink that changes nothing**.

---

## 1. System Architecture, Module Boundaries & Responsibility Split

### Recommended Flat Module Layout

```text
backend/
├── config.py             # Loads .env into a single validated Settings dataclass
├── protocol.json         # Default v4 declarative protocol definition (FPorts, opcodes, bitmasks, 25 s timeout)
├── protocol.v3.json      # Backward-compatibility v3 protocol definition (cfg <= 7 only)
├── protocol.py           # Generic pure-Python codec reading protocol JSON (zero DB/MQTT/FastAPI imports)
├── models.py             # Semantic domain dataclasses (CommandKind, uplinks) + strict Pydantic v2 REST models
├── transport.py          # Thin LoRaTransport interface: TTNMqttTransport + MockTransport
├── db.py                 # PostgreSQL pool, startup DDL, auto-seeding, plain SQL queries
├── device_manager.py     # Just-in-time state reconciliation (plan_sync), dispatch loop & schedule loop
├── main.py               # FastAPI app, lifespan, custom 400/401/409 handlers, CORS, REST & /debug routes
└── tests/
    ├── test_protocol.py  # Pure unit tests verifying protocol.py + protocol.json (v4 & v3) byte vectors
    ├── test_app.py       # FastAPI + MockTransport end-to-end workflow, plan_sync & /debug tests
    ├── test_mqtt_local.py
    └── test_docker_e2e.py
```

### Protocol & Transport Abstraction Contracts

1. **`protocol.py` + `protocol.json` (`ProtocolCodec` — pure functions, zero DB/MQTT/FastAPI imports):**
   * Loads and validates `PROTOCOL_CONFIG_PATH` (default `protocol.json` for `v4`, or `protocol.v3.json` for `v3`) into a typed `ProtocolSpec` containing FPorts, `struct` formats (with explicit endianness `>`), `cmd_id` bounds (`1–255`), `confirmed_downlinks` (`false` in v4 per `PROTOCOL_FINAL.md`), `handshake` timeouts/retries (`25 s` Class C default, configurable to `75 s` for Class A), flag bit positions, reason maps (`4 -> "auto"`), command opcodes, and optional telemetry bounds (`enabled: false` by default).
   * `decode_uplink(f_port: int, payload: bytes) -> DecodedStateUplink | DecodedLightTelemetry | None` — decodes 4-byte `FPort 1` state uplinks (`reason` enum name, `mode` `'manual' | 'automatic'`, `position` `0–100 | None` where `255 -> None`, 7 boolean flags, `raw_flags`). Returns `None` for unknown or disabled `f_port`; raises `ValueError` on malformed payloads (wrong byte length, unknown `reason`, `100 < position < 255`).
   * `encode_command(cmd_id: int, kind: CommandKind, args: dict) -> EncodedDownlink` — encodes `(cmd_id, kind, args)` into `(f_port, payload_bytes, confirmed)`. Enforces v4 bit 7 = `0` and v3 `cfg <= 7` (rejecting v4-only `set_mode`/`set_day` bits when `consolidated_config_downlink == false`).
2. **`transport.py` (`LoRaTransport` — 3 methods + `is_connected`):**
   * `is_connected: bool`
   * `async start(on_uplink: Callable[[str, datetime, int, bytes], Awaitable[None]]) -> None`
   * `async send_downlink(dev_id: str, f_port: int, payload: bytes, confirmed: bool = False) -> None`
   * `async clear_downlinks(dev_id: str) -> None` *(publishes `{"downlinks": []}` to `down/replace`)*
   * `TTNMqttTransport` (`TRANSPORT_MODE=ttn_mqtt`): publishes unconfirmed downlinks via `down/replace` (**never `down/push`**), subscribes to `up`, `join`, `down/queued`, `down/sent`, `down/failed` (plus `down/ack` / `down/nack` for passive diagnostic logging), and wraps its loop in `try/except Exception` + exponential reconnect (`2 s -> 60 s`).
   * `MockTransport` (`TRANSPORT_MODE=mock`): simulates a calibrated v4 curtain offline: emits initial `heartbeat` (`position=50`, `mode='automatic'`, `is_calibrated=True`), decodes incoming downlinks via `protocol.py`, supports independent Type 3 `set_mode` (bit 5) and `set_day` (bit 6), supports **mid-move preemption** (newest command cancels an active move and wins), emits an immediate reply (`moving=1` if a motor move starts), and emits a completion uplink (`moving=0`) after movement finishes.
3. **Single-Publisher Dispatch & Just-in-Time `plan_sync` (`device_manager.py`):**
   * REST endpoints, the uplink handler, and the schedule loop **never publish directly to MQTT** and do **not** eagerly freeze Type 2 / Type 3 / Type 4 bytes when a REST call arrives. Instead:
     * `POST /position`, `POST /sync`, and `scheduler_loop` write explicit rows (`set_position`, `set_mode` for sync, `set_schedule`) to `device_commands` (`status='QUEUED'`).
     * `PUT /mode`, `PUT /auto`, `PUT /settings`, `startup` uplinks, and the first uplink after backend boot set `devices.sync_pending = TRUE` and signal `dispatch_event.set()`.
   * A **single** `dispatch_loop` background task wakes on `dispatch_event` (or every `DISPATCH_POLL_SEC = 2 s` for timeouts), checks `transport.is_connected`, processes `IN_FLIGHT` timeouts/retries (`25 s` Class C / `75 s` Class A, max `2` attempts), and when no command is `IN_FLIGHT`, executes explicit queued commands FIFO or runs **`plan_sync()`** to compute the minimal downlink (`0` or `1` in v4) at the exact moment of transmission.

### Responsibility Split ("Who Does What")

| Job | Owner | Rationale & Firmware Detail |
|---|---|---|
| Read A0/A1 light sensors, 5-min average (`tickMs`), drive 28BYJ-48 stepper | **Device** | Operates offline with zero airtime (`STEP_US = 3000`). |
| Open/close **by sun** (outdoor A0 thresholds `thrOpen` / `thrClose`) | **Device** | Needs live outdoor sensor; includes `openArmed` hysteresis. |
| Keep indoor light at `target` ± `5 %` during `day` (max `25 %` travel/tick) | **Device** | Needs live indoor A1 sensor; skips move inside deadband. |
| Physical buttons (`Up D5` / `Down D3` / `Stop D4`) & calibration (`travel`) | **Device** | Local override ("person at the window wins"): switches to `manual` and emits `button` uplink. |
| Persist config, calibration, link & `lastId` across reboots | **Device (EEPROM)** | `smart_curtain.ino` stores `{magic, autoMode, config, target, thrOpen, thrClose, lastId, dayOpen, travel, pos, link}` in EEPROM (`MAGIC = 0xC3`). |
| Open/close **by time** (`"HH:MM"`, Danish local time) | **Backend** | Device has **no clock**; backend sends Type 4 (`set_schedule`) at the target minute. |
| Store schedule times, thresholds, desired mode & last confirmed Type 3 | **Backend** | 4-byte uplink echoes `mode`, `day`, and bits 0–2 of `cfg`, **not** times, `target`, `thrOpen`, or `thrClose`. |
| Stamp uplinks, track 24h downlink budget & serve REST / `/debug` | **Backend** | Backend timestamps arrivals and powers the React UI & Debug page. |
| UI presentation, staged position confirmation, budget badge & Debug UI | **Frontend (React)** | Owned by us; Web + PWA + Capacitor Mobile sharing one REST contract. |

---

## 2. Configuration (`.env` & `protocol.json`), Startup Seeding & PostgreSQL Schema

### Environment Configuration (`.env` — Secrets & Deployment Only)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `MQTT_API_KEY` | If `ttn_mqtt` | — | TTN API Key (uplink read + downlink write). |
| `TTN_APPID` | If `ttn_mqtt` | — | TTN Application ID (username `{TTN_APPID}@{TTN_TENANT}`). |
| `TTN_DEVID` | **Yes** | — | TTN End Device ID (seeded on startup). |
| `TTN_REGION` | No | `eu1` | TTN cluster region (`{TTN_REGION}.cloud.thethings.network`). |
| `TTN_TENANT` | No | `ttn` | TTN tenant suffix (set `""` for tenantless TTS). |
| `TTN_MQTT_HOST` | No | — | Optional broker hostname override. |
| `TTN_MQTT_PORT` | No | `8883` | Broker port (`8883` MQTTS, `1883` plain TCP). |
| `TTN_MQTT_USE_TLS` | No | `true` | Enable TLS 1.2+ on MQTT connection. |
| `TTN_MQTT_CA_CERTS` | No | — | Optional PEM CA bundle path. |
| `POSTGRES_USER` | No | `curtain` | PostgreSQL role (used when `POSTGRES_PASSWORD` is set). |
| `POSTGRES_PASSWORD` | No | — | Builds URL-encoded DSN when non-empty (overrides `DATABASE_URL`). |
| `POSTGRES_DB` | No | `curtain` | PostgreSQL database name. |
| `POSTGRES_HOST` | No | `localhost` | PostgreSQL host (`postgres` in Docker Compose). |
| `POSTGRES_PORT` | No | `5432` | PostgreSQL port. |
| `DATABASE_URL` | No | `postgresql://curtain:curtain@localhost:5432/curtain` | Fallback DSN when `POSTGRES_PASSWORD` is unset. |
| `APP_HOST` | No | `127.0.0.1` | FastAPI bind host (`0.0.0.0` in Docker). |
| `APP_PORT` | No | `8000` | FastAPI bind port. |
| `TRANSPORT_MODE` | No | `ttn_mqtt` | `ttn_mqtt` (live TTN MQTTS) or `mock` (offline simulator). |
| `PROTOCOL_VERSION` | No | **`v4`** | **`v4`** (`protocol.json`, `PROTOCOL_FINAL.md`) or `v3` (`protocol.v3.json`, `cfg <= 7` fallback). |
| `PROTOCOL_CONFIG_PATH` | No | — | Optional explicit protocol JSON path (overrides `PROTOCOL_VERSION`). |
| `HANDSHAKE_TIMEOUT_SEC` | No | — | Optional override for `protocol.handshake.timeout_sec` (`25` Class C, `75` Class A). |
| `SYNC_ON_STARTUP` | No | `false` | Enqueue `POST /sync`-style `set_mode` on startup for unseen devices (`last_seen_at IS NULL`). |
| `DISPATCH_POLL_SEC` | No | `2` | Max sleep interval for `dispatch_loop` timeout scans. |
| `SCHEDULER_POLL_SEC` | No | `10` | Interval for checking `"HH:MM"` open/close schedules. |
| `SCHEDULE_TIMEZONE` | No | `Europe/Copenhagen` | IANA timezone for schedules and `compute_day_hint`. |
| `TOKEN_TTL_HOURS` | No | `168` | Bearer token lifetime in hours (7 days). |
| `DEMO_USER_EMAIL` | No | `demo@example.com` | Seeded user email (trimmed & lowercase). |
| `DEMO_USER_PASSWORD_MD5` | No | `2805ba35a46fd703543fb61625cd9393` | MD5 hex of `"curtain"` for seeded demo login. |
| `DEVICE_NAME` | No | `Roller curtain` | Seeded device display name. |
| `DEVICE_DEV_EUI` | No | `0004A30B00E8F83E` | Seeded RN2483 hardware DevEUI. |

### Finalized Protocol Configuration (`backend/protocol.json` — LoRaWAN v4)

All wire-format details, FPorts, opcodes, bitmasks, and handshake timings live declaratively in `protocol.json` (with `confirmed_downlinks: false` and `timeout_sec: 25` per `PROTOCOL_FINAL.md`, and `telemetry_uplink.enabled: false` because firmware does not send light telemetry):

```json
{
  "version": "v4",
  "confirmed_downlinks": false,
  "features": {
    "consolidated_config_downlink": true
  },
  "cmd_id": {
    "min": 1,
    "max": 255
  },
  "handshake": {
    "timeout_sec": 25,
    "max_attempts": 2,
    "daily_downlink_warn_threshold": 10
  },
  "state_uplink": {
    "f_port": 1,
    "struct_format": ">BBBB",
    "fields": ["last_cmd_id", "reason", "flags", "position"],
    "uncalibrated_position": 255,
    "reasons": {
      "0": "heartbeat",
      "1": "startup",
      "2": "button",
      "3": "app_command",
      "4": "auto",
      "5": "schedule",
      "6": "calibrated",
      "7": "rejected"
    },
    "flag_bits": {
      "automatic": 0,
      "is_moving": 1,
      "is_calibrated": 2,
      "is_day": 3,
      "open_by_sun": 4,
      "close_by_sun": 5,
      "keep_indoor_light": 6
    }
  },
  "downlinks": {
    "f_port": 1,
    "commands": {
      "set_position": {
        "opcode": 1,
        "struct_format": ">BBB",
        "fields": ["cmd_id", "opcode", "position"]
      },
      "set_mode": {
        "opcode": 2,
        "struct_format": ">BBB",
        "fields": ["cmd_id", "opcode", "mode"],
        "mode_values": {"manual": 0, "automatic": 1}
      },
      "set_auto_settings": {
        "opcode": 3,
        "struct_format": ">BBBBBB",
        "fields": ["cmd_id", "opcode", "cfg", "target_light_level", "outside_threshold_open", "outside_threshold_close"],
        "cfg_bits": {
          "auto_open": 0,
          "auto_close": 1,
          "maintain_light": 2,
          "automatic": 3,
          "is_day": 4,
          "set_mode": 5,
          "set_day": 6
        }
      },
      "set_schedule": {
        "opcode": 4,
        "struct_format": ">BBB",
        "fields": ["cmd_id", "opcode", "action"],
        "action_values": {"open": 0, "close": 1}
      }
    }
  },
  "telemetry_uplink": {
    "enabled": false,
    "f_port": 2,
    "struct_format": ">BB12H",
    "fields": ["version", "status_flags", "light_samples"],
    "schema_version": 1,
    "sample_count": 12,
    "sample_interval_min": 5,
    "max_raw_value": 1024,
    "sensor": "outside",
    "unit": "raw_adc"
  }
}
```

> **Note on `reason = 4` (`"auto"`) & `protocol.v3.json`:**
> - `PROTOCOL_FINAL.md` and `assets/archived/ARCHIVED_ttn_lorawan_files/ARCHIVED_ttn_payload_formatter.js` name reason `4` as `"auto"` (replacing the earlier backend string `"auto_light"`).
> - `cfg` bit `7` (`0x80`) **must always remain `0`**, or the device rejects the downlink (`reason = "rejected"`).
> - `protocol.v3.json` omits `cfg_bits` `automatic`, `is_day`, `set_mode`, `set_day` (retaining only bits `0..2` so `cfg <= 7`) and sets `"consolidated_config_downlink": false`.

#### Semantic `command_args` Schemas (`snake_case`)
* `set_position`: `{"position": 0..100}`
* `set_mode`: `{"mode": "manual" | "automatic"}`
* `set_auto_settings`: `{"auto_open": 0|1, "auto_close": 0|1, "maintain_light": 0|1, "target_light_level": 0..100, "outside_threshold_open": 0..100, "outside_threshold_close": 0..100, "automatic": 0|1 (optional), "is_day": 0|1 (optional), "set_mode": 0|1 (optional), "set_day": 0|1 (optional)}`
  *(In `protocol.v3.json`, `set_mode` and `set_day` are omitted/forbidden so `cfg <= 7`.)*
* `set_schedule`: `{"action": "open" | "close"}`

### Startup DDL & Idempotent Seeding

On startup (inside FastAPI lifespan before serving requests):
1. Run `CREATE TABLE IF NOT EXISTS` for all tables below (plus additive `ALTER TABLE devices ADD COLUMN IF NOT EXISTS ...` migrations for `last_confirmed_auto_settings` and `sync_pending`), and ensure the single-in-flight guard:
   ```sql
   CREATE UNIQUE INDEX IF NOT EXISTS uq_one_inflight_per_device
     ON device_commands (device_id) WHERE status = 'IN_FLIGHT';
   ```
2. Generate UUIDs in Python (`uuid.uuid4()`) and hash `DEMO_USER_PASSWORD_MD5` via `hashlib.sha256(md5_hex.lower().encode()).hexdigest()`.
3. Seed user (`ON CONFLICT (email) DO UPDATE`), device (`ON CONFLICT (user_id) DO UPDATE`), and default settings (`ON CONFLICT (device_id) DO NOTHING`: `auto_open=0`, `auto_open_time='07:30'`, `auto_close=1`, `auto_close_time='21:00'`, `maintain_light=0`, `target_light_level=60`, `outside_threshold_open=80`, `outside_threshold_close=10`).

### PostgreSQL Schema

| Table | Columns & Types | Constraints & Notes |
|---|---|---|
| `users` | `id` (UUID PK), `email` (VARCHAR, unique), `password_hash` (VARCHAR), `created_at` (TIMESTAMPTZ) | `email` trimmed & lowercase. `password_hash` stores `SHA-256` of client-sent 32-char lowercase MD5 hex, verified via `hmac.compare_digest`. |
| `auth_tokens` | `token_hash` (VARCHAR PK), `user_id` (UUID FK → `users.id`), `created_at` (TIMESTAMPTZ), `expires_at` (TIMESTAMPTZ) | `SHA-256` of opaque Bearer token (`secrets.token_urlsafe(32)`). Prune expired rows on `POST /login`. |
| `devices` | `id` (UUID PK), `user_id` (UUID FK → `users.id`, unique), `ttn_device_id` (VARCHAR, unique), `dev_eui` (CHAR(16), unique), `name` (VARCHAR), `position` (SMALLINT, nullable), `target_position` (SMALLINT, nullable), `mode` (VARCHAR, default `'automatic'`), `desired_mode` (VARCHAR, default `'automatic'`), `is_moving` (BOOL, default `FALSE`), `is_calibrated` (BOOL, default `FALSE`), `is_day` (BOOL, default `FALSE`), `device_flags` (SMALLINT, default `0`), `last_reason` (VARCHAR, nullable), `last_cmd_id_ack` (SMALLINT, default `0`), `next_cmd_id` (SMALLINT, default `1`), **`last_confirmed_auto_settings` (`JSONB`, nullable)**, **`sync_pending` (`BOOLEAN`, default `FALSE`)**, `last_seen_at` (TIMESTAMPTZ, nullable) | `last_confirmed_auto_settings` stores the last Type 3 snapshot `{auto_open, auto_close, maintain_light, target_light_level, outside_threshold_open, outside_threshold_close}` confirmed by the device (needed because `target`, `thrOpen`, `thrClose` aren't echoed in uplinks). `sync_pending` triggers `plan_sync` at dispatch time. `position`: `0–100` or `NULL` when uncalibrated. *(Future extension note: see [`calibration_protocol_proposal.md`](calibration_protocol_proposal.md) for backend-initiated recalibration via proposed Opcode `5` while keeping calibration data in EEPROM.)* |
| `device_settings` | `device_id` (UUID PK/FK → `devices.id`), `auto_open` (SMALLINT, default `0`), `auto_open_time` (VARCHAR(5), default `'07:30'`), `auto_close` (SMALLINT, default `1`), `auto_close_time` (VARCHAR(5), default `'21:00'`), `maintain_light` (SMALLINT, default `0`), `target_light_level` (SMALLINT, default `60`), `outside_threshold_open` (SMALLINT, default `80`), `outside_threshold_close` (SMALLINT, default `10`), `last_open_sched_date` (DATE, nullable), `last_close_sched_date` (DATE, nullable), `updated_at` (TIMESTAMPTZ) | Check constraints: `auto_open`, `auto_close`, `maintain_light` in `(0, 1)`; `target_light_level`, `outside_threshold_open`, `outside_threshold_close` in `0–100`; `outside_threshold_close < outside_threshold_open`; times match `^([01]\d\|2[0-3]):[0-5]\d$`. |
| `device_commands` | `id` (BIGSERIAL PK), `device_id` (UUID FK → `devices.id`), `command_kind` (VARCHAR), `command_args` (JSONB), `cmd_id` (SMALLINT, nullable), `wire_fport` (SMALLINT, nullable), `wire_payload_hex` (VARCHAR, nullable), `status` (VARCHAR), `attempts` (SMALLINT, default `0`), `sent_at` (TIMESTAMPTZ, nullable), `resolved_at` (TIMESTAMPTZ, nullable), `created_at` (TIMESTAMPTZ) | FIFO queue & command history powering `GET /debug` (`lastCommandSent`, `inFlightCommand`, `queuedCommands`, `recentCommands`). `status` in `('QUEUED', 'IN_FLIGHT', 'CONFIRMED', 'REJECTED', 'NOT_DELIVERED', 'SUPERSEDED')`. |
| `downlink_transmissions` | `id` (BIGSERIAL PK), `device_id` (UUID FK → `devices.id`), `command_id` (BIGINT, nullable FK → `device_commands.id` `ON DELETE SET NULL`), `f_port` (SMALLINT), `confirmed` (**`BOOLEAN, default FALSE`**), `sent_at` (TIMESTAMPTZ) | Append-only log indexed on `(device_id, sent_at DESC)` inserting 1 row per `transport.send_downlink()` publish for the rolling 24h budget window. |
| `device_events` | `id` (BIGSERIAL PK), `device_id` (UUID FK → `devices.id`), `received_at` (TIMESTAMPTZ), `last_cmd_id` (SMALLINT), `reason` (VARCHAR), `mode` (VARCHAR), `flags` (SMALLINT), `position` (SMALLINT, nullable), `raw_fport` (SMALLINT), `raw_payload_hex` (VARCHAR) | Indexed on `(device_id, received_at DESC)`. Directly powers `GET /debug` (`lastUplinkReceived`, `recentUplinks`) with zero extra tables. |
| `light_telemetry` | `id` (BIGSERIAL PK), `device_id` (UUID FK → `devices.id`), `recorded_at` (TIMESTAMPTZ), `light_level` (SMALLINT) | Optional future table; empty unless `telemetry_uplink.enabled` is turned on. |

---

## 3. REST API Specification (Frontend ↔ Backend)

* **Deployment URL Layout:** FastAPI registers all endpoints at root (`/health`, `/login`, `/status`, `/position`, `/mode`, `/auto`, `/settings`, `/sync`, `/debug`, optional `/telemetry`). In production/Compose, Nginx serves the React SPA at `/` and proxies `/api/*` → `http://backend:8000/*` (preventing browser history-reload conflicts on client routes like `/settings` or `/debug`), while direct API access (legacy `ARCHIVED_frontend_app/`, `curl`, Swagger `/docs`) continues to hit root paths cleanly.
* **CORS:** Permissive `CORSMiddleware` (`allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`) so native mobile (Capacitor `capacitor://localhost`, `http://localhost`) and local Vite dev servers (`http://localhost:5173`) connect seamlessly.
* **Authentication:** Every endpoint except `POST /login` and `GET /health` requires `Authorization: Bearer <token>`. Missing/invalid tokens always return HTTP **`401`** with `{"error": "not logged in"}` (via `HTTPBearer(auto_error=False)` or header parsing).
* **Strict Validation & Error Schema:**
  * Custom FastAPI handlers map `RequestValidationError` → HTTP **`400`** `{"error": "..."}` (never `422`) and `HTTPException` / domain errors → `{"error": "..."}` (`400`, `401`, `409`).
  * All REST request models use `ConfigDict(extra="forbid")` and Pydantic `StrictInt` / `StrictStr` so booleans (`true`/`false`), floats, and unknown keys return `400`.
* **Value Formats:**
  * **Booleans:** `1` = on, `0` = off (`StrictInt`).
  * **Position:** `0–100` (`0` = fully open, `100` = fully closed), `null` when uncalibrated.
  * **Light levels & thresholds:** `0–100` (%).
  * **Times:** `"HH:MM"` 24-hour clock in `SCHEDULE_TIMEZONE` (`Europe/Copenhagen`).
  * **Password:** 32-character lowercase MD5 hex digest computed client-side.

### Endpoint Contracts

| Method & Path | Auth | Request Body / Query | Success Response | Behavior & Validation |
|---|---|---|---|---|
| `GET /health` | None | *None* | `200` `{"ok": true, "transportConnected": true, "protocolVersion": "v4"}` | Liveness, transport, and active protocol version probe. |
| `POST /login` | None | `{"username": "demo@example.com", "password": "<md5 hex>"}` | `200` `{"token": "..."}` | Trim/lowercase `username`. Verify `SHA-256(password.lower())` with `hmac.compare_digest`. Prune expired tokens, issue new Bearer token; `401` on mismatch. |
| `GET /status` | Bearer | *None* | `200` (see JSON below) | Polled every `5 s` idle (`~1.5 s` while moving). Returns the 10 canonical `communication_spec.md` fields plus additive status/budget fields (`moving`, `calibrated`, `day`, `targetPosition`, `lastSeenAt`, `downlinksUsed24h`, `downlinkBudget24h`, `downlinkNextSlotAt`). |
| `POST /position` | Bearer | `{"position": 40}` | `200` `{"ok": true}` | **Allowed in both `manual` and `automatic` modes** (v4 rule: Type 1 switches device to manual and moves in **1 downlink**). Reject with `409` `{"error": "curtain is not calibrated"}` if `is_calibrated == false`. Otherwise sets `desired_mode = 'manual'`, `target_position = position`, clears `sync_pending` mode drift (superseding conflicting queued mode/schedule/position rows), enqueues `set_position`, and wakes `dispatch_loop`. |
| `PUT /mode` | Bearer | `{"mode": "manual" \| "automatic"}` | `200` `{"ok": true}` | Validates `"manual" \| "automatic"`. Sets `desired_mode = mode` (clears `target_position = NULL` if `"automatic"`), sets `sync_pending = TRUE`, and wakes `dispatch_loop`. `plan_sync` sends 0 DLs if mode + day/night already match, Type 2 if only mode differs, or 1 Type 3 (bits 5/6) if mode + day/night or settings differ. |
| `PUT /auto` | Bearer | `{"autoOpen": 1, "autoOpenTime": "07:30", "autoClose": 0, "autoCloseTime": "22:45", "maintainLight": 1, "targetLightLevel": 35}` | `200` `{"ok": true}` | **Strict full 6-field payload required** (`400` if any missing/unknown). Persists settings (resetting `last_open_sched_date`/`last_close_sched_date` to `NULL` if that time changed), sets `sync_pending = TRUE`, and wakes `dispatch_loop`. **Time-only rule:** if only times changed and `compute_day_hint()` flips day/night right now (`!= devices.is_day`), `plan_sync` sends Type 4 (`set_schedule`); otherwise 0 DLs! |
| `PUT /settings` | Bearer | `{"outsideThresholdOpen": 80, "outsideThresholdClose": 25}` | `200` `{"ok": true}` | Strict `0–100` integers with **`outsideThresholdClose < outsideThresholdOpen`** (`400` otherwise). Persists, sets `sync_pending = TRUE`, wakes `dispatch_loop`. |
| `POST /sync` | Bearer | *None* | `200` `{"ok": true}` | Explicit 1-downlink state refresh (e.g., from Debug page or fresh DB): supersedes queued `set_mode` and enqueues `set_mode` with current `effective_mode` so the device replies immediately on `FPort 1`. |
| `GET /debug` | Bearer | Query: `?limit=20` (optional, default `20`, range `1–100`) | `200` (see JSON below) | Powers the React **Debug Page**. Returns server metadata, decoded device state & 7-bit flags, settings sync diff, 24h budget, `inFlightCommand`, `queuedCommands`, `lastCommandSent`, `lastUplinkReceived`, and recent command/uplink history. Returns `400` if `limit` is out of range. |
| `GET /telemetry` | Bearer | Query: `?hours=24` (`1–168`) | `200` | **Optional / disabled by default** (`PROTOCOL_FINAL.md` defines no `FPort 2` light data; returns `readings: []` unless `telemetry_uplink.enabled == true`). |

#### Canonical `GET /status` Response (`200 OK`)

```json
{
  "position": 50,
  "mode": "manual",
  "autoOpen": 0,
  "autoOpenTime": "07:30",
  "autoClose": 1,
  "autoCloseTime": "21:00",
  "maintainLight": 0,
  "targetLightLevel": 60,
  "outsideThresholdOpen": 80,
  "outsideThresholdClose": 10,
  "moving": false,
  "calibrated": true,
  "day": false,
  "targetPosition": null,
  "lastSeenAt": "2026-11-26T02:44:52Z",
  "downlinksUsed24h": 3,
  "downlinkBudget24h": 10,
  "downlinkNextSlotAt": "2026-11-27T01:12:04Z"
}
```

* **Uncalibrated device (`position == null`):** Never return `255` to the UI; return `"position": null`, `"calibrated": false`.
* **Effective `mode`:** Returns `desired_mode` while `sync_pending == true` or a mode-changing command (`set_mode`, `set_position`, or v4 Type 3 with `set_mode=1`) is `QUEUED` or `IN_FLIGHT`, otherwise `devices.mode`.
* **Soft Downlink Budget Advisory:** `downlinksUsed24h >= downlinkBudget24h` (`10`) logs a backend warning and enables a soft advisory warning in the React UI, **never** returning HTTP `429`.

#### New `GET /debug?limit=20` Response (`200 OK`)

```json
{
  "server": {
    "serverTime": "2026-11-26T02:45:10Z",
    "scheduleTimezone": "Europe/Copenhagen",
    "localTime": "2026-11-26T03:45:10+01:00",
    "transportMode": "ttn_mqtt",
    "transportConnected": true,
    "protocolVersion": "v4",
    "confirmedDownlinks": false,
    "handshakeTimeoutSec": 25,
    "handshakeMaxAttempts": 2
  },
  "device": {
    "ttnDeviceId": "curtain-01",
    "devEui": "0004A30B00E8F83E",
    "name": "Roller curtain",
    "position": 40,
    "targetPosition": null,
    "reportedMode": "manual",
    "desiredMode": "manual",
    "effectiveMode": "manual",
    "moving": false,
    "calibrated": true,
    "day": false,
    "computedDayHint": false,
    "flagsRaw": 36,
    "flagsHex": "0x24",
    "decodedFlags": {
      "automatic": false,
      "moving": false,
      "calibrated": true,
      "day": false,
      "openBySun": false,
      "closeBySun": true,
      "keepIndoorLight": false
    },
    "lastReason": "app_command",
    "lastCmdIdAck": 22,
    "nextCmdId": 23,
    "lastSeenAt": "2026-11-26T02:44:52Z",
    "lastSeenAgoSec": 18,
    "settingsInSync": true,
    "savedSettings": {
      "autoOpen": 0,
      "autoOpenTime": "07:30",
      "autoClose": 1,
      "autoCloseTime": "21:00",
      "maintainLight": 0,
      "targetLightLevel": 60,
      "outsideThresholdOpen": 80,
      "outsideThresholdClose": 10
    },
    "lastConfirmedAutoSettings": {
      "autoOpen": 0,
      "autoClose": 1,
      "maintainLight": 0,
      "targetLightLevel": 60,
      "outsideThresholdOpen": 80,
      "outsideThresholdClose": 10
    }
  },
  "budget": {
    "downlinksUsed24h": 3,
    "downlinkBudget24h": 10,
    "downlinkNextSlotAt": "2026-11-27T01:12:04Z"
  },
  "inFlightCommand": null,
  "queuedCommands": [],
  "lastCommandSent": {
    "id": 48,
    "cmdId": 22,
    "kind": "set_position",
    "args": { "position": 40 },
    "status": "CONFIRMED",
    "attempts": 1,
    "wireFport": 1,
    "wirePayloadHex": "160128",
    "description": "#22 go to 40 % closed",
    "createdAt": "2026-11-26T02:44:40Z",
    "sentAt": "2026-11-26T02:44:40Z",
    "resolvedAt": "2026-11-26T02:44:42Z",
    "roundTripMs": 2140
  },
  "lastUplinkReceived": {
    "id": 105,
    "receivedAt": "2026-11-26T02:44:52Z",
    "ageSec": 18,
    "lastCmdId": 22,
    "reason": "app_command",
    "mode": "manual",
    "flags": 36,
    "flagsHex": "0x24",
    "decodedFlags": {
      "automatic": false,
      "moving": false,
      "calibrated": true,
      "day": false,
      "openBySun": false,
      "closeBySun": true,
      "keepIndoorLight": false
    },
    "position": 40,
    "rawFport": 1,
    "rawPayloadHex": "16032428",
    "description": "#22 app_command · 40 % closed · manual"
  },
  "recentCommands": [],
  "recentUplinks": []
}
```

* **`DebugCommand` nullable fields:** `cmdId`, `wireFport`, `wirePayloadHex`, `sentAt`, `resolvedAt`, and `roundTripMs` are `null` while `status === "QUEUED"`; `resolvedAt` and `roundTripMs` remain `null` while `status === "IN_FLIGHT"`. `lastCommandSent` returns the most recent row with `sent_at IS NOT NULL` (ordered by `sent_at DESC, id DESC`).
* **`lastConfirmedAutoSettings`:** `null` until the first `set_auto_settings` command has been confirmed (`status = 'CONFIRMED'`).
* **`computedDayHint`:** `boolean | null` from `compute_day_hint()` (`null` when clock day/night is unknown/delegated to sensors).

---

## 4. TTN MQTT Communication & LoRaWAN Link Spec (`transport.py`)

### Device Radio & Class C Default (`PROTOCOL_FINAL.md`)
* **Hardware:** Arduino Pro Mini 3.3 V (ATmega328P 8 MHz) + Microchip RN2483 (firmware **1.0.5**, supports Class C).
* **OTAA & Class C Initialization:** Firmware joins EU868 using its hardware EUI (`0004A30B00E8F83E`), starts at SF7 (`dr 5`) and alternates SF7 / SF9 (`dr 3`) on every other failed join, sets `mac set rx2 3 869525000` and `mac set class c`, and listens for `mac_rx 1 <hex>` at any time.
* **TTN Console Requirement:** *"Supports class C"* **must** be ticked on the end device in TTN Console. Without it, `mac set class c` still succeeds locally on the RN2483, so firmware does not poll and downlinks wait up to 1 hour until the next uplink.
* **Class A Fallback:** Only if `mac set class c` fails (`invalid_param` on older RN2483) does firmware remain in Class A, polling every `60 s` (slowing per the 30 s/day airtime budget to ~2.5 min at SF7 / ~8 min at SF9 once the burst allowance drains).
* **TTN Fair-Use Limits:**
  * **Uplink airtime:** Max **30 s/device/day** (`51 ms` @ SF7 ≈ 580/day; `165 ms` @ SF9 ≈ 180/day). Never use confirmed uplinks.
  * **Downlink budget:** Max **10 downlinks/device/day**.

### `TTNMqttTransport` Connection & Topics
* **Broker:** `{TTN_REGION}.cloud.thethings.network` (default `eu1.cloud.thethings.network`), Port `8883` (MQTTS / TLS 1.2+), Auth `{TTN_APPID}@{TTN_TENANT}` / `{MQTT_API_KEY}`, reconnect backoff `2 s → 60 s`.

| Direction | Topic Pattern | Purpose |
|---|---|---|
| **Subscribe (Uplink)** | `v3/{TTN_APPID}@{TTN_TENANT}/devices/+/up` | Receives `FPort 1` unconfirmed 4-byte state uplinks. Ignores MAC-only uplinks lacking `f_port`/`frm_payload`. |
| **Publish (Downlink)** | `v3/{TTN_APPID}@{TTN_TENANT}/devices/{dev_id}/down/replace` | Sets the single active **unconfirmed** downlink (`"confirmed": false`) or clears queue (`{"downlinks": []}`). **Never use `.../down/push`.** |
| **Subscribe (Network Events)** | `.../join`, `.../down/queued`, `.../down/sent`, `.../down/failed` *(plus optional `.../down/ack`, `.../down/nack`)* | Operational logging of TTN join, queue, gateway TX, and failure events. **Sole command confirmation:** application-level `FPort 1` uplink with matching `last_cmd_id` (`confirmed: false` means LoRaWAN MAC ACKs are not used). |

---

## 5. Binary Payload Protocol (`PROTOCOL_FINAL.md` — LoRaWAN v4)

All multi-byte integers are unsigned big-endian (`>`).

### 5.1 Uplink: Curtain State (`FPort 1` — Always 4 Bytes, `>BBBB`)

| Byte | Field | Type | Range / Semantic Mapping |
|---|---|---|---|
| `0` | `last_cmd_id` | `uint8` | Last command ID applied (`0` = none yet, `1–255` = matched command ID). |
| `1` | `reason` | `uint8` | `0` `heartbeat` · `1` `startup` · `2` `button` · `3` `app_command` · **`4` `auto`** · `5` `schedule` · `6` `calibrated` · `7` `rejected` |
| `2` | `flags` | `uint8` | Bit 0 (`0x01`): `automatic` (`1` = automatic, `0` = manual)<br>Bit 1 (`0x02`): `is_moving`<br>Bit 2 (`0x04`): `is_calibrated`<br>Bit 3 (`0x08`): `is_day` (between opening & closing)<br>Bit 4 (`0x10`): `open_by_sun`<br>Bit 5 (`0x20`): `close_by_sun`<br>Bit 6 (`0x40`): `keep_indoor_light`<br>Bit 7 (`0x80`): always `0` |
| `3` | `position` | `uint8` | `% closed`: `0–100` (`0` = fully open, `100` = fully closed). `255` (`0xFF`) → `null` (not calibrated). |

**Uplink Transmission Triggers:**
* Immediately when a downlink command arrives (the reply), carrying `is_moving = 1` if the motor is about to run, or `reason = 'rejected'` if invalid.
* When the motor stops after any move (`button`, `app_command`, `auto`, `schedule` with `is_moving = 0`).
* On `startup`, on `calibrated`, and on `heartbeat` after 1 hour without any uplink (or 60 s polls in Class A fallback).

### 5.2 Downlink: Commands (`FPort 1` — 3 or 6 Bytes, LoRaWAN v4)

Byte `0` of every downlink is `cmd_id` (`1–255`, wrapping `255 → 1`; **never `0`**).

| `CommandKind` | Type | Format | Length | Byte Layout & Meaning |
|---|---|---|---|---|
| `'set_position'` | `1` (`0x01`) | `>BBB` | 3 B | `[cmd_id, 0x01, pos]` — Go to `pos` (`0–100` % closed). Switches device to `manual`. Rejected until calibrated. |
| `'set_mode'` | `2` (`0x02`) | `>BBB` | 3 B | `[cmd_id, 0x02, mode]` — `0` = `manual`, `1` = `automatic`. |
| `'set_auto_settings'` | `3` (`0x03`) | `>BBBBBB` | 6 B | `[cmd_id, 0x03, cfg, target, thrOpen, thrClose]` — Saves auto settings; with bits 5/6 also sets mode and/or day/night (v4). All values `0–100`; **`thrClose < thrOpen`**. |
| `'set_schedule'` | `4` (`0x04`) | `>BBB` | 3 B | `[cmd_id, 0x04, action]` — `0` = open / day, `1` = close / night. In `manual` mode updates `day`/`night` only without moving. |

#### Type 3 `cfg` Byte Layout (v4)

| Bit | Value | Field (`cfg_bits`) | Meaning |
|---|---|---|---|
| `0` | `0x01` | `auto_open` | `1` = open by sun (`autoOpen`), `0` = open by time |
| `1` | `0x02` | `auto_close` | `1` = close by sun (`autoClose`), `0` = close by time |
| `2` | `0x04` | `maintain_light` | `1` = keep indoor light at `target` during the day |
| `3` | `0x08` | `automatic` | Mode: `1` = `automatic`, `0` = `manual`. **Used only if bit 5 is set.** |
| `4` | `0x10` | `is_day` | Day/night: `1` = day, `0` = night. **Used only if bit 6 is set.** |
| `5` | `0x20` | `set_mode` | `1` = apply mode from bit 3 (v4). |
| `6` | `0x40` | `set_day` | `1` = apply day/night from bit 4 (v4). |
| `7` | `0x80` | *(reserved)* | **Must be `0`**, or the device rejects the message. |

**Canonical Type 3 Wire Examples** (`cmd_id = 0x16` / `22`, `keep_indoor_light = 1`, `target = 27 %` / `0x1B`, `thrOpen = 80 %` / `0x50`, `thrClose = 10 %` / `0x0A`):
* `16 03 04 1B 50 0A` — Settings only (`cfg = 0x04`, v3 style). Mode and day stay as they are.
* `16 03 7C 1B 50 0A` — Settings + automatic + day (`cfg = 0x04 | 0x08 | 0x10 | 0x20 | 0x40 = 0x7C`) in **1 downlink**.
* `16 03 24 1B 50 0A` — Settings + manual (`cfg = 0x04 | 0x20 = 0x24`). Day stays as it is.

**Why bits 5 and 6 are separate:** When both open and close are set to *"by sun"* (`auto_open == 1` and `auto_close == 1`), the device decides day/night from its outdoor sensor, so the backend must keep `set_day = 0` and avoid overwriting `is_day`.

> **v3 Compatibility Guard:** Never transmit `cfg` bits `3–6` (`cfg > 7`) to firmware older than 22 Nov; that firmware validates `b[2] <= 7` and rejects anything higher.

### 5.3 Optional Future Telemetry (`FPort 2` — Disabled by Default)

`PROTOCOL_FINAL.md` deliberately omits light telemetry (`no FPort 2`). `protocol.json` retains `telemetry_uplink` metadata with `"enabled": false` solely as a documented placeholder (26-byte `>BB12H`, twelve 5-min raw ADC samples `0–1024`, ~2 s/day at SF7 or ~6.4 s/day at SF9) should the team ever add it in firmware.

---

## 6. Core Backend Workflows (`device_manager.py`)

### A. Uplink Ingestion & Reconciliation (`handle_uplink`)
1. Ignore TTN uplinks missing `f_port` or `frm_payload`; decode Base64 and parse via `codec.decode_uplink(f_port, raw)`. Log and discard unknown ports or `ValueError` frames.
2. In one DB transaction for `DecodedStateUplink`:
   * Insert a row into `device_events` (`received_at`, `last_cmd_id`, `reason`, `mode`, `flags`, `position`, `raw_fport`, `raw_payload_hex`).
   * Update reported device fields in `devices`: `last_seen_at`, `last_reason`, `last_cmd_id_ack`, `device_flags`, `mode`, `is_moving`, `is_calibrated`, `is_day`, `position` (`NULL` if uncalibrated), clearing `target_position = NULL` when `not is_moving` or `mode == 'automatic'`.
   * **EEPROM `lastId` Continuity:** Because `smart_curtain.ino` persists `st.lastId` in EEPROM across reboots, whenever no command is `IN_FLIGHT` or `QUEUED` and `decoded.last_cmd_id > 0`, advance `devices.next_cmd_id = codec.next_cmd_id(decoded.last_cmd_id)`. This prevents fresh-DB or post-restart `cmd_id` collisions from triggering firmware duplicate-ID deduplication.
   * **Command Matching (`reason != 'startup'`):**
     * If an `IN_FLIGHT` command matches `cmd_id == decoded.last_cmd_id`: mark `'REJECTED'` (if `decoded.reason == 'rejected'`) or `'CONFIRMED'`, set `resolved_at = received_at`.
     * When a `set_auto_settings` row is marked `'CONFIRMED'`, update `devices.last_confirmed_auto_settings` with its 6 auto fields (`auto_open`, `auto_close`, `maintain_light`, `target_light_level`, `outside_threshold_open`, `outside_threshold_close`).
     * If `'REJECTED'` on `set_position`, clear `target_position = NULL`.
     * After any non-startup reply where no mode change remains pending (`not sync_pending` and no queued/in-flight `set_mode` / `set_position`), adopt `devices.desired_mode = decoded.mode` so physical button overrides (`reason == 'button'`) and rejections immediately sync `GET /status`.
   * **`startup` Uplink OR First Uplink After Backend Boot (`not synced_since_boot`):**
     * If `decoded.reason == 'startup'`: mark any stale pre-reboot `IN_FLIGHT` row as `'SUPERSEDED'`, and adopt `devices.desired_mode = decoded.mode` (*"The backend's settings win; the device's reported mode is the truth for mode"* per `PROTOCOL_FINAL.md`).
     * On either `startup` or the first uplink after the backend starts, set `devices.sync_pending = TRUE` and call `dispatch_event.set()`. `plan_sync()` will send **at most 1 Type 3** with only what differs (or nothing if already in sync).

### B. Tri-State Clock Day/Night Helper (`compute_day_hint`) & Just-in-Time `plan_sync`

Per `PROTOCOL_FINAL.md` (*"Day/night from the clock — only the sides that work by time count"*):

```python
def compute_day_hint(
    now_local: datetime,
    auto_open: int,
    auto_open_time: str,
    auto_close: int,
    auto_close_time: str,
) -> bool | None:
    now_m = now_local.hour * 60 + now_local.minute
    open_m = _hhmm_to_minutes(auto_open_time)
    close_m = _hhmm_to_minutes(auto_close_time)
    time_open = (auto_open == 0)
    time_close = (auto_close == 0)

    if time_open and time_close:
        if open_m < close_m:
            return open_m <= now_m < close_m
        if open_m > close_m:
            return now_m >= open_m or now_m < close_m
        return False
    if time_open:
        return now_m >= open_m
    if time_close:
        return False if now_m >= close_m else None
    return None
```

* **Both by time (`0, 0`):** `True` between `auto_open_time` and `auto_close_time` (supports overnight windows).
* **Only open by time (`0, 1`):** `False` before `auto_open_time`, `True` at/after `auto_open_time`.
* **Only close by time (`1, 0`):** `False` at/after `auto_close_time`, **`None` before `auto_close_time`** (unknown — never send day!).
* **Both by sun (`1, 1`):** **`None`** (device decides — never send day/night!).

#### Just-in-Time `plan_sync` Decision Matrix (Evaluated at Dispatch Time)
When `sync_pending == True` and the command's turn comes in `dispatch_loop`:
0. **Manual Mode Short-Circuit (`desired_mode == 'manual'`):** Automatic rules and day/night have no effect while in manual mode, and Type 3 reconciles them whenever entering `automatic`. Therefore while `desired_mode == 'manual'`, emit **1 × Type 2 (`set_mode: manual`)** if `reported_mode != 'manual'`, and otherwise emit **`0` downlinks** (ensuring clean startup in manual mode starts at **`0 / 10`** downlinks and manual-mode settings edits coalesce into `1 × Type 3` upon switching to `automatic`).
1. **`auto_differs` (when `desired_mode == 'automatic'`):** Compare current `device_settings` (`auto_open`, `auto_close`, `maintain_light`, `target_light_level`, `outside_threshold_open`, `outside_threshold_close`) against `devices.last_confirmed_auto_settings` **and** verify uplink flags (`open_by_sun`, `close_by_sun`, `keep_indoor_light`) match. (`True` if `last_confirmed_auto_settings IS NULL` or any field/flag differs.)
2. **`mode_differs`:** `devices.desired_mode != devices.mode`.
3. **`day_differs`:** Compute `hint = compute_day_hint(now_local, ...)`.
   * If `hint is None`: `day_differs = False`.
   * Else if `desired_mode == 'automatic'`: `day_differs = (devices.is_day != hint)`.
4. **Emit Minimal Downlink(s) and Clear `sync_pending = FALSE`:**
   * **In `v4` (`consolidated_config_downlink == true`):**
     * If `auto_differs` OR (`mode_differs` and `day_differs`): enqueue **1 × Type 3 (`set_auto_settings`)** with `set_mode = 1 if mode_differs else 0`, `automatic = 1 if desired_mode == 'automatic' else 0`, `set_day = 1 if day_differs else 0`, `is_day = 1 if hint else 0`.
     * Else if `mode_differs` only: enqueue **1 × Type 2 (`set_mode`)**.
     * Else if `day_differs` only: enqueue **1 × Type 4 (`set_schedule`)** (`"open"` if `hint` else `"close"`).
     * Else (none differ): **0 downlinks** (Golden Rule).
   * **In `v3` (`consolidated_config_downlink == false`):**
     * Enqueue only the differing commands in order: `[Type 3 (cfg <= 7) if auto_differs]`, `[Type 4 if day_differs]`, `[Type 2 if mode_differs]`.

### C. REST / Event → Downlink Summary

| Trigger | Backend Behavior & Downlink Produced |
|---|---|
| `POST /position {position}` | If not calibrated → `409`. Otherwise set `desired_mode='manual'`, `target_position=position`, clear pending `sync_pending` mode override, supersede queued `set_position`/`set_mode`/`set_schedule`, enqueue **Type 1** (always). |
| `PUT /mode {mode}` | Update `desired_mode`, set `sync_pending = TRUE`. At dispatch: `0` DL if mode + day/night match; **Type 2** if only mode differs; **Type 3 (bits 5 + 6)** in v4 if mode & day/night both differ. |
| `PUT /auto {...}` or `PUT /settings {...}` | Persist settings, set `sync_pending = TRUE`. At dispatch: **Type 3** (+ bits 5/6 if needed) if auto settings changed; **Type 4** if only times changed and `day_differs` flipped right now; otherwise **`0` DL**. |
| Open/close time reached (`Europe/Copenhagen`) | If `devices.mode == 'automatic'` and that side is time-based, enqueue **Type 4, always** (not skipped). |
| `startup` or first uplink after backend start | Set `desired_mode = decoded.mode` on `startup`, set `sync_pending = TRUE`. If `desired_mode == 'manual'`, emit `0` DL; if `'automatic'`, emit **1 × Type 3** with whatever differs (bits 5/6 as needed), or `0` DL. |
| `POST /sync` | Supersede queued `set_mode` and enqueue **1 × Type 2** with current `effective_mode`. |

### D. Single-Publisher Dispatch Loop & Handshake (`dispatch_loop`)
1. **One Command in Flight:** Never transmit while `device_commands` has `status = 'IN_FLIGHT'` (enforced by `uq_one_inflight_per_device`).
2. **Dispatch Priority (when no `IN_FLIGHT` row remains):**
   * First, check explicit FIFO rows (`WHERE device_id = $1 AND status = 'QUEUED' ORDER BY id ASC LIMIT 1`: `set_position`, scheduled `set_schedule`, `POST /sync`). Note that `POST /position` takes precedence and supersedes stale queued position/mode/schedule commands.
   * Second, if no explicit `QUEUED` row exists and `devices.sync_pending == TRUE`, run `plan_sync()` inside the dispatch transaction to materialize `0` or `1` command (`v4`), immediately clearing `sync_pending = FALSE`.
3. **Wire Allocation & Unconfirmed Publish:**
   * Allocate `cmd_id` from `devices.next_cmd_id` (`1–255`, wrapping `255 → 1`, never `0`), increment `next_cmd_id`, encode via `codec.encode_command(...)`, set `status = 'IN_FLIGHT'`, `attempts = 1`, `sent_at = NOW()`.
   * Publish to `.../down/replace` with **`"confirmed": false`**. Insert 1 row into `downlink_transmissions` and warn in logs if rolling 24h count `>= 10`.
4. **Timeout, Resend & Queue Clearing:**
   * If `NOW() - sent_at >= protocol.handshake.timeout_sec` (**`25 s`** Class C, `75 s` Class A):
     * If `attempts < 2`: re-publish the exact same `wire_payload_hex` (same `cmd_id`, unconfirmed), increment `attempts`, update `sent_at = NOW()`, log 1 row in `downlink_transmissions`. *(Firmware deduplicates repeated `cmd_id` and repeats its reply without acting twice.)*
     * If `attempts >= 2`: mark `'NOT_DELIVERED'`, `resolved_at = NOW()`, call `transport.clear_downlinks(ttn_device_id)` (so the command cannot arrive later by surprise), clear `target_position = NULL` if `set_position`, and revert `desired_mode = mode` if it was a mode change with no newer pending mode change.

### E. Time-Based Schedule Dispatcher (`scheduler_loop`)
Runs every `SCHEDULER_POLL_SEC` (`10 s`) in `SCHEDULE_TIMEZONE` (`Europe/Copenhagen`):
* For each device with `devices.mode == 'automatic'`:
  * If `auto_open == 0` and `hhmm == auto_open_time` and `last_open_sched_date != today`: **always** enqueue `set_schedule` (`{"action": "open"}`), set `last_open_sched_date = today`, wake `dispatch_loop`.
  * If `auto_close == 0` and `hhmm == auto_close_time` and `last_close_sched_date != today`: **always** enqueue `set_schedule` (`{"action": "close"}`), set `last_close_sched_date = today`, wake `dispatch_loop`.
* Schedule commands are **never skipped** on the assumption that `devices.is_day` already matches, because the device's `day` flag may have changed locally via sunlight triggers since its last uplink (`PROTOCOL_FINAL.md`).

---

## 7. Deployment on GCP — Containerized PostgreSQL, Backend, React SPA & Nginx SSL

A Docker Compose single-VM stack (`docker-compose.yml` + `deploy/gcp/docker-compose.prod.yml`) keeps operations simple while isolating PostgreSQL and serving both the React SPA and REST API over HTTPS:

1. **Compute Engine VM & `systemd` (`deploy/gcp/curtain-backend.service`):** Ubuntu/Debian VM (`e2-micro`/`e2-small`) with static IP, DNS `A` record (`DOMAIN`), and `chmod 600 .env`.
2. **PostgreSQL (`postgres:19-alpine` — Never Exposed):** Isolated `internal: true` network (`db_internal`), zero host ports, `scram-sha-256` auth, `/var/lib/postgresql` volume.
3. **Backend (`python:3.15-slim`):** Non-root `appuser`, `uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*`, zero host ports, attached to `db_internal` + `app_net`.
4. **Nginx + React Static Build + Let's Encrypt SSL (`nginx:1.31.9-alpine` + `certbot/certbot:latest`):**
   * Build the React frontend (`frontend/dist/`) in a multi-stage Node build (or dedicated Nginx image) and serve it at `/usr/share/nginx/html`:
     * `location /api/ { proxy_pass http://backend:8000/; ... }` (trailing slash strips `/api` prefix so FastAPI paths stay unchanged).
     * `location / { try_files $uri $uri/ /index.html; }` for React Router SPA routes (`/`, `/settings`, `/debug`, `/login`).
     * `Cache-Control: no-cache` for `index.html`, `manifest.webmanifest`, and `sw.js`; long-lived `Cache-Control: public, max-age=31536000, immutable` for hashed `/assets/*`.
   * Listen on `80` (ACME HTTP-01 + 301 redirect) and `443` (`http2 on;`, TLS 1.2/1.3, HSTS, security headers); `certbot` renews every 12 h.
5. **GCP Firewall:** Allow only `80/tcp`, `443/tcp`, and restricted `22/tcp`; never expose `8000` or `5432`.

---

## 8. Firmware Reference, Verification & Operational Edge Cases

### Device-Side Rules Reference (`PROTOCOL_FINAL.md` & `smart_curtain.ino`)
* **Hardware Wiring (`smart_curtain.ino`):** Outdoor LDR `A0`, Indoor LDR `A1`, Buttons `UP D5 / DOWN D3 / STOP D4` (`HIGH` = pressed, 30 ms debounce), ULN2003 coils `D6..D9` (`STEP_US = 3000`, 28BYJ-48 half-steps), RN2483 `TX → D10, RX ← D11, RST D12`.
* **5-Minute Sensor Tick (`tickMs = 300000 ms`):** Averages 1 Hz LDR samples every 5 minutes (`outPct`, `inPct` in `0–100 %`). In `automatic` mode:
  * **By sun:** opens when `outPct > thrOpen` (requires `openArmed == true`, reset when `outPct < thrClose`); closes when `outPct < thrClose` (clears `openArmed = false`).
  * **Day + keep indoor light:** aims for `target ± 5 %` (`DEADBAND = 5`). Inside deadband, stays put; outside, moves by `err = target - inPct` capped at `±25 %` (`MAX_STEP = 25`) of travel per tick. If the room is already brighter than `target`, the blind remains closed all day.
  * **Day without keep-light:** fully open (`0 % closed`). **Night:** fully closed (`100 % closed`).
* **Mid-Move Preemption (22 Nov v4):** `decide()` runs immediately after `mode`, `auto`, or `schedule` even while moving; the newest command cancels or reverses the current move and replies with `moving = 1` if starting the motor. *(Verify board has 22 Nov sketch; the 19 Nov sketch in `firmware/smart_curtain/smart_curtain.ino` still has `!dir` on line 233 and `b[2] <= 7` on line 207.)*
* **Airtime Budget Guard (`smart_curtain.ino`):** `allowance` starts at `3000 ms` and refills at `30 s/day` (`+1 ms` per `2880 ms`). Non-urgent uplinks (`button`, `auto`) defer when `allowance < costMs` (`51 ms` SF7, `165 ms` SF9); replies (`app_command`, `schedule`, `rejected`), `heartbeat`, `calibrated`, and `startup` transmit with `force = true`.
* **USB vs LoRa Link:** Selected via USB `LINK U` / `LINK L` and persisted in EEPROM (`DL <hex>` / `UL <hex>` at 9600 baud; `#...` debug lines on USB only).

### Verification Test Suite (`backend/tests/`)
1. **`test_protocol.py` (Pure Codec):**
   * Verify v4 Type 3 canonical vectors: `16 03 04 1B 50 0A` (`cfg = 0x04`), `16 03 7C 1B 50 0A` (`cfg = 0x7C`), and `16 03 24 1B 50 0A` (`cfg = 0x24`), plus Type 1/2/4 and `reason = 4 -> "auto"`.
   * Verify bit 7 (`0x80`) is never set and `protocol.v3.json` rejects `set_mode`/`set_day` (`cfg <= 7` guard).
2. **`test_app.py` (Workflows & `GET /debug`):**
   * Verify tri-state `compute_day_hint` across all 4 sun/time combinations (`(1, 0)` before close returns `None`; `(1, 1)` returns `None`).
   * Verify `plan_sync` coalescing: rapid `PUT /mode` + `PUT /auto` collapses into 1 Type 3; time-only `PUT /auto` that flips `is_day` emits Type 4 while non-flipping time edits emit `0` DLs.
   * Verify `POST /position` succeeds from `automatic` mode in 1 Type 1 downlink and returns `409` when uncalibrated.
   * Verify unconfirmed downlinks (`confirmed=False`), `25 s` timeout retry with identical `cmd_id`, `NOT_DELIVERED` queue clear, EEPROM `lastId` advancement of `next_cmd_id`, and complete `GET /debug?limit=20` payload shape (`lastCommandSent`, `lastUplinkReceived`, `decodedFlags`, `settingsInSync`).
3. **`test_mqtt_local.py` & `test_docker_e2e.py`:** Local Mosquitto TLS/TCP + full Compose verification including React SPA + Nginx `/api/` proxy routing.

### Operational Edge Cases
* **DST (`Europe/Copenhagen`):** `last_open_sched_date` / `last_close_sched_date` prevents double-firing during autumn fallback; skipped spring-forward times (02:00–03:00) do not trigger.
* **Uncalibrated & Fresh DB:** `POST /position` returns `409` until calibrated; `GET /status` returns `position: null`; Debug page provides **Force State Sync (`POST /sync`)** for already-calibrated hardware on a fresh DB.
* **Non-Application / Malformed Uplinks:** Skip uplinks missing `f_port`/`frm_payload`; discard unknown ports or invalid lengths/reasons/positions (`100 < position < 255`).
