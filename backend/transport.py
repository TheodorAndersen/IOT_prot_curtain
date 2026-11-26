"""LoRaWAN transport abstraction: TTN MQTT v3 and in-memory MockTransport."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from models import CommandKind, DecodedDownlink, DecodedStateUplink
from protocol import ProtocolCodec

logger = logging.getLogger(__name__)

UplinkCallback = Callable[[str, datetime, int, bytes], Awaitable[None]]


class LoRaTransport(ABC):
    """Minimal LoRaWAN transport contract."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if transport is currently connected and ready."""

    @abstractmethod
    async def start(self, on_uplink: UplinkCallback) -> None:
        """Start transport connection and register uplink callback."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down transport."""

    @abstractmethod
    async def send_downlink(
        self, dev_id: str, f_port: int, payload: bytes, confirmed: bool = False
    ) -> None:
        """Transmit a downlink payload for dev_id."""

    @abstractmethod
    async def clear_downlinks(self, dev_id: str) -> None:
        """Clear any queued downlinks on the network server for dev_id."""


class TTNMqttTransport(LoRaTransport):
    """The Things Stack (TTN v3) MQTTS transport using paho-mqtt."""

    def __init__(
        self,
        ttn_appid: str,
        mqtt_api_key: str,
        ttn_region: str = "eu1",
        *,
        ttn_tenant: str = "ttn",
        mqtt_host: str = "",
        mqtt_port: int = 8883,
        mqtt_use_tls: bool = True,
        mqtt_ca_certs: str = "",
        extra_topics: tuple[str, ...] = (),
        on_extra_message: Callable[[str, bytes], None] | None = None,
        on_connected: Callable[[], None] | None = None,
    ) -> None:
        self._extra_topics = extra_topics
        self._on_extra_message = on_extra_message
        self._on_connected = on_connected
        self._username = f"{ttn_appid}@{ttn_tenant}" if ttn_tenant else ttn_appid
        self._password = mqtt_api_key
        self._host = mqtt_host or f"{ttn_region}.cloud.thethings.network"
        self._port = mqtt_port
        self._use_tls = mqtt_use_tls
        self._connected = False
        self._on_uplink: UplinkCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[tuple[str, datetime, int, bytes]] | None = None
        self._consumer_task: asyncio.Task[None] | None = None

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv311,
        )
        self._client.username_pw_set(self._username, self._password)
        if self._use_tls:
            self._client.tls_set(
                ca_certs=mqtt_ca_certs or None,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
        self._client.reconnect_delay_set(min_delay=2, max_delay=60)
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self, on_uplink: UplinkCallback) -> None:
        self._on_uplink = on_uplink
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._consumer_task = asyncio.create_task(
            self._consume_uplinks(), name="ttn-uplink-consumer"
        )
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()
        logger.info(
            "TTN MQTT transport started (broker=%s:%s, username=%s, tls=%s)",
            self._host,
            self._port,
            self._username,
            self._use_tls,
        )

    async def stop(self) -> None:
        self._connected = False
        try:
            self._client.disconnect()
        except Exception:
            logger.exception("Error disconnecting TTN MQTT client")
        try:
            self._client.loop_stop()
        except Exception:
            logger.exception("Error stopping TTN MQTT network loop")
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None

    async def send_downlink(
        self, dev_id: str, f_port: int, payload: bytes, confirmed: bool = False
    ) -> None:
        if not self._connected:
            raise RuntimeError("TTN MQTT client is not connected")
        topic = f"v3/{self._username}/devices/{dev_id}/down/replace"
        body = json.dumps(
            {
                "downlinks": [
                    {
                        "f_port": f_port,
                        "frm_payload": base64.b64encode(payload).decode("ascii"),
                        "priority": "NORMAL",
                        "confirmed": confirmed,
                    }
                ]
            }
        )
        info = self._client.publish(topic, body, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed with rc={info.rc} on {topic}")
        logger.info(
            "Published confirmed=%s downlink to %s (f_port=%d, hex=%s)",
            confirmed,
            dev_id,
            f_port,
            payload.hex(),
        )

    async def clear_downlinks(self, dev_id: str) -> None:
        if not self._connected:
            logger.warning("Skipping clear_downlinks for %s: MQTT not connected", dev_id)
            return
        topic = f"v3/{self._username}/devices/{dev_id}/down/replace"
        body = json.dumps({"downlinks": []})
        info = self._client.publish(topic, body, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("Failed to clear downlinks on %s (rc=%s)", topic, info.rc)
        else:
            logger.info("Cleared TTN downlink queue for %s", dev_id)

    def _handle_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if getattr(reason_code, "is_failure", False):
            self._connected = False
            logger.warning("TTN MQTT connect failed: %s", reason_code)
            return
        self._connected = True
        topics = [
            (f"v3/{self._username}/devices/+/up", 1),
            (f"v3/{self._username}/devices/+/join", 1),
            (f"v3/{self._username}/devices/+/down/queued", 1),
            (f"v3/{self._username}/devices/+/down/sent", 1),
            (f"v3/{self._username}/devices/+/down/failed", 1),
            (f"v3/{self._username}/devices/+/down/ack", 1),
            (f"v3/{self._username}/devices/+/down/nack", 1),
        ]
        topics += [(t, 1) for t in self._extra_topics]
        client.subscribe(topics)
        logger.info(
            "Connected to MQTT broker %s and subscribed to %d topic patterns", self._host, len(topics)
        )
        if self._on_connected is not None:
            self._on_connected()

    def publish_raw(self, topic: str, payload: str, retain: bool = False) -> None:
        """Publish a non-LoRaWAN control message (used by DualTransport on the local broker)."""
        self._client.publish(topic, payload, qos=1, retain=retain)

    def _handle_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        self._connected = False
        logger.warning("Disconnected from TTN MQTT broker: %s", reason_code)

    def _handle_message(
        self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage
    ) -> None:
        try:
            topic = msg.topic
            prefix = f"v3/{self._username}/devices/"
            if not topic.startswith(prefix):
                if self._on_extra_message is not None and topic in self._extra_topics:
                    self._on_extra_message(topic, msg.payload)
                return
            rest = topic[len(prefix) :]
            if "/" not in rest:
                return
            topic_dev_id, event_path = rest.split("/", 1)

            if event_path == "up":
                self._process_uplink_message(topic_dev_id, msg.payload)
            elif event_path in ("join", "down/queued", "down/sent", "down/ack"):
                logger.info("TTN %s for %s: %s", event_path, topic_dev_id, msg.payload.decode("utf-8", errors="replace")[:300])
            elif event_path in ("down/nack", "down/failed"):
                logger.warning("TTN %s for %s: %s", event_path, topic_dev_id, msg.payload.decode("utf-8", errors="replace")[:500])
        except Exception:
            logger.exception("Unhandled error in TTN MQTT on_message callback")

    def _process_uplink_message(self, topic_dev_id: str, raw_payload: bytes) -> None:
        data = json.loads(raw_payload.decode("utf-8"))
        uplink = data.get("uplink_message")
        if not isinstance(uplink, dict):
            return

        f_port = uplink.get("f_port")
        frm_payload = uplink.get("frm_payload")
        if not isinstance(f_port, int) or isinstance(f_port, bool) or not frm_payload:
            # Safely ignore TTN MAC-only uplinks lacking f_port or frm_payload
            return

        dev_id = (
            data.get("end_device_ids", {}).get("device_id") or topic_dev_id
        )
        ts_raw = data.get("received_at") or uplink.get("received_at")
        received_at = _parse_ttn_timestamp(ts_raw)

        try:
            decoded_bytes = base64.b64decode(frm_payload, validate=True)
        except Exception:
            logger.warning("Invalid base64 frm_payload from %s on f_port=%d", dev_id, f_port)
            return

        if self._loop is not None and self._queue is not None:
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, (dev_id, received_at, f_port, decoded_bytes)
            )

    async def _consume_uplinks(self) -> None:
        assert self._queue is not None
        while True:
            dev_id, received_at, f_port, raw_bytes = await self._queue.get()
            try:
                if self._on_uplink is not None:
                    await self._on_uplink(dev_id, received_at, f_port, raw_bytes)
            except Exception:
                logger.exception("Error processing uplink for %s (f_port=%d)", dev_id, f_port)
            finally:
                self._queue.task_done()


def _parse_ttn_timestamp(ts_raw: Any) -> datetime:
    if isinstance(ts_raw, str) and ts_raw.strip():
        try:
            dt = datetime.fromisoformat(ts_raw.strip())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


class DualTransport(LoRaTransport):
    """USB test link and real TTN link side by side; exactly one is active.

    usb: TTNMqttTransport on a local broker where local_test/mqtt_usb_bridge.py stands in for TTN
    (it passes the same FPort 1 bytes to the board over COM4). ttn: the real TTN MQTT transport.
    Uplinks from the inactive link are ignored. The active link is saved in a small JSON file and
    published retained on LINK_TOPIC, so the bridge can tell the board "LINK U" or "LINK L".
    """

    LINK_TOPIC = "curtain/link"
    BRIDGE_TOPIC = "curtain/bridge"

    def __init__(
        self,
        make_usb: Callable[..., TTNMqttTransport],
        ttn: TTNMqttTransport,
        link_default: str = "usb",
        state_file: Path | None = None,
    ) -> None:
        self._state_file = state_file
        self._link = self._load_link(link_default)
        self._bridge_online = False
        self._on_uplink: UplinkCallback | None = None
        self.usb = make_usb(
            extra_topics=(self.BRIDGE_TOPIC,),
            on_extra_message=self._on_bridge_message,
            on_connected=self._announce_link,
        )
        self.ttn = ttn

    def _load_link(self, default: str) -> str:
        try:
            if self._state_file is not None and self._state_file.exists():
                link = json.loads(self._state_file.read_text(encoding="utf-8")).get("link")
                if link in ("usb", "ttn"):
                    return link
        except Exception:
            logger.warning("Could not read %s; using link=%s", self._state_file, default)
        return default

    @property
    def link(self) -> str:
        return self._link

    @property
    def bridge_online(self) -> bool:
        return self._bridge_online and self.usb.is_connected

    @property
    def active(self) -> TTNMqttTransport:
        return self.usb if self._link == "usb" else self.ttn

    @property
    def is_connected(self) -> bool:
        return self.active.is_connected

    def _on_bridge_message(self, topic: str, payload: bytes) -> None:
        self._bridge_online = payload.decode("utf-8", errors="replace").strip() == "online"
        logger.info("USB bridge is %s", "online" if self._bridge_online else "offline")

    def _announce_link(self) -> None:
        self.usb.publish_raw(self.LINK_TOPIC, self._link, retain=True)

    async def start(self, on_uplink: UplinkCallback) -> None:
        self._on_uplink = on_uplink
        await self.usb.start(self._from_usb)
        await self.ttn.start(self._from_ttn)
        logger.info("DualTransport started, active link: %s", self._link)

    async def stop(self) -> None:
        await self.usb.stop()
        await self.ttn.stop()

    async def _from_usb(self, dev_id: str, received_at: datetime, f_port: int, payload: bytes) -> None:
        if self._link == "usb" and self._on_uplink is not None:
            await self._on_uplink(dev_id, received_at, f_port, payload)
        else:
            logger.info("Ignoring USB uplink %s while the link is %s", payload.hex(), self._link)

    async def _from_ttn(self, dev_id: str, received_at: datetime, f_port: int, payload: bytes) -> None:
        if self._link == "ttn" and self._on_uplink is not None:
            await self._on_uplink(dev_id, received_at, f_port, payload)
        else:
            logger.info("Ignoring TTN uplink %s while the link is %s", payload.hex(), self._link)

    async def set_link(self, link: str) -> None:
        if link not in ("usb", "ttn"):
            raise ValueError(f"unknown link {link!r}")
        self._link = link
        if self._state_file is not None:
            try:
                self._state_file.write_text(json.dumps({"link": link}), encoding="utf-8")
            except OSError:
                logger.warning("Could not save the link to %s", self._state_file)
        if self.usb.is_connected:
            self._announce_link()
        logger.info("Active link is now %s", link)

    async def send_downlink(
        self, dev_id: str, f_port: int, payload: bytes, confirmed: bool = False
    ) -> None:
        await self.active.send_downlink(dev_id, f_port, payload, confirmed)

    async def clear_downlinks(self, dev_id: str) -> None:
        await self.active.clear_downlinks(dev_id)


class MockTransport(LoRaTransport):
    """In-memory local LoRaWAN device simulator driven by ProtocolCodec."""

    def __init__(
        self,
        codec: ProtocolCodec,
        dev_id: str,
        reply_delay_sec: float = 0.3,
        startup_delay_sec: float = 0.05,
        emit_initial_heartbeat: bool = True,
    ) -> None:
        self._codec = codec
        self._dev_id = dev_id
        self._reply_delay_sec = reply_delay_sec
        self._startup_delay_sec = startup_delay_sec
        self._emit_initial_heartbeat = emit_initial_heartbeat

        self._connected = False
        self._on_uplink: UplinkCallback | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._reply_tasks: set[asyncio.Task[None]] = set()

        # Simulated device state (starts calibrated at position=50, automatic)
        self.position: int | None = 50
        self.mode: str = "automatic"
        self.is_calibrated: bool = True
        self.is_moving: bool = False
        self.is_day: bool = False
        self.open_by_sun: bool = False
        self.close_by_sun: bool = True
        self.keep_indoor_light: bool = False
        self.is_calibrating: bool = False
        self.last_cmd_id: int = 0

        self.sent_downlinks: list[DecodedDownlink] = []
        self.cleared_count: int = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self, on_uplink: UplinkCallback) -> None:
        self._on_uplink = on_uplink
        self._connected = True
        if self._emit_initial_heartbeat:
            self._spawn(self._emit_initial_state(), is_reply=False)
        logger.info("MockTransport started for device %s", self._dev_id)

    async def stop(self) -> None:
        self._connected = False
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        self._reply_tasks.clear()

    async def send_downlink(
        self, dev_id: str, f_port: int, payload: bytes, confirmed: bool = False
    ) -> None:
        if not self._connected:
            raise RuntimeError("MockTransport is not connected")
        decoded = self._codec.decode_downlink(f_port, payload)
        self.sent_downlinks.append(decoded)
        # Newest command wins even mid-move: cancel any in-progress simulated move task
        for task in list(self._reply_tasks):
            task.cancel()
        self._reply_tasks.clear()
        self._spawn(self._simulate_reply(decoded), is_reply=True)

    async def clear_downlinks(self, dev_id: str) -> None:
        self.cleared_count += 1
        for task in list(self._reply_tasks):
            task.cancel()
        self._reply_tasks.clear()

    async def inject_uplink(
        self, f_port: int, payload: bytes, received_at: datetime | None = None
    ) -> None:
        """Inject a raw uplink directly into the registered callback."""
        if self._on_uplink is not None:
            await self._on_uplink(
                self._dev_id,
                received_at or datetime.now(timezone.utc),
                f_port,
                payload,
            )

    def _spawn(self, coro: Awaitable[None], is_reply: bool) -> None:
        task = asyncio.create_task(coro)  # type: ignore[arg-type]
        self._tasks.add(task)
        if is_reply:
            self._reply_tasks.add(task)

        def _cleanup(t: asyncio.Task[None]) -> None:
            self._tasks.discard(t)
            self._reply_tasks.discard(t)

        task.add_done_callback(_cleanup)

    async def _emit_initial_state(self) -> None:
        if self._startup_delay_sec > 0:
            await asyncio.sleep(self._startup_delay_sec)
        await self._emit_state("heartbeat")

    async def _simulate_reply(self, dl: DecodedDownlink) -> None:
        if self._reply_delay_sec > 0:
            await asyncio.sleep(self._reply_delay_sec)
        self.last_cmd_id = dl.cmd_id

        if dl.kind == CommandKind.SET_POSITION:
            if not self.is_calibrated:
                await self._emit_state("rejected")
                return
            target = int(dl.args["position"])
            self.mode = "manual"
            if self.position == target:
                self.is_moving = False
                await self._emit_state("app_command")
                return
            self.is_moving = True
            await self._emit_state("app_command")
            if self._reply_delay_sec > 0:
                await asyncio.sleep(self._reply_delay_sec)
            self.position = target
            self.is_moving = False
            await self._emit_state("app_command")
        elif dl.kind == CommandKind.SET_MODE:
            self.is_moving = False
            self.mode = str(dl.args["mode"])
            await self._emit_state("app_command")
        elif dl.kind == CommandKind.SET_AUTO_SETTINGS:
            self.is_moving = False
            self.open_by_sun = bool(dl.args["auto_open"])
            self.close_by_sun = bool(dl.args["auto_close"])
            self.keep_indoor_light = bool(dl.args["maintain_light"])
            if dl.args.get("set_mode") == 1:
                self.mode = "automatic" if dl.args.get("automatic") else "manual"
            if dl.args.get("set_day") == 1:
                self.is_day = bool(dl.args.get("is_day"))
            await self._emit_state("app_command")
        elif dl.kind == CommandKind.SET_SCHEDULE:
            self.is_moving = False
            self.is_day = dl.args["action"] == "open"
            await self._emit_state("schedule")
        elif dl.kind == CommandKind.CALIBRATE:
            self.is_moving = False
            self.mode = "manual"
            self.is_calibrated = False
            self.is_calibrating = True
            await self._emit_state("app_command")

    async def _emit_state(self, reason: str) -> None:
        state = DecodedStateUplink(
            last_cmd_id=self.last_cmd_id,
            reason=reason,
            mode="automatic" if self.mode == "automatic" else "manual",
            is_moving=self.is_moving,
            is_calibrated=self.is_calibrated,
            is_day=self.is_day,
            open_by_sun=self.open_by_sun,
            close_by_sun=self.close_by_sun,
            keep_indoor_light=self.keep_indoor_light,
            raw_flags=0,
            position=self.position if self.is_calibrated else None,
            is_calibrating=self.is_calibrating,
        )
        f_port, payload = self._codec.encode_state_uplink(state)
        await self.inject_uplink(f_port, payload)
