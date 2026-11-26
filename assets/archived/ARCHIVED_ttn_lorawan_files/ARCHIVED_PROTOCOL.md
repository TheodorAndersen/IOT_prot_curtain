# Smart Curtains — LoRaWAN protocol v3

Group 7 · 19 Nov 2026 · Pro Mini 3.3 V + RN2483 → TTN (EU868).
Firmware: [../firmware/smart_curtain](../firmware/smart_curtain/smart_curtain.ino) · local USB test: [../local_test](../local_test/README.md) · TTN formatter: [ttn_payload_formatter.js](ttn_payload_formatter.js) (`node test_formatter.js`).

v3 follows the UI team's spec (Andreas, 19 Nov): **position is % closed (0 = open, 100 = closed)**, modes are manual / automatic, and automatic has open/close by time or by outdoor light, plus "keep indoor light".

## The idea in one line
**The curtain sends its state (4 bytes) only when something changes: after every move, when a command arrives, and once an hour as a "still alive" message. Commands arrive at any time (Class C). The backend owns the clock.**

## Who does what
| Job | Where | Why |
|---|---|---|
| Read light, decide every 5 min (5-min average), move the motor | Device | Works offline, no airtime |
| Open/close **by sun** (outdoor thresholds) | Device | Needs the sensor |
| Keep indoor light at the target (only while "day") | Device | Needs the sensor |
| Open/close **by time** ("07:30") | Backend: sends a `schedule` command at that minute | The device has no clock |
| Store the settings shown in the app (`GET /status`) | Backend | Device echoes mode and flags only |

## Limits
TTN fair use: 30 s uplink airtime and 10 downlinks per device per day. A 4-byte uplink costs 51 ms at SF7 (580/day possible) and 165 ms at SF9 (180/day). The firmware has a budget guard that refills at 30 s/day. Never use confirmed uplinks. Pro Mini use: 39 % flash, 40 % RAM (with the RN2483 link).

## Uplink (device → backend), FPort 1, always 4 bytes
| Byte | Content |
|---|---|
| 0 | Last command ID applied (0 = none yet) |
| 1 | Reason: 0 heartbeat · 1 startup · 2 button · 3 app command · 4 auto (light) · 5 schedule · 6 calibrated · 7 rejected |
| 2 | Flags: bit 0 automatic · bit 1 moving · bit 2 calibrated · bit 3 day (between opening and closing) · bit 4 open by sun · bit 5 close by sun · bit 6 keep indoor light |
| 3 | Position, % closed (0 = open, 100 = closed). 255 = unknown (not calibrated) |

When it sends: when the motor stops after any move (`button`, `app_command`, `auto`, `schedule`) · right after a command arrives, **before** the motor starts (with `moving`) · `startup` · `calibrated` · `heartbeat` after 1 hour of silence.

## Downlink (backend → device), FPort 1
First byte is always the command ID (1–255, then back to 1; never 0).

| Type | Bytes | Meaning |
|---|---|---|
| 1 position | `[id] 01 [pos]` | Go to pos % closed. Switches to manual. Rejected until calibrated. |
| 2 mode | `[id] 02 [0 manual / 1 automatic]` | |
| 3 auto settings | `[id] 03 [cfg] [target] [thrOpen] [thrClose]` | cfg bit 0 open by sun, bit 1 close by sun, bit 2 keep indoor light. thrClose must be < thrOpen. |
| 4 schedule | `[id] 04 [0 open / 1 close]` | Backend sends this at the open/close time. In manual mode it only sets day/night. |

REST → downlink mapping (the backend does this):
- `POST /position {position}` → type 1
- `PUT /mode {mode}` → (if switching to automatic and a side is time-based: type 4 with the current day/night first) → type 2
- `PUT /auto {...}` and `PUT /settings {...}` → type 3 with everything (cfg from `autoOpen`, `autoClose`, `maintainLight`)
- At `autoOpenTime` / `autoCloseTime` (Danish local time), if automatic and that side isn't by sun → type 4
- After a `startup` uplink → resend type 3 and type 2 (the backend's settings win)

## The handshake
1. One command at a time. The app sends only the final value (handle released).
2. The device replies at once with the same ID (`app_command`, or `schedule`, or `rejected`). Then a second message when the motor stops.
3. The backend marks it **confirmed** when the ID matches. No reply → resend once with the same ID (the device repeats its reply and doesn't act twice). Then "not delivered".
4. After any reply, the backend takes the mode from the device (it may have refused, or a button may have switched to manual).

## Device rules
- **Every 5 min (tick):** average the light, then in automatic mode:
  - by sun: open when outdoor > thrOpen (after it has been dark), close when outdoor < thrClose
  - day + keep indoor light: if indoor is off target by > 5 %, move by the error (max 25 % of travel per tick); otherwise day = fully open; night = fully closed
- **Buttons** (Up D3 / Down D4 / Stop D5): Up = fully open, Down = fully closed, Stop = stop. Any press switches to manual: the person at the window wins.
- **Calibration** (no home switch): curtain fully down by hand → Up → Stop at the top. Travel and position are saved in EEPROM.
- **No clock** on the device. The backend stamps messages on arrival.

## Commands on demand: Class C
Our RN2483 has firmware **1.0.5** (checked 19 Nov), so it supports Class C. The firmware joins with OTAA (DevEUI = the module's hardware EUI `0004A30B00E8F83E`, SF7, falls back to SF9 every other failed join), sets `mac set rx2 3 869525000` and `mac set class c`, and then receives `mac_rx 1 <hex>` at any time. TTN also needs **"Supports class C"** ticked on the end device. If `mac set class c` fails, the firmware stays in Class A and sends a state uplink every 60 s (within its airtime budget) so downlinks can get through.

The backend sends downlinks with MQTT `down/replace` (never `push`): there is only ever one command in flight, and a resend replaces the copy that is still queued. If a command gets no reply, the backend clears the queue, so it can't arrive later by surprise.

## Links: USB test and LoRaWAN live
The same bytes travel over either link. The firmware's link is chosen over USB with `LINK U` / `LINK L` and saved in EEPROM: USB = `DL <hex>` / `UL <hex>` lines and the radio is held in reset; LoRa = the RN2483 carries FPort 1. The local test server (`../local_test`) has the matching switch: USB serial, or TTN over MQTT.
