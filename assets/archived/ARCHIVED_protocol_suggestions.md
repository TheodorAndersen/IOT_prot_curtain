# LoRaWAN Protocol Suggestions (v3 → v4 Proposal)

Recommendations for the Group 7 firmware team to cut TTN downlink usage under the **10 downlinks/device/day** fair-use limit without increasing airtime.

---

## 1. The Downlink Budget Problem in v3

In [`Smart_Curtains_LoRaWAN_Protocol_v3.pdf`](Smart_Curtains_LoRaWAN_Protocol_v3.pdf), `type 3 (set_auto_settings)`, `type 2 (set_mode)`, and `type 4 (set_schedule)` are separate downlinks:

| Flow | v3 Baseline Downlinks | v3 + Backend Optimizations | Proposed v4 Consolidated |
|---|---|---|---|
| Reboot (`startup`) in `manual` mode | 2 (`type 3` + `type 2`) | **1** (`type 3`, skips redundant `type 2`) | **1** (`type 3` consolidated) |
| Reboot (`startup`) in `automatic` mode (time-based) | **3** (`type 3` + `type 4` + `type 2`) | 2–3 (`type 4` skipped if `is_day` matches) | **1** (`type 3` consolidated) |
| `PUT /mode {"mode": "automatic"}` (time-based) | **2** (`type 4` + `type 2`) | 1–2 (`type 4` skipped if `is_day` matches) | **1** (`type 3` consolidated) |
| Change only `autoOpenTime` / `autoCloseTime` (`PUT /auto`) | 1 (`type 3`) | **0** (skipped in backend) | **0** (skipped in backend) |
| Save unchanged thresholds (`PUT /settings`) or same mode (`PUT /mode`) | 1 | **0** (skipped in backend) | **0** (skipped in backend) |

Notice that a single reboot in `automatic` mode currently consumes **30% of the entire daily TTN downlink quota** (and up to 6 attempts if a confirmation is missed).

---

## 2. Proposal: Consolidate Mode + Day/Night into `type 3` Unused `cfg` Bits (v4)

In v3, `type 3` is `[cmd_id, 0x03, cfg, target, thrOpen, thrClose]` (6 bytes), and **`cfg` only uses bits `0..2`**, leaving **5 unused bits (`3..7`)** in the exact same 6-byte frame (zero extra airtime).

### Proposed `cfg` Bitmask (Byte 2 of `type 3`)

| Bit | Mask | Name | Meaning |
|---|---|---|---|
| `0` | `0x01` | `open_by_sun` | `1` = open when outdoor light > `thrOpen`, `0` = backend schedule (`autoOpen`) |
| `1` | `0x02` | `close_by_sun` | `1` = close when outdoor light < `thrClose`, `0` = backend schedule (`autoClose`) |
| `2` | `0x04` | `keep_indoor_light` | `1` = regulate indoor light during `day` (`maintainLight`) |
| **`3`** | **`0x08`** | **`automatic`** | **`1` = `automatic` mode, `0` = `manual` mode** *(read only if bit 5 = 1)* |
| **`4`** | **`0x10`** | **`is_day`** | **`1` = day/open, `0` = night/close** *(read only if bit 5 = 1)* |
| **`5`** | **`0x20`** | **`apply_mode_and_day`** | **`1` = apply bits 3 & 4 atomically; `0` = settings-only (100% v3 compatible)** |
| `6..7` | `0xC0` | Reserved | Always `0` |

### Why bit 5 (`apply_mode_and_day`) makes v4 clean & safe
- When `PUT /auto` or `PUT /settings` changes thresholds while the user is editing settings, the backend sends `type 3` with **`apply_mode_and_day = 0`** — identical to v3, so changing thresholds never accidentally overrides `mode` or `is_day`.
- On **`startup`** or **`PUT /mode("automatic")`**, the backend sends **one** `type 3` with **`apply_mode_and_day = 1`**, `automatic`, and `is_day` set — replacing `type 3` + `type 4` + `type 2` (3 downlinks → **1 downlink**).
- `type 1`, `type 2`, `type 4`, and the 4-byte `FPort 1` uplink format remain **unchanged** for simple single-purpose commands (`POST /position`, hourly schedule transitions, and `POST /sync`).

---

## 3. Additional Firmware Suggestions (Optional)

1. **Persist `cfg` + `target` + `thrOpen` + `thrClose` in EEPROM:**
   - Calibration travel is already stored in EEPROM. If `type 3` settings are also saved to EEPROM (only rewritten when values actually change to protect flash wear), the device retains its configuration across brownouts/reboots and `startup` sync could become even lighter.
2. **Optional `FPort 2` Hourly Outside Light Telemetry:**
   - The backend already supports `FPort 2` (`>BB12H`, 26 bytes, twelve 5-minute raw ADC readings `0–1024` per hour, `~66 ms` at SF7 ≈ `1.6 s/day`, well within the 30 s/day uplink budget) via `GET /telemetry` if firmware chooses to send it.
