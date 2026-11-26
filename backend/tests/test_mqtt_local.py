"""Integration test exercising TTNMqttTransport against a local Mosquitto broker."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import paho.mqtt.client as mqtt
import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from main import create_app
from models import CommandKind, CommandStatus, DecodedStateUplink
from protocol import ProtocolCodec
from test_app import DEMO_MD5, FakeDatabase, make_test_settings
from transport import TTNMqttTransport

CONTAINER_NAME = "curtain-mqtt-test"
TLS_PORT = 18883
TCP_PORT = 11883
TTN_APPID = "test-app"
TTN_TENANT = "ttn"
TTN_USERNAME = f"{TTN_APPID}@{TTN_TENANT}"
TTN_API_KEY = "NNSXS.TESTKEYID.TESTSECRET"
TTN_DEVID = "curtain-01"
PROTOCOL_JSON_PATH = TESTS_DIR.parent / "protocol.json"


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"Command failed (rc={proc.returncode}): {' '.join(args)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def _wait_tcp(host: str, port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"Timed out waiting for {host}:{port}")


@pytest.mark.docker
@pytest.mark.asyncio
async def test_ttn_mqtt_transport_against_local_mosquitto(tmp_path: Path) -> None:
    _run(["docker", "rm", "-f", CONTAINER_NAME], check=False)

    bootstrap_sh = (
        "set -eu; "
        "command -v openssl >/dev/null 2>&1 || apk add --no-cache openssl >/dev/null; "
        "mkdir -p /tmp/certs; "
        "openssl req -x509 -nodes -newkey rsa:2048 -days 1 "
        "-subj '/CN=TestCA' -keyout /tmp/certs/ca.key -out /tmp/certs/ca.crt 2>/dev/null; "
        "openssl req -new -nodes -newkey rsa:2048 "
        "-subj '/CN=127.0.0.1' -keyout /tmp/certs/server.key -out /tmp/certs/server.csr 2>/dev/null; "
        "printf 'subjectAltName=IP:127.0.0.1,DNS:localhost\\nextendedKeyUsage=serverAuth\\n' > /tmp/certs/ext.cnf; "
        "openssl x509 -req -in /tmp/certs/server.csr -CA /tmp/certs/ca.crt -CAkey /tmp/certs/ca.key "
        "-CAcreateserial -out /tmp/certs/server.crt -days 1 -extfile /tmp/certs/ext.cnf 2>/dev/null; "
        f"mosquitto_passwd -b -c /tmp/passwd '{TTN_USERNAME}' '{TTN_API_KEY}'; "
        "chmod 0700 /tmp/passwd; chown mosquitto:mosquitto /tmp/passwd; "
        "chmod 0644 /tmp/certs/ca.crt /tmp/certs/server.crt; chmod 0640 /tmp/certs/server.key; "
        "chown -R mosquitto:mosquitto /tmp/certs; "
        "cat > /tmp/mosquitto.conf <<'CONF'\n"
        "allow_anonymous false\n"
        "password_file /tmp/passwd\n"
        "listener 8883\n"
        "cafile /tmp/certs/ca.crt\n"
        "certfile /tmp/certs/server.crt\n"
        "keyfile /tmp/certs/server.key\n"
        "listener 1883\n"
        "CONF\n"
        "exec /usr/sbin/mosquitto -c /tmp/mosquitto.conf"
    )

    try:
        _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                CONTAINER_NAME,
                "-p",
                f"127.0.0.1:{TLS_PORT}:8883",
                "-p",
                f"127.0.0.1:{TCP_PORT}:1883",
                "eclipse-mosquitto:2",
                "sh",
                "-c",
                bootstrap_sh,
            ]
        )

        _wait_tcp("127.0.0.1", TLS_PORT, timeout=30.0)
        _wait_tcp("127.0.0.1", TCP_PORT, timeout=10.0)

        ca_crt = tmp_path / "ca.crt"
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            cp = _run(
                ["docker", "cp", f"{CONTAINER_NAME}:/tmp/certs/ca.crt", str(ca_crt)],
                check=False,
            )
            if cp.returncode == 0 and ca_crt.exists():
                break
            time.sleep(0.2)
        assert ca_crt.exists(), "Failed to extract CA certificate from Mosquitto container"

        async def _noop_uplink(_d: str, _t: datetime, _p: int, _b: bytes) -> None:
            return None

        bad_transport = TTNMqttTransport(
            ttn_appid=TTN_APPID,
            mqtt_api_key="wrong-key",
            ttn_tenant=TTN_TENANT,
            mqtt_host="127.0.0.1",
            mqtt_port=TLS_PORT,
            mqtt_use_tls=True,
            mqtt_ca_certs=str(ca_crt),
        )
        await bad_transport.start(_noop_uplink)
        try:
            await asyncio.sleep(1.0)
            assert bad_transport.is_connected is False
        finally:
            await bad_transport.stop()

        tcp_transport = TTNMqttTransport(
            ttn_appid=TTN_APPID,
            mqtt_api_key=TTN_API_KEY,
            ttn_tenant=TTN_TENANT,
            mqtt_host="127.0.0.1",
            mqtt_port=TCP_PORT,
            mqtt_use_tls=False,
        )
        await tcp_transport.start(_noop_uplink)
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not tcp_transport.is_connected:
                await asyncio.sleep(0.05)
            assert tcp_transport.is_connected is True
        finally:
            await tcp_transport.stop()

        settings = make_test_settings(
            ttn_appid=TTN_APPID,
            ttn_devid=TTN_DEVID,
            mqtt_api_key=TTN_API_KEY,
            transport_mode="ttn_mqtt",
            ttn_tenant=TTN_TENANT,
            ttn_mqtt_host="127.0.0.1",
            ttn_mqtt_port=TLS_PORT,
            ttn_mqtt_use_tls=True,
            ttn_mqtt_ca_certs=str(ca_crt),
        )
        codec = ProtocolCodec.from_file(PROTOCOL_JSON_PATH)
        db = FakeDatabase()
        app = create_app(settings=settings, db=db, transport=None)

        loop = asyncio.get_running_loop()
        downlinks_q: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        sim_subscribed = threading.Event()
        down_topic = f"v3/{TTN_USERNAME}/devices/{TTN_DEVID}/down/replace"

        sim = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv311,
        )
        sim.username_pw_set(TTN_USERNAME, TTN_API_KEY)
        sim.tls_set(ca_certs=str(ca_crt), tls_version=ssl.PROTOCOL_TLS_CLIENT)

        def _on_sim_connect(client: mqtt.Client, _u: Any, _f: Any, rc: Any, _p: Any) -> None:
            if not getattr(rc, "is_failure", False):
                client.subscribe(down_topic, qos=1)

        def _on_sim_sub(_c: mqtt.Client, _u: Any, _mid: Any, _rc: Any, _p: Any) -> None:
            sim_subscribed.set()

        def _on_sim_msg(_c: mqtt.Client, _u: Any, msg: mqtt.MQTTMessage) -> None:
            payload = json.loads(msg.payload.decode("utf-8"))
            loop.call_soon_threadsafe(downlinks_q.put_nowait, (msg.topic, payload))

        sim.on_connect = _on_sim_connect
        sim.on_subscribe = _on_sim_sub
        sim.on_message = _on_sim_msg
        sim.connect("127.0.0.1", TLS_PORT, keepalive=60)
        sim.loop_start()

        try:
            assert sim_subscribed.wait(timeout=5.0), "Simulator MQTT client failed to subscribe"

            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        hr = await client.get("/health")
                        if hr.status_code == 200 and hr.json()["transportConnected"]:
                            break
                        await asyncio.sleep(0.05)
                    assert (await client.get("/health")).json() == {
                        "ok": True,
                        "transportConnected": True,
                        "protocolVersion": "v4",
                    }

                    login = await client.post(
                        "/login",
                        json={"username": "demo@example.com", "password": DEMO_MD5},
                    )
                    assert login.status_code == 200
                    headers = {"Authorization": f"Bearer {login.json()['token']}"}

                    up_topic = f"v3/{TTN_USERNAME}/devices/{TTN_DEVID}/up"

                    def _pub_uplink(f_port: int, payload: bytes) -> None:
                        sim.publish(
                            up_topic,
                            json.dumps(
                                {
                                    "end_device_ids": {"device_id": TTN_DEVID},
                                    "received_at": datetime.now(timezone.utc)
                                    .isoformat()
                                    .replace("+00:00", "Z"),
                                    "uplink_message": {
                                        "f_port": f_port,
                                        "frm_payload": base64.b64encode(payload).decode("ascii"),
                                    },
                                }
                            ),
                            qos=1,
                        ).wait_for_publish()

                    # MAC-only and malformed FPort 1 uplinks are ignored
                    sim.publish(
                        up_topic,
                        json.dumps({"end_device_ids": {"device_id": TTN_DEVID}, "uplink_message": {"f_port": 0}}),
                        qos=1,
                    ).wait_for_publish()
                    _pub_uplink(1, b"\x01\x02")

                    # Valid initial manual heartbeat calibrates device with 0 downlinks
                    _, hb_bytes = codec.encode_state_uplink(
                        DecodedStateUplink(
                            last_cmd_id=0,
                            reason="heartbeat",
                            mode="manual",
                            is_moving=False,
                            is_calibrated=True,
                            is_day=False,
                            open_by_sun=False,
                            close_by_sun=False,
                            keep_indoor_light=False,
                            raw_flags=4,
                            position=50,
                        )
                    )
                    _pub_uplink(1, hb_bytes)

                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        st_hb = (await client.get("/status", headers=headers)).json()
                        if st_hb["calibrated"] is True:
                            break
                        await asyncio.sleep(0.05)
                    assert st_hb["downlinksUsed24h"] == 0
                    assert downlinks_q.empty()

                    # Switching to automatic triggers 1 consolidated Type 3 sync over MQTTS
                    assert (
                        await client.put("/mode", json={"mode": "automatic"}, headers=headers)
                    ).status_code == 200
                    _, sync_msg = await asyncio.wait_for(downlinks_q.get(), timeout=5.0)
                    sync_entry = sync_msg["downlinks"][0]
                    assert sync_entry["confirmed"] is False
                    sync_dl = codec.decode_downlink(
                        sync_entry["f_port"], base64.b64decode(sync_entry["frm_payload"])
                    )
                    assert sync_dl.kind == CommandKind.SET_AUTO_SETTINGS
                    _, sync_reply = codec.encode_state_uplink(
                        DecodedStateUplink(
                            last_cmd_id=sync_dl.cmd_id,
                            reason="app_command",
                            mode="automatic",
                            is_moving=False,
                            is_calibrated=True,
                            is_day=False,
                            open_by_sun=False,
                            close_by_sun=True,
                            keep_indoor_light=False,
                            raw_flags=0x25,
                            position=50,
                        )
                    )
                    _pub_uplink(1, sync_reply)

                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        dbg = (await client.get("/debug", headers=headers)).json()
                        if dbg["device"]["settingsInSync"] is True:
                            break
                        await asyncio.sleep(0.05)

                    # POST /position 42 -> receives unconfirmed v4 downlink on down/replace
                    assert (await client.post("/position", json={"position": 42}, headers=headers)).status_code == 200
                    topic_rx, dl_msg = await asyncio.wait_for(downlinks_q.get(), timeout=5.0)
                    assert topic_rx == down_topic
                    assert len(dl_msg["downlinks"]) == 1
                    dl_entry = dl_msg["downlinks"][0]
                    assert dl_entry["f_port"] == 1
                    assert dl_entry["confirmed"] is False

                    decoded_dl = codec.decode_downlink(
                        dl_entry["f_port"], base64.b64decode(dl_entry["frm_payload"])
                    )
                    assert decoded_dl.kind == CommandKind.SET_POSITION
                    assert decoded_dl.args == {"position": 42}

                    for moving, pos in ((True, 50), (False, 42)):
                        _, reply_bytes = codec.encode_state_uplink(
                            DecodedStateUplink(
                                last_cmd_id=decoded_dl.cmd_id,
                                reason="app_command",
                                mode="manual",
                                is_moving=moving,
                                is_calibrated=True,
                                is_day=False,
                                open_by_sun=False,
                                close_by_sun=True,
                                keep_indoor_light=False,
                                raw_flags=0x24,
                                position=pos,
                            )
                        )
                        _pub_uplink(1, reply_bytes)

                    deadline = time.monotonic() + 5.0
                    st_pos: dict[str, Any] = {}
                    while time.monotonic() < deadline:
                        st_pos = (await client.get("/status", headers=headers)).json()
                        if st_pos["position"] == 42 and st_pos["moving"] is False:
                            break
                        await asyncio.sleep(0.05)
                    assert st_pos["position"] == 42
                    assert st_pos["moving"] is False
                    assert st_pos["downlinksUsed24h"] == 2
                    assert db.commands[-1]["status"] == CommandStatus.CONFIRMED.value

                    # clear_downlinks publishes {"downlinks": []} to down/replace
                    await app.state.manager.transport.clear_downlinks(TTN_DEVID)
                    clear_topic, clear_msg = await asyncio.wait_for(
                        downlinks_q.get(), timeout=5.0
                    )
                    assert clear_topic == down_topic
                    assert clear_msg == {"downlinks": []}
        finally:
            sim.loop_stop()
            sim.disconnect()
    finally:
        _run(["docker", "rm", "-f", CONTAINER_NAME], check=False)
