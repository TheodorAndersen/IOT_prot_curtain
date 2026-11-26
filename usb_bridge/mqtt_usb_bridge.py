"""Connect the board on the PC's USB port to the backend, for both links of the app's USB/TTN switch.

The backend (TRANSPORT_MODE=ttn_mqtt with USB_MQTT_HOST set) talks MQTT to its own broker for the USB
link and to real TTN for the LoRaWAN link. This script runs on the PC the board is plugged into and:
  * follows the switch: the backend publishes "usb" or "ttn" (retained) on curtain/link,
    and the script tells the board "LINK U" or "LINK L" (the board saves it in EEPROM);
  * USB link: passes the same FPort 1 bytes between the broker and the board, like TTN would:
        backend --down/replace--> broker --> here --"DL <hex>"--> Pro Mini
        backend <------up-------- broker <-- here <--"UL <hex>"-- Pro Mini
  * TTN link: the radio carries the bytes; this script only powers the board and shows its output;
  * publishes "online"/"offline" (retained, with a last will) on curtain/bridge so the app can show it;
  * reopens the serial port by itself if the USB adapter drops out.

Which broker (--server, or BRIDGE_SERVER in the repo's .env):
  hosted backend (docker-compose on the server):  --server wss://<DOMAIN>/mqtt   (needs USB_MQTT_PASSWORD)
  local Docker stack:                             --server ws://127.0.0.1:8080/mqtt
  backend run by hand + local Mosquitto:          (default) mqtt://USB_MQTT_HOST:USB_MQTT_PORT, i.e. 127.0.0.1:1883
Login: --user / --password, else USB_MQTT_USERNAME (default "bridge") / USB_MQTT_PASSWORD from .env.
TTN_APPID / TTN_DEVID / TTN_TENANT must match the backend's, since they are part of the MQTT topics.

Run:   python usb_bridge/mqtt_usb_bridge.py [--server URL] [--port COM4]
Stop:  Ctrl+C. Close the Arduino Serial Monitor first: only one program can open the serial port.
Note:  opening the port resets the Pro Mini (on the LoRa link that means a new TTN join).
"""

import argparse, base64, json, pathlib, re, sys, threading, time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import serial, serial.tools.list_ports
import paho.mqtt.client as mqtt

LINK_TOPIC, BRIDGE_TOPIC = 'curtain/link', 'curtain/bridge'
CMD_TOPIC = 'curtain/board-cmd'     # test hook: "BTN U|D|S" or "STATE" is passed to the board (e.g. mosquitto_pub)
SCHEMES = {'mqtt': (1883, False, 'tcp'), 'mqtts': (8883, True, 'tcp'),
           'ws': (80, False, 'websockets'), 'wss': (443, True, 'websockets')}


