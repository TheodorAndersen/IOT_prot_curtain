# Smart Curtains — LoRaWAN Protocol v4 Specification (Draft / Opt-In)

> **Version Status:** Optional consolidated downlink protocol enabled in the backend via `PROTOCOL_VERSION=v4` in `.env` (`backend/protocol.v4.json`). Default remains **`v3`** (`Smart_Curtains_LoRaWAN_Protocol_v3.pdf`).
>
> **Compatibility:** Keeps the 4-byte `FPort 1` uplink, downlink types `1`, `2`, and `4`, and optional `FPort 2` telemetry byte-for-byte identical to v3. Only extends **Downlink Type 3 (`set_auto_settings`)** using 3 previously unused bits (`bits 3–5`) of the `cfg` byte to eliminate multi-downlink sequences on `startup` and `PUT /mode`.

---

## 1. Radio & Link Parameters (Unchanged from v3)
- **Hardware:** Arduino Pro Mini 3.3 V + Microchip RN2483 (firmware 1.0.5, OTAA EU868, Class C default).
- **TTN Fair-Use Limits:** Max **30 s/device/day** uplink airtime (unconfirmed uplinks only) and **10 downlinks/device/day** (confirmed downlinks on `down/replace`, single command in flight, max 2 attempts / 20 s timeout).

---

## 2. Uplink: Curtain State (`FPort 1`, Always 4 Bytes — Identical to v3)

Python `struct` format: `>BBBB`

| Byte | Field | Type | Values |
|---|---|---|---|
| `0` | `last_cmd_id` | `uint8` | `0` = none yet, `1–255` = last downlink `cmd_id` processed |
| `1` | `reason` | `uint8` | `0` `heartbeat` · `1` `startup` · `2` `button` · `3` `app_command` · `4` `auto_light` · `5` `schedule` · `6` `calibrated` · `7` `rejected` |
| `2` | `flags` | `uint8` | Bit 0: `automatic` · Bit 1: `is_moving` · Bit 2: `is_calibrated` · Bit 3: `is_day` · Bit 4: `open_by_sun` · Bit 5: `close_by_sun` · Bit 6: `keep_indoor_light` · Bit 7: reserved (`0`) |
| `3` | `position` | `uint8` | `% closed` (`0` = open, `100` = closed), `255` (`0xFF`) = not calibrated |

---

## 3. Downlink: Commands (`FPort 1`, 3 or 6 Bytes)

Byte `0` is always `cmd_id` (`1–255`, wrapping `255 → 1`, never `0`).

| Type | Opcode | Format | Length | Byte Layout & v4 Semantics |
|---|---|---|---|---|
| `set_position` | `0x01` | `>BBB` | 3 B | `[cmd_id, 0x01, pos]` — Move to `pos` (`0–100` % closed) and switch to `manual`. Rejected until calibrated. |
| `set_mode` | `0x02` | `>BBB` | 3 B | `[cmd_id, 0x02, mode]` — `0` = `manual`, `1` = `automatic`. Used for standalone mode changes and `POST /sync`. |
| `set_auto_settings` | `0x03` | `>BBBBBB` | 6 B | `[cmd_id, 0x03, cfg, target, thrOpen, thrClose]` (`thrClose < thrOpen`, all `0–100`) — See `cfg` bit layout below. |
| `set_schedule` | `0x04` | `>BBB` | 3 B | `[cmd_id, 0x04, action]` — `0` = open / day, `1` = close / night. Sent at scheduled `autoOpenTime` / `autoCloseTime`. |

### Downlink Type 3 `cfg` Bit Layout (v4)

| Bit | Mask | Name | Behavior |
|---|---|---|---|
| `0` | `0x01` | `auto_open` | `1` = open by sun, `0` = open by backend schedule |
| `1` | `0x02` | `auto_close` | `1` = close by sun, `0` = close by backend schedule |
| `2` | `0x04` | `maintain_light` | `1` = keep indoor light at `target` while `is_day == 1` |
| `3` | `0x08` | `automatic` | Target mode (`1` = `automatic`, `0` = `manual`). **Applied only when bit 5 = 1.** |
| `4` | `0x10` | `is_day` | Target day/night state (`1` = day/open, `0` = night/close). **Applied only when bit 5 = 1.** |
| `5` | `0x20` | `apply_mode_and_day` | `0` = update thresholds/flags only (v3 behavior); `1` = also apply `automatic` (bit 3) and `is_day` (bit 4) atomically. |
| `6..7` | `0xC0` | Reserved | Always `0` |

### Firmware Rules for Type 3 with `apply_mode_and_day = 1`
1. Apply `open_by_sun`, `close_by_sun`, `keep_indoor_light`, `target`, `thrOpen`, and `thrClose` (rejecting with `reason = 7` if `thrClose >= thrOpen`).
2. Set `is_day` from bit 4 (`0x10`).
3. Set `mode` from bit 3 (`0x08`: `1` = `automatic`, `0` = `manual`).
4. Reply immediately on `FPort 1` with `last_cmd_id = cmd_id`, `reason = 3 (app_command)`, and updated `flags` (then trigger motor move on next tick/immediately as per `automatic` rules).

---

## 4. REST → Downlink Mapping in v4

| Trigger | Downlinks Sent (v4) | Downlinks Saved vs. v3 Baseline |
|---|---|---|
| `POST /position {position}` | `type 1` (1 DL) | Same (1 DL) |
| `PUT /mode {"mode": "automatic"}` (when `is_day` differs or queued settings exist) | **`type 3`** with `apply_mode_and_day=1, automatic=1, is_day=...` (**1 DL**) | **2 → 1 DL** |
| `PUT /mode` (when `is_day` already matches & no queued settings) | `type 2` (1 DL) | Same (1 DL) |
| `PUT /mode` (same as effective mode) | **None (0 DL)** | **1 → 0 DL** |
| `PUT /auto` (times changed only) / no-op `PUT /settings` | **None (0 DL)** | **1 → 0 DL** |
| `PUT /auto` (flags/target changed) or `PUT /settings` (thresholds changed) | `type 3` with `apply_mode_and_day=0` (1 DL) | Same (1 DL) |
| Scheduled time reached (`Europe/Copenhagen`) | `type 4` (1 DL) | Same (1 DL) |
| After `startup` uplink | **1 × `type 3`** with `apply_mode_and_day=1, automatic=(desired_mode=='automatic'), is_day=...` (**1 DL**) | **2–3 → 1 DL** |
| `POST /sync` | `type 2` with current effective mode (1 DL) | Explicit resync (1 DL) |

---

## 5. Optional Outside Light Telemetry (`FPort 2`, 26 Bytes — Identical to v3)
- `>BB12H`: `[version=0x01, status_flags, light_samples[0..11]]` (`uint16` raw ADC `0–1024`, 5-minute intervals oldest → newest, timestamp back-calculated by backend from `received_at`).
