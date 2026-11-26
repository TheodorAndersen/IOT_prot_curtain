# USB bridge (USB/TTN switch)

The app has a switch in **Settings → Connection**: *USB cable* or *LoRaWAN (TTN)*. Both carry exactly the same LoRaWAN payload bytes (FPort 1, see [assets/PROTOCOL_FINAL.md](../assets/PROTOCOL_FINAL.md)). Only the transport differs:

```
USB link:  app ─/api─▶ backend ─MQTT─▶ mosquitto ◀─wss://<DOMAIN>/mqtt─ mqtt_usb_bridge.py (PC) ─COM port "DL/UL <hex>"─▶ Pro Mini
TTN link:  app ─/api─▶ backend ─MQTT─▶ TTN eu1 ─▶ gateway ─LoRa─▶ RN2483 ─▶ Pro Mini
```

`mqtt_usb_bridge.py` runs on the PC the board is plugged into. It stands in for TTN on the USB link, follows the switch (tells the board `LINK U` or `LINK L`, which the board saves in EEPROM), and reports `online`/`offline` so the app can show "Blind connected to the PC". On the TTN link it only powers the board and prints its log. It reopens the COM port by itself if the USB adapter drops out.

## Run it

```powershell
pip install -r usb_bridge/requirements.txt
python usb_bridge/mqtt_usb_bridge.py --server wss://<DOMAIN>/mqtt
```

It reads the repo's `.env` for `TTN_APPID`, `TTN_DEVID` (part of the MQTT topics, must match the backend's) and `USB_MQTT_PASSWORD`. Put `BRIDGE_SERVER=wss://<DOMAIN>/mqtt` in `.env` to skip `--server`.

| Backend runs… | `--server` |
|---|---|
| on the server (docker-compose + `deploy/gcp`) | `wss://<DOMAIN>/mqtt` |
| in Docker on this PC (`docker compose up`) | `ws://127.0.0.1:8080/mqtt` |
| by hand (`uvicorn`) with Mosquitto installed locally (`USB_MQTT_HOST=127.0.0.1` in `.env`) | none: the default is `mqtt://USB_MQTT_HOST:USB_MQTT_PORT`, anonymous |

Other options: `--port COM4` (default: the first CP210x adapter), `--user`/`--password`, `--verbose` (also prints the once-a-second `#L` lines).

Only one program can open the COM port: close the Arduino Serial Monitor first. Opening the port resets the Pro Mini; on the LoRa link that means a new TTN join (about 10 s).

## Server side

`docker-compose.yml` has a `mosquitto` service (not published on the host). The backend reaches it at `mosquitto:1883`, and nginx forwards `/mqtt` (WebSocket) to it, so the bridge needs nothing but HTTPS on port 443. Every client must log in with `USB_MQTT_USERNAME` (default `bridge`) / `USB_MQTT_PASSWORD` from `.env`. Without `USB_MQTT_PASSWORD` nobody can log in: the USB link is off and TTN works as before.

The active link is saved in the `backend_data` volume and starts as `LINK_DEFAULT` (default `ttn`).

## Good to know
- Switch to LoRaWAN while the bridge is connected: the board only learns the link over USB. After that it keeps it, also on battery.
- Run **one** backend per TTN device. Two backends on the same TTN app (e.g. the server and a PC) both receive every uplink and both send downlinks.
- Test hook: publish `BTN U`, `BTN D`, `BTN S` or `STATE` on `curtain/board-cmd` to press a virtual button on the board.