def read_env(path):
    env = {}
    if path.exists():
        for raw in path.read_text(encoding='utf-8').splitlines():
            if '=' in raw and not raw.lstrip().startswith('#'):
                k, v = raw.split('=', 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                    v = v[1:-1]
                env[k.strip()] = v
    return env


def parse_server(url):
    """'wss://host/mqtt' -> (host, port, tls, transport, path). A bare 'host[:port]' means mqtt://."""
    if '://' not in url:
        url = 'mqtt://' + url
    u = urlsplit(url)
    if u.scheme not in SCHEMES or not u.hostname:
        raise SystemExit(f'--server must look like wss://example.com/mqtt or mqtt://127.0.0.1:1883, got {url!r}')
    default_port, tls, transport = SCHEMES[u.scheme]
    return u.hostname, u.port or default_port, tls, transport, (u.path or '/mqtt')


def find_port(preferred):
    if preferred:
        return preferred
    for p in serial.tools.list_ports.comports():
        if 'CP210' in (p.description or ''):
            return p.device
    return None


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # a garbled serial byte must not crash the bridge
    here = pathlib.Path(__file__).resolve().parent
    env = read_env(here.parent / '.env')
    local_default = f"mqtt://{env.get('USB_MQTT_HOST') or '127.0.0.1'}:{env.get('USB_MQTT_PORT') or 1883}"
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', help='serial port (default: auto-detect CP210x)')
    ap.add_argument('--server', default=env.get('BRIDGE_SERVER') or local_default,
                    help='broker URL: wss://<DOMAIN>/mqtt, ws://host:port/mqtt, mqtt://host:port (default: %(default)s)')
    ap.add_argument('--user', default=env.get('USB_MQTT_USERNAME') or 'bridge')
    ap.add_argument('--password', default=env.get('USB_MQTT_PASSWORD', ''),
                    help='broker password (default: USB_MQTT_PASSWORD from .env; empty = anonymous)')
    ap.add_argument('--app', default=env.get('TTN_APPID', 'local-test'))
    ap.add_argument('--dev', default=env.get('TTN_DEVID', 'curtain-01'))
    ap.add_argument('--tenant', default=env.get('TTN_TENANT', 'ttn'))
    ap.add_argument('--verbose', action='store_true', help='also show the once-a-second #L light/position lines')
    args = ap.parse_args()
    host, mqtt_port, use_tls, transport, ws_path = parse_server(args.server)
    where = f"{'wss' if use_tls else 'ws'}://{host}:{mqtt_port}{ws_path}" if transport == 'websockets' \
        else f"{'mqtts' if use_tls else 'mqtt'}://{host}:{mqtt_port}"

    user = f'{args.app}@{args.tenant}' if args.tenant else args.app
    base = f'v3/{user}/devices/{args.dev}'
    state = {'link': None, 'ser': None, 'booted': False}
    lock = threading.Lock()

    def board(text):
        with lock:
            ser = state['ser']
            if ser is None:
                print(f'  !! board not connected, dropped: {text}', flush=True)
                return
            try:
                ser.write((text + '\n').encode('ascii'))
            except serial.SerialException:
                print(f'  !! write failed: {text}', flush=True)
                return
        print(f'  -> board  {text}', flush=True)

    def tell_link():
        if state['booted'] and state['link']:
            board('LINK L' if state['link'] == 'ttn' else 'LINK U')

    def on_connect(c, userdata, flags, reason, props):
        if reason.is_failure:
            hint = ' (check --user/--password, or USB_MQTT_PASSWORD in .env)' if 'authori' in str(reason).lower() \
                or 'password' in str(reason).lower() else ''
            print(f'!! broker refused us: {reason}{hint}', flush=True)
            return
        c.subscribe([(f'{base}/down/replace', 1), (f'{base}/down/push', 1), (LINK_TOPIC, 1), (CMD_TOPIC, 1)])
        c.publish(BRIDGE_TOPIC, 'online' if state['ser'] else 'offline', qos=1, retain=True)
        print(f'MQTT: connected to {where}, acting as TTN device {args.dev}', flush=True)

    def on_disconnect(c, userdata, flags, reason, props):
        print(f'!! MQTT: disconnected ({reason}). Reconnecting...', flush=True)

    def on_message(c, userdata, msg):
        if msg.topic == CMD_TOPIC:                          # test hook: virtual buttons / state request
            line = msg.payload.decode('ascii', 'replace').strip()
            if re.fullmatch(r'BTN [UDS]|STATE', line):
                board(line)
            return
        if msg.topic == LINK_TOPIC:
            link = msg.payload.decode('ascii', 'replace').strip()
            if link in ('usb', 'ttn') and link != state['link']:
                state['link'] = link
                print(f'Link is now {link.upper()} (set in the app)', flush=True)
                tell_link()
            return
        if state['link'] == 'ttn':
            print('  (ignored a USB downlink: the link is TTN)', flush=True)
            return
        try:
            downlinks = json.loads(msg.payload or b'{}').get('downlinks', [])
        except ValueError:
            print(f'  !! bad downlink message on {msg.topic}', flush=True)
            return
        if not downlinks:
            print('  backend cleared its downlink queue', flush=True)
        for d in downlinks:
            if d.get('frm_payload'):
                board('DL ' + base64.b64decode(d['frm_payload']).hex().upper())

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id='usb-bridge',
                         transport=transport)
    if transport == 'websockets':
        client.ws_set_options(path=ws_path)
    if use_tls:
        client.tls_set()                                    # system CA store (Let's Encrypt on the server)
    if args.password:
        client.username_pw_set(args.user, args.password)
    client.will_set(BRIDGE_TOPIC, 'offline', qos=1, retain=True)
    client.reconnect_delay_set(min_delay=2, max_delay=30)
    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    print(f'MQTT: connecting to {where} as {args.user if args.password else "anonymous"}...', flush=True)
    client.connect_async(host, mqtt_port, keepalive=30)
    client.loop_start()

    def publish_uplink(payload):
        client.publish(f'{base}/up', json.dumps({
            'end_device_ids': {'device_id': args.dev, 'application_ids': {'application_id': args.app}},
            'received_at': datetime.now(timezone.utc).isoformat(),
            'uplink_message': {'f_port': 1, 'frm_payload': base64.b64encode(payload).decode('ascii')},
        }), qos=1)

    try:
        while True:
            port = find_port(args.port)
            if port is None:
                time.sleep(2)
                continue
            try:
                ser = serial.Serial(port, 9600, timeout=0.2)    # opening it resets the Pro Mini (DTR)
            except serial.SerialException as e:
                print(f'!! cannot open {port}: {e}. Retrying...', flush=True)
                time.sleep(3)
                continue
            with lock:
                state['ser'], state['booted'] = ser, False
            client.publish(BRIDGE_TOPIC, 'online', qos=1, retain=True)
            print(f'Board on {port}. Waiting for it to boot...', flush=True)
            boot_at = time.time() + 2.5
            buf = b''
            try:
                while True:
                    if not state['booted'] and time.time() >= boot_at:
                        state['booted'] = True
                        if state['link'] is None:                 # keep the board's saved link until the backend says
                            print('Link: waiting for the backend (the board keeps its saved link)', flush=True)
                        tell_link()
                    buf += ser.read(256)
                    while b'\n' in buf:
                        raw, buf = buf.split(b'\n', 1)
                        s = raw.decode('utf-8', errors='replace').strip()
                        if not s or (s.startswith('#L') and not args.verbose):
                            continue
                        if s.startswith('UL ') and state['link'] != 'ttn':
                            try:
                                publish_uplink(bytes.fromhex(s[3:].strip()))
                                print(f'  <- board  {s}   (published as uplink)', flush=True)
                            except ValueError:
                                print(f'  board    {s}   (bad hex, not published)', flush=True)
                        else:
                            print(f'  board    {s}', flush=True)
            except serial.SerialException as e:
                print(f'!! lost the board ({e}). Waiting for it to come back...', flush=True)
                with lock:
                    state['ser'] = None
                client.publish(BRIDGE_TOPIC, 'offline', qos=1, retain=True)
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            client.publish(BRIDGE_TOPIC, 'offline', qos=1, retain=True).wait_for_publish(2)
        except (RuntimeError, ValueError):
            pass                                            # not connected: the last will says "offline"
        client.loop_stop()
        if state['ser'] is not None:
            state['ser'].close()


if __name__ == '__main__':
    sys.exit(main())
