# Smart Curtains — LoRaWAN protocol v4.1

Group 7 · 26 Nov 2026 · Pro Mini 3.3 V + RN2483 → TTN (EU868). **This file is the source of truth for the bytes.**
Firmware: [../firmware/smart_curtain](../firmware/smart_curtain/smart_curtain.ino) · USB bridge: [../usb_bridge](../usb_bridge/README.md) · TTN formatter: [ttn_payload_formatter.js](ttn_payload_formatter.js) (`node test_formatter.js`).

Position is **% closed (0 = open, 100 = closed)**, modes are manual / automatic, and automatic opens/closes by time or by outdoor light, plus "keep indoor light". This follows the UI team's REST spec (19 Nov).

## What's new in v4.1 (26 Nov)
v4.1 is v4 plus **calibration started from the app**. A v4 message means exactly the same thing in v4.1.
1. **Downlink type 5 `calibrate`:** `[id] 05 01` makes the device forget its travel and wait for the buttons (see [Calibration](#calibration-v41)).
2. **Uplink flag bit 7 = calibrating.** While it is set, the position byte is 255 until the bottom is set, then 100.

The firmware (v4.1), the backend (`POST /calibrate`) and the React app (Settings → Calibrate) speak v4.1. Tested over USB on the board on 26 Nov.

## What's new in v4
v4 is v3 plus one optional extension. A v3 message means exactly the same thing in v4.
1. **Settings, mode and day/night in one downlink.** Type 3 can now also set the mode and day/night (cfg bits 3–6). This saves downlinks: switching to Automatic, or resyncing after a restart, now takes 1 downlink instead of 2–3.
2. **Never send a downlink that changes nothing.** The backend compares with what the device last reported (see [Backend rules](#backend-rules-rest--downlink)).
3. **The newest command wins, even mid-move.** The reply then says `moving` if the motor is about to run (fixed in the firmware on 22 Nov).

The firmware and the local test server speak v4 (tested on the board, 22 Nov). An old backend that only sends v3 messages still works with it.

## The idea in one line
**The curtain sends its state (4 bytes) only when something changes: after every move, when a command arrives, and once an hour as a "still alive" message. Commands arrive at any time (Class C). The backend owns the clock.**

## Who does what
| Job | Where | Why |
|---|---|---|
| Read light, decide every 5 min (5-min average), move the motor | Device | Works offline, no airtime |
| Open/close **by sun** (outdoor thresholds) | Device | Needs the sensor |
| Keep indoor light at the target (only while "day") | Device | Needs the sensor |
| Open/close **by time** ("07:30") | Backend: sends a `schedule` command at that minute | The device has no clock |
| Store the settings shown in the app (`GET /status`) | Backend | The device echoes only mode, day and the auto flags |

## Limits
- **TTN fair use, per device per day:** 30 s uplink airtime and **10 downlinks**. Downlinks are the tighter limit.
- **One uplink** (4 bytes) costs 51 ms at SF7 (about 580 a day) or 165 ms at SF9 (about 180 a day).
- **The firmware has a budget guard** that refills at 30 s a day. Uplinks that aren't urgent (automatic moves, buttons) wait when the budget is used up. Replies to commands, heartbeats and startup messages always go.
- **Never use confirmed uplinks.**
- **A normal day** uses about 30–60 uplinks: hourly heartbeats, 2 per app command, 2 per scheduled open/close, and keep-indoor-light moves. With keep-indoor-light moving on every 5-min tick all day, the worst case is about 200 (10 s at SF7, 33 s at SF9).

## Uplink (device → backend), FPort 1, always 4 bytes
| Byte | Content |
|---|---|
| 0 | Last command ID applied (0 = none yet) |
| 1 | Reason: 0 heartbeat · 1 startup · 2 button · 3 app command · 4 auto (light) · 5 schedule · 6 calibrated · 7 rejected |
| 2 | Flags: bit 0 automatic · bit 1 moving · bit 2 calibrated · bit 3 day (between opening and closing) · bit 4 open by sun · bit 5 close by sun · bit 6 keep indoor light · bit 7 calibrating (v4.1) |
| 3 | Position, % closed (0 = open, 100 = closed). 255 = unknown (not calibrated). While calibrating: 255 until the bottom is set, then 100. |

When it sends:
- when the motor stops after any move (`button`, `app_command`, `auto`, `schedule`)
- right after a command arrives (the reply). It carries `moving` if the command starts the motor.
- `startup`, `calibrated`
- during calibration: when it starts (reply, or `button` if a button started it) and when the bottom is set (`button`)
- `heartbeat` after 1 hour without any uplink

There is **no light data** in the protocol. This is deliberate: the user decided it, to save airtime. There is no FPort 2. If the team wants an outdoor-light history later, a 26-byte FPort 2 message once an hour would cost about 2 s a day at SF7 or 6.4 s at SF9. The firmware doesn't have it.

## Downlink (backend → device), FPort 1
The first byte is always the command ID (1–255, then back to 1; never 0).

| Type | Bytes | Meaning |
|---|---|---|
| 1 position | `[id] 01 [pos]` | Go to pos % closed. Switches to manual. Rejected until calibrated. |
| 2 mode | `[id] 02 [0 manual / 1 automatic]` | |
| 3 auto settings | `[id] 03 [cfg] [target] [thrOpen] [thrClose]` | Saves the auto settings. With bit 5 / bit 6 it also sets the mode / day (v4). All values 0–100; thrClose must be < thrOpen. |
| 4 schedule | `[id] 04 [0 open / 1 close]` | Sets day (open) or night (close). The backend sends this at the open/close time. In manual mode it only sets day/night and nothing moves. |
| 5 calibrate (v4.1) | `[id] 05 01` | Forgets the travel, switches to manual and waits for the buttons. The reply has flag bit 7 and position 255. Any other value of the third byte is rejected. |

**Type 3 `cfg` byte:**

| Bit | Value | Meaning |
|---|---|---|
| 0 | 0x01 | Open by sun (0 = open by time) |
| 1 | 0x02 | Close by sun (0 = close by time) |
| 2 | 0x04 | Keep indoor light at `target` during the day |
| 3 | 0x08 | Mode: 1 = automatic, 0 = manual. **Used only if bit 5 is set.** |
| 4 | 0x10 | Day/night: 1 = day, 0 = night. **Used only if bit 6 is set.** |
| 5 | 0x20 | Set the mode from bit 3 (v4) |
| 6 | 0x40 | Set day/night from bit 4 (v4) |
| 7 | 0x80 | Must be 0, or the device rejects the message |

Examples, with keep indoor light at 27 %, open above 80 %, close below 10 %:
- `16 03 04 1B 50 0A`: settings only (v3 style). Mode and day stay as they are.
- `16 03 7C 1B 50 0A`: settings + automatic + day, in one downlink.
- `16 03 24 1B 50 0A`: settings + manual. Day stays as it is.

Bits 5 and 6 are separate because when both sides are "by sun", the device decides day/night itself and the backend must not overwrite it.

## Backend rules (REST → downlink)
**Golden rule: send nothing that changes nothing.** The backend knows what the device has from:
- its **last uplink**: mode (bit 0), day (bit 3), and the open-by-sun / close-by-sun / keep-light flags (bits 4–6)
- the **last type 3 the device confirmed**: the target and thresholds aren't in the uplink, so remember them

**Day/night from the clock** (only the sides that work by time count):
- both by time: day between the open time and the close time
- only open by time: night before the open time, day after
- only close by time: night after the close time; before it, unknown, so don't send day
- both by sun: unknown. The device decides, so never send day/night.

| REST call or event | Downlink |
|---|---|
| `POST /position {position}` | Type 1, always |
| `PUT /mode {mode}` | Nothing if the device already has that mode (and, for automatic, the right day/night). Only the mode differs → type 2. Mode and day/night differ → **one type 3 with bits 5 + 6**. |
| `PUT /auto {...}` or `PUT /settings {...}` | Any auto setting changed → type 3, plus bits 5/6 if mode/day also differ. Only the times changed and day/night flips now → type 4. Otherwise nothing. |
| Open/close time reached (Danish local time), mode automatic, that side by time | Type 4, always. Not skipped, because the device's day flag may have changed since its last uplink. |
| `startup` uplink, or the first uplink after the backend starts | One type 3 with whatever differs (bits 5/6 as needed), or nothing. The backend's settings win; the device's reported mode is the truth for mode. |

Work out what to send when the command's turn comes, not when the REST call arrives. Then several quick changes in a row collapse into one downlink. The backend (`plan_sync` in `backend/device_manager.py`) does exactly this.

## The handshake
1. **One command at a time.** The app sends only the final value (when the handle is released).
2. **The device replies at once** with the same ID (`app_command`, `schedule` or `rejected`). It sends a second message when the motor stops.
3. **The backend marks it confirmed** when the ID matches. With no reply, it resends once with the same ID: the device repeats its reply and doesn't act twice. If that fails too, the command is "not delivered".
   - Wait 25 s for the reply (Class C), 75 s in Class A, 4 s over the USB test link.
4. **After any reply, the backend takes the mode from the device.** The device may have refused, or a button may have switched it to manual.
5. **Downlinks are unconfirmed** (LoRaWAN `confirmed: false`). The device's reply is the confirmation.
6. **Send via MQTT `down/replace`, never `push`.** Only one command is ever in flight, and a resend replaces the copy that is still queued. If a command gets no reply, clear the queue so it can't arrive later by surprise.

## Device rules
- **Every 5 min (tick):** average the light, then in automatic mode:
  - **By sun:** open when outdoor > thrOpen (after it has been dark), close when outdoor < thrClose.
  - **Day + keep indoor light:** aim for target ± 5 %. Inside that range nothing moves. Outside it, move by the error (10 % too dark → open by 10 % of the travel), at most 25 % per tick, then measure again at the next tick. At the end of the travel it stays put.
  - **Day without keep-light:** fully open. **Night:** fully closed.
  - With keep indoor light on, "open by time" doesn't open fully: the indoor target sets the daytime position. If the room is brighter than the target, the blind stays closed all day, and the evening close has nothing to do.
- **Right after `mode`, `auto` or `schedule`:** the same decision runs at once, even mid-move. The newest command wins: the motor changes course or stops.
- **Buttons** (Up D5 / Down D3 / Stop D4): Up = fully open, Down = fully closed, Stop = stop. Any press switches to manual: the person at the window wins.
- **Calibration:** see below. Travel and position are saved in EEPROM.
- **No clock** on the device. The backend stamps messages when they arrive.

## Calibration (v4.1)
There is no home switch, so a person shows the device the two ends with the three buttons.
1. **Start:** the app sends `[id] 05 01` (or `CAL` over USB, or any button while uncalibrated). The device forgets its travel, switches to manual and replies `calibrating`, position 255. The app shows step 1.
2. **Bottom:** Down runs the motor until Stop. Stop marks the bottom. The device sends a `button` uplink with `calibrating`, position 100. The app shows step 2. (Up also works in this step, to correct an overshoot.)
3. **Top:** Up runs the motor until Stop. Stop marks the top, if it is at least 200 steps (about 0.6 s) above the bottom. Otherwise the press is ignored. Down in this step returns towards the bottom but never below it.
4. **Done:** the device saves the travel and sends `calibrated` (position 0 = open, flag bit 7 cleared).

While calibrating, position commands are rejected and automatic decisions are paused. A reset during calibration leaves the device uncalibrated. A new `calibrate` command restarts at step 1.

The backend derives the app's step from the last uplink: a `calibrate` command still queued or in flight → "sending", flag bit 7 with position 255 → "bottom", flag bit 7 with position 100 → "top".

## Commands on demand: Class C
Our RN2483 has firmware **1.0.5** (checked 19 Nov), so it supports Class C.

**What the firmware does:**
- joins with OTAA. The DevEUI is the module's hardware EUI `0004A30B00E8F83E`. It joins at SF7 and falls back to SF9 on every other failed join.
- sets `mac set rx2 3 869525000` and `mac set class c`
- then receives `mac_rx 1 <hex>` at any time

**TTN also needs "Supports class C" ticked on the end device.** If it isn't ticked, the radio still accepts `mac set class c`, so the firmware doesn't poll. A command then waits in TTN until the next uplink, which can be up to an hour away.

Only if `mac set class c` itself fails does the firmware stay in Class A. It then sends a state uplink every 60 s so downlinks can get through. Those polls obey the airtime budget, so after about 100 min at SF7 (20 min at SF9) they slow to about every 2.5 min (8 min at SF9).

## Links: USB test and LoRaWAN live
The same bytes travel over either link.
- **The firmware's link** is chosen over USB with `LINK U` / `LINK L` and saved in EEPROM.
  - **USB:** `DL <hex>` / `UL <hex>` lines, and the radio is held in reset.
  - **LoRa:** the RN2483 carries FPort 1.
- **The backend** has it since 26 Nov: Settings → Connection in the React app (`PUT /link`). USB goes through an MQTT broker (the `mosquitto` service in docker-compose, reached at `wss://<DOMAIN>/mqtt`) and [`usb_bridge/mqtt_usb_bridge.py`](../usb_bridge/README.md) on the PC, which also sends the board `LINK U` / `LINK L`.

## Changes from the suggested v4 draft (22 Nov)
A teammate's draft proposed the combined type 3. We adopted it with these changes:
- **Separate "set mode" (bit 5) and "set day" (bit 6)** instead of one bit for both, so "by sun" can be left alone.
- **"Only the times changed → 0 downlinks" is not always right.** If the new time flips day/night right now, send a type 4. Otherwise the device waits until the next day's open or close.
- **No FPort 2 light data.** It isn't in v3 or the firmware (see Uplink).
- **Unconfirmed downlinks; 25 s (Class C) / 75 s (Class A) reply timeout** instead of confirmed downlinks with a 20 s timeout.
- **The backend must not send bits 3–6 to firmware older than 22 Nov.** That firmware rejects any cfg above 7.

## History
- **v1:** light reports every 15 min.
- **v1.1:** automatic decisions moved onto the 5-minute tick.
- **v2:** no light data; send only on change.
- **v3:** adopted the UI spec: manual/automatic, times, thresholds, % closed.
- **v4 (22 Nov):** combined type 3, send-nothing-that-changes-nothing, newest command wins mid-move.
- **v4.1 (26 Nov):** `calibrate` command (type 5) and the `calibrating` flag (bit 7), so calibration can be started and followed in the app.
