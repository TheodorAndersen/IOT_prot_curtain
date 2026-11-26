"""Integration and behavioral tests for REST API, DeviceManager, plan_sync, scheduler, and /debug."""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from config import Settings, load_settings
from db import (
    DatabaseConflictError,
    DispatchAction,
    _is_mode_changing_command,
    _to_utc,
    compute_day_hint,
    compute_is_day,
    compute_sync_commands,
    is_settings_in_sync,
)
from device_manager import DeviceManager
from main import create_app
from models import CommandKind, CommandStatus, DecodedStateUplink
from protocol import ProtocolCodec
from transport import MockTransport

PROTOCOL_JSON_PATH = Path(__file__).resolve().parent.parent / "protocol.json"
PROTOCOL_V3_JSON_PATH = Path(__file__).resolve().parent.parent / "protocol.v3.json"
DEMO_MD5 = hashlib.md5(b"curtain").hexdigest()


def make_test_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "mqtt_api_key": "",
        "ttn_appid": "test-app",
        "ttn_devid": "curtain-01",
        "ttn_region": "eu1",
        "database_url": "postgresql://curtain:curtain@localhost:5432/curtain",
        "app_host": "127.0.0.1",
        "app_port": 8000,
        "transport_mode": "mock",
        "protocol_config_path": PROTOCOL_JSON_PATH,
        "dispatch_poll_sec": 0.05,
        "scheduler_poll_sec": 60.0,
        "schedule_timezone": "Europe/Copenhagen",
        "token_ttl_hours": 168,
        "demo_user_email": "demo@example.com",
        "demo_user_password_md5": DEMO_MD5,
        "device_name": "Roller curtain",
        "device_dev_eui": "0004A30B00E8F83E",
        "protocol_version": "v4",
    }
    defaults.update(overrides)
    return Settings(**defaults)


class FakeDatabase:
    """In-memory async datastore matching Database's contract and invariants."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, dict[str, Any]] = {}
        self.devices: dict[uuid.UUID, dict[str, Any]] = {}
        self.settings: dict[uuid.UUID, dict[str, Any]] = {}
        self.commands: list[dict[str, Any]] = []
        self.transmissions: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.telemetry: dict[tuple[uuid.UUID, datetime], int] = {}
        self._next_cmd_pk = 1
        self._next_event_pk = 1
        self._next_tx_pk = 1

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def init_schema(self) -> None:
        return None

    async def seed_defaults(self, settings: Settings, codec: ProtocolCodec) -> None:
        pw_hash = hashlib.sha256(
            settings.demo_user_password_md5.lower().encode("utf-8")
        ).hexdigest()
        existing_user = self.users.get(settings.demo_user_email)
        if existing_user is None:
            user_id = uuid.uuid4()
            self.users[settings.demo_user_email] = {
                "id": user_id,
                "email": settings.demo_user_email,
                "password_hash": pw_hash,
            }
        else:
            existing_user["password_hash"] = pw_hash
            user_id = existing_user["id"]

        existing_dev = next(
            (d for d in self.devices.values() if d["user_id"] == user_id), None
        )
        if existing_dev is None:
            dev_id = uuid.uuid4()
            self.devices[dev_id] = {
                "id": dev_id,
                "user_id": user_id,
                "ttn_device_id": settings.ttn_devid,
                "dev_eui": settings.device_dev_eui,
                "name": settings.device_name,
                "position": None,
                "target_position": None,
                "mode": "automatic",
                "desired_mode": "automatic",
                "is_moving": False,
                "is_calibrated": False,
                "is_day": False,
                "device_flags": 0,
                "last_reason": None,
                "last_cmd_id_ack": 0,
                "next_cmd_id": codec.spec.cmd_id.min,
                "last_confirmed_auto_settings": None,
                "sync_pending": False,
                "sync_time_changed": False,
                "last_seen_at": None,
            }
            self.settings[dev_id] = {
                "device_id": dev_id,
                "auto_open": 0,
                "auto_open_time": "07:30",
                "auto_close": 1,
                "auto_close_time": "21:00",
                "maintain_light": 0,
                "target_light_level": 60,
                "outside_threshold_open": 80,
                "outside_threshold_close": 10,
                "last_open_sched_date": None,
                "last_close_sched_date": None,
            }
        else:
            existing_dev["ttn_device_id"] = settings.ttn_devid
            existing_dev["dev_eui"] = settings.device_dev_eui
            existing_dev["name"] = settings.device_name

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        return self.users.get(email)

    async def create_auth_token(
        self, user_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> None:
        now = datetime.now(timezone.utc)
        expired = [k for k, v in self.tokens.items() if v["expires_at"] < now]
        for k in expired:
            del self.tokens[k]
        self.tokens[token_hash] = {"user_id": user_id, "expires_at": expires_at}

    async def get_user_id_by_token_hash(self, token_hash: str) -> uuid.UUID | None:
        row = self.tokens.get(token_hash)
        if row is None or row["expires_at"] <= datetime.now(timezone.utc):
            return None
        return row["user_id"]

    def _dev_by_user(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        return next((d for d in self.devices.values() if d["user_id"] == user_id), None)

    def _has_pending_set_mode(self, dev: dict[str, Any]) -> bool:
        if dev["sync_pending"] and dev["desired_mode"] != dev["mode"]:
            return True
        return any(
            c["device_id"] == dev["id"]
            and _is_mode_changing_command(c["command_kind"], c["command_args"])
            and c["status"] in (CommandStatus.QUEUED.value, CommandStatus.IN_FLIGHT.value)
            for c in self.commands
        )

    def _insert_command(
        self, device_id: uuid.UUID, kind: CommandKind, args: dict[str, Any]
    ) -> None:
        self.commands.append(
            {
                "id": self._next_cmd_pk,
                "device_id": device_id,
                "command_kind": kind.value,
                "command_args": dict(args),
                "cmd_id": None,
                "wire_fport": None,
                "wire_payload_hex": None,
                "status": CommandStatus.QUEUED.value,
                "attempts": 0,
                "sent_at": None,
                "resolved_at": None,
                "created_at": datetime.now(timezone.utc),
            }
        )
        self._next_cmd_pk += 1

    async def get_device_and_settings_by_user(
        self, user_id: uuid.UUID, now_utc: datetime | None = None
    ) -> dict[str, Any] | None:
        dev = self._dev_by_user(user_id)
        if dev is None:
            return None
        window_start = _to_utc(now_utc) - timedelta(hours=24)
        recent_sent = [
            t["sent_at"]
            for t in self.transmissions
            if t["device_id"] == dev["id"] and t["sent_at"] > window_start
        ]
        st = self.settings[dev["id"]]
        return {
            "device_id": dev["id"],
            "ttn_device_id": dev["ttn_device_id"],
            "position": dev["position"],
            "target_position": dev["target_position"],
            "mode": dev["mode"],
            "desired_mode": dev["desired_mode"],
            "is_moving": dev["is_moving"],
            "is_calibrated": dev["is_calibrated"],
            "is_day": dev["is_day"],
            "last_seen_at": dev["last_seen_at"],
            "sync_pending": dev["sync_pending"],
            "auto_open": st["auto_open"],
            "auto_open_time": st["auto_open_time"],
            "auto_close": st["auto_close"],
            "auto_close_time": st["auto_close_time"],
            "maintain_light": st["maintain_light"],
            "target_light_level": st["target_light_level"],
            "outside_threshold_open": st["outside_threshold_open"],
            "outside_threshold_close": st["outside_threshold_close"],
            "has_pending_set_mode": self._has_pending_set_mode(dev),
            "downlinks_used_24h": len(recent_sent),
            "oldest_downlink_sent_at": min(recent_sent) if recent_sent else None,
        }

    async def get_debug_snapshot(
        self, user_id: uuid.UUID, limit: int, now_utc: datetime | None = None
    ) -> dict[str, Any] | None:
        dev = self._dev_by_user(user_id)
        if dev is None:
            return None
        window_start = _to_utc(now_utc) - timedelta(hours=24)
        recent_sent = [
            t["sent_at"]
            for t in self.transmissions
            if t["device_id"] == dev["id"] and t["sent_at"] > window_start
        ]
        st = self.settings[dev["id"]]
        combined_dev = {
            **dev,
            **st,
            "device_id": dev["id"],
            "has_pending_set_mode": self._has_pending_set_mode(dev),
            "downlinks_used_24h": len(recent_sent),
            "oldest_downlink_sent_at": min(recent_sent) if recent_sent else None,
        }
        dev_cmds = [c for c in self.commands if c["device_id"] == dev["id"]]
        in_flight = next(
            (c for c in dev_cmds if c["status"] == CommandStatus.IN_FLIGHT.value), None
        )
        queued = sorted(
            [c for c in dev_cmds if c["status"] == CommandStatus.QUEUED.value],
            key=lambda c: c["id"],
        )
        sent_cmds = [c for c in dev_cmds if c["sent_at"] is not None]
        sent_cmds.sort(key=lambda c: (c["sent_at"], c["id"]), reverse=True)
        recent_commands = sorted(dev_cmds, key=lambda c: c["id"], reverse=True)[:limit]
        dev_events = [e for e in self.events if e["device_id"] == dev["id"]]
        dev_events.sort(key=lambda e: (e["received_at"], e["id"]), reverse=True)
        return {
            "device": combined_dev,
            "in_flight": in_flight,
            "queued": queued,
            "last_sent": sent_cmds[0] if sent_cmds else None,
            "recent_commands": recent_commands,
            "recent_uplinks": dev_events[:limit],
        }

    async def list_devices(self) -> list[dict[str, Any]]:
        return [
            {"id": d["id"], "ttn_device_id": d["ttn_device_id"]}
            for d in self.devices.values()
        ]

    async def get_telemetry(
        self, user_id: uuid.UUID, hours: int
    ) -> tuple[str, list[dict[str, Any]]] | None:
        dev = self._dev_by_user(user_id)
        if dev is None:
            return None
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        items = [
            {"recorded_at": ts, "light_level": lvl}
            for (did, ts), lvl in self.telemetry.items()
            if did == dev["id"] and ts >= since
        ]
        items.sort(key=lambda r: r["recorded_at"])
        return dev["ttn_device_id"], items

    async def enqueue_position(
        self, user_id: uuid.UUID, position: int, codec: ProtocolCodec
    ) -> None:
        dev = self._dev_by_user(user_id)
        assert dev is not None
        if not dev["is_calibrated"]:
            raise DatabaseConflictError("curtain is not calibrated")
        st = self.settings[dev["id"]]
        keep_sync = bool(dev["sync_pending"]) and not is_settings_in_sync(dev, st, codec)
        dev["desired_mode"] = "manual"
        dev["target_position"] = position
        dev["sync_pending"] = keep_sync
        dev["sync_time_changed"] = False

        now = datetime.now(timezone.utc)
        for c in self.commands:
            if (
                c["device_id"] == dev["id"]
                and c["status"] == CommandStatus.QUEUED.value
                and (
                    c["command_kind"] in (CommandKind.SET_POSITION.value, CommandKind.SET_MODE.value, CommandKind.SET_SCHEDULE.value)
                    or (
                        c["command_kind"] == CommandKind.SET_AUTO_SETTINGS.value
                        and int(c["command_args"].get("set_mode") or 0) == 1
                    )
                )
            ):
                c["status"] = CommandStatus.SUPERSEDED.value
                c["resolved_at"] = now
        self._insert_command(dev["id"], CommandKind.SET_POSITION, {"position": position})

    async def enqueue_mode(self, user_id: uuid.UUID, mode: str) -> bool:
        dev = self._dev_by_user(user_id)
        assert dev is not None
        if (
            mode == "manual"
            and str(dev["mode"]) == "manual"
            and str(dev["desired_mode"]) == "manual"
            and not bool(dev["sync_pending"])
            and not self._has_pending_set_mode(dev)
        ):
            return False
        dev["desired_mode"] = mode
        if mode == "automatic":
            dev["target_position"] = None
        dev["sync_pending"] = True
        now = datetime.now(timezone.utc)
        for c in self.commands:
            if (
                c["device_id"] == dev["id"]
                and c["status"] == CommandStatus.QUEUED.value
                and (
                    c["command_kind"] in (CommandKind.SET_MODE.value, CommandKind.SET_SCHEDULE.value)
                    or (
                        c["command_kind"] == CommandKind.SET_AUTO_SETTINGS.value
                        and int(c["command_args"].get("set_mode") or 0) == 1
                    )
                )
            ):
                c["status"] = CommandStatus.SUPERSEDED.value
                c["resolved_at"] = now
        return True

    async def enqueue_sync(self, user_id: uuid.UUID) -> None:
        dev = self._dev_by_user(user_id)
        if dev is None:
            raise DatabaseConflictError("device not found")
        effective_mode = (
            dev["desired_mode"] if self._has_pending_set_mode(dev) else dev["mode"]
        )
        now = datetime.now(timezone.utc)
        for c in self.commands:
            if (
                c["device_id"] == dev["id"]
                and c["command_kind"] == CommandKind.SET_MODE.value
                and c["status"] == CommandStatus.QUEUED.value
            ):
                c["status"] = CommandStatus.SUPERSEDED.value
                c["resolved_at"] = now
        self._insert_command(dev["id"], CommandKind.SET_MODE, {"mode": effective_mode})

    async def enqueue_startup_sync_for_unseen_devices(self) -> bool:
        enqueued_any = False
        for dev in self.devices.values():
            if dev["last_seen_at"] is not None:
                continue
            if self._has_pending_set_mode(dev):
                continue
            self._insert_command(
                dev["id"], CommandKind.SET_MODE, {"mode": dev["desired_mode"]}
            )
            enqueued_any = True
        return enqueued_any

    async def update_auto_settings(
        self,
        user_id: uuid.UUID,
        auto_open: int,
        auto_open_time: str,
        auto_close: int,
        auto_close_time: str,
        maintain_light: int,
        target_light_level: int,
    ) -> bool:
        dev = self._dev_by_user(user_id)
        assert dev is not None
        st = self.settings[dev["id"]]
        open_time_changed = auto_open_time != st["auto_open_time"]
        close_time_changed = auto_close_time != st["auto_close_time"]
        if open_time_changed:
            st["last_open_sched_date"] = None
        if close_time_changed:
            st["last_close_sched_date"] = None

        st["auto_open"] = auto_open
        st["auto_open_time"] = auto_open_time
        st["auto_close"] = auto_close
        st["auto_close_time"] = auto_close_time
        st["maintain_light"] = maintain_light
        st["target_light_level"] = target_light_level
        dev["sync_pending"] = True
        dev["sync_time_changed"] = bool(dev["sync_time_changed"]) or open_time_changed or close_time_changed

        now = datetime.now(timezone.utc)
        for c in self.commands:
            if (
                c["device_id"] == dev["id"]
                and c["command_kind"] == CommandKind.SET_AUTO_SETTINGS.value
                and c["status"] == CommandStatus.QUEUED.value
            ):
                c["status"] = CommandStatus.SUPERSEDED.value
                c["resolved_at"] = now
        return True

    async def update_thresholds(
        self,
        user_id: uuid.UUID,
        outside_threshold_open: int,
        outside_threshold_close: int,
    ) -> bool:
        dev = self._dev_by_user(user_id)
        assert dev is not None
        st = self.settings[dev["id"]]
        st["outside_threshold_open"] = outside_threshold_open
        st["outside_threshold_close"] = outside_threshold_close
        dev["sync_pending"] = True

        now = datetime.now(timezone.utc)
        for c in self.commands:
            if (
                c["device_id"] == dev["id"]
                and c["command_kind"] == CommandKind.SET_AUTO_SETTINGS.value
                and c["status"] == CommandStatus.QUEUED.value
            ):
                c["status"] = CommandStatus.SUPERSEDED.value
                c["resolved_at"] = now
        return True

    async def apply_state_uplink(
        self,
        ttn_device_id: str,
        received_at: datetime,
        decoded: DecodedStateUplink,
        raw_fport: int,
        raw_payload_hex: str,
        codec: ProtocolCodec,
        first_uplink_since_boot: bool = False,
    ) -> tuple[bool, bool]:
        dev = next(
            (d for d in self.devices.values() if d["ttn_device_id"] == ttn_device_id),
            None,
        )
        if dev is None:
            return False, False

        device_id = dev["id"]
        self.events.append(
            {
                "id": self._next_event_pk,
                "device_id": device_id,
                "received_at": received_at,
                "last_cmd_id": decoded.last_cmd_id,
                "reason": decoded.reason,
                "mode": decoded.mode,
                "flags": decoded.raw_flags,
                "position": decoded.position,
                "raw_fport": raw_fport,
                "raw_payload_hex": raw_payload_hex,
            }
        )
        self._next_event_pk += 1

        dev["last_seen_at"] = received_at
        dev["last_reason"] = decoded.reason
        dev["last_cmd_id_ack"] = decoded.last_cmd_id
        dev["device_flags"] = decoded.raw_flags
        dev["mode"] = decoded.mode
        dev["is_moving"] = decoded.is_moving
        dev["is_calibrated"] = decoded.is_calibrated
        dev["is_day"] = decoded.is_day
        dev["position"] = decoded.position
        if not decoded.is_moving or decoded.mode == "automatic":
            dev["target_position"] = None

        should_wake = False

        if decoded.reason == "startup":
            for c in self.commands:
                if c["device_id"] == device_id and c["status"] == CommandStatus.IN_FLIGHT.value:
                    c["status"] = CommandStatus.SUPERSEDED.value
                    c["resolved_at"] = received_at
                elif (
                    c["device_id"] == device_id
                    and c["status"] == CommandStatus.QUEUED.value
                    and c["command_kind"]
                    in (
                        CommandKind.SET_AUTO_SETTINGS.value,
                        CommandKind.SET_SCHEDULE.value,
                        CommandKind.SET_MODE.value,
                    )
                ):
                    c["status"] = CommandStatus.SUPERSEDED.value
                    c["resolved_at"] = received_at
            dev["desired_mode"] = decoded.mode
            dev["sync_pending"] = True
            dev["sync_time_changed"] = False
            should_wake = True
        else:
            inflight = next(
                (
                    c
                    for c in self.commands
                    if c["device_id"] == device_id and c["status"] == CommandStatus.IN_FLIGHT.value
                ),
                None,
            )
            if inflight is not None and inflight["cmd_id"] == decoded.last_cmd_id:
                new_status = (
                    CommandStatus.REJECTED.value
                    if decoded.reason == "rejected"
                    else CommandStatus.CONFIRMED.value
                )
                inflight["status"] = new_status
                inflight["resolved_at"] = received_at
                if (
                    new_status == CommandStatus.CONFIRMED.value
                    and inflight["command_kind"] == CommandKind.SET_AUTO_SETTINGS.value
                ):
                    args = inflight["command_args"]
                    dev["last_confirmed_auto_settings"] = {
                        "auto_open": int(args["auto_open"]),
                        "auto_close": int(args["auto_close"]),
                        "maintain_light": int(args["maintain_light"]),
                        "target_light_level": int(args["target_light_level"]),
                        "outside_threshold_open": int(args["outside_threshold_open"]),
                        "outside_threshold_close": int(args["outside_threshold_close"]),
                    }
                elif (
                    new_status == CommandStatus.REJECTED.value
                    and inflight["command_kind"] == CommandKind.SET_POSITION.value
                ):
                    dev["target_position"] = None
                should_wake = True

            was_sync_pending = bool(dev["sync_pending"])
            if first_uplink_since_boot:
                dev["sync_pending"] = True
                should_wake = True

            has_pending_mode = any(
                c["device_id"] == device_id
                and _is_mode_changing_command(c["command_kind"], c["command_args"])
                and c["status"] in (CommandStatus.QUEUED.value, CommandStatus.IN_FLIGHT.value)
                for c in self.commands
            )
            if not has_pending_mode and not was_sync_pending:
                dev["desired_mode"] = decoded.mode

        has_active_cmds = any(
            c["device_id"] == device_id
            and c["status"] in (CommandStatus.QUEUED.value, CommandStatus.IN_FLIGHT.value)
            for c in self.commands
        )
        if decoded.last_cmd_id > 0 and not has_active_cmds:
            dev["next_cmd_id"] = codec.next_cmd_id(decoded.last_cmd_id)

        return True, should_wake

    async def insert_light_telemetry(
        self,
        ttn_device_id: str,
        received_at: datetime,
        samples: list[tuple[datetime, int]],
    ) -> bool:
        dev = next(
            (d for d in self.devices.values() if d["ttn_device_id"] == ttn_device_id),
            None,
        )
        if dev is None:
            return False
        for ts, lvl in samples:
            self.telemetry.setdefault((dev["id"], ts), lvl)
        dev["last_seen_at"] = received_at
        return True

    async def prepare_dispatch_action(
        self,
        device_id: uuid.UUID,
        now_utc: datetime,
        now_local: datetime,
        codec: ProtocolCodec,
    ) -> DispatchAction | None:
        dev = self.devices.get(device_id)
        if dev is None:
            return None
        need_clear = False
        inflight_list = [
            c
            for c in self.commands
            if c["device_id"] == device_id and c["status"] == CommandStatus.IN_FLIGHT.value
        ]
        assert len(inflight_list) <= 1, "single IN_FLIGHT invariant violated"
        inflight = inflight_list[0] if inflight_list else None

        if inflight is not None:
            elapsed = (now_utc - inflight["sent_at"]).total_seconds()
            if elapsed < codec.spec.handshake.timeout_sec:
                return None
            if inflight["attempts"] < codec.spec.handshake.max_attempts:
                inflight["attempts"] += 1
                inflight["sent_at"] = now_utc
                return DispatchAction(
                    ttn_device_id=dev["ttn_device_id"],
                    clear_downlinks=False,
                    f_port=inflight["wire_fport"],
                    payload=bytes.fromhex(inflight["wire_payload_hex"]),
                    confirmed=codec.spec.confirmed_downlinks,
                    command_id=int(inflight["id"]),
                )
            inflight["status"] = CommandStatus.NOT_DELIVERED.value
            inflight["resolved_at"] = now_utc
            if inflight["command_kind"] == CommandKind.SET_POSITION.value:
                dev["target_position"] = None
            if _is_mode_changing_command(inflight["command_kind"], inflight["command_args"]):
                has_newer = any(
                    c["device_id"] == device_id
                    and _is_mode_changing_command(c["command_kind"], c["command_args"])
                    and c["status"] == CommandStatus.QUEUED.value
                    for c in self.commands
                )
                if not has_newer and not dev["sync_pending"]:
                    dev["desired_mode"] = dev["mode"]
            need_clear = True

        queued = [
            c
            for c in self.commands
            if c["device_id"] == device_id and c["status"] == CommandStatus.QUEUED.value
        ]
        if not queued and dev["sync_pending"]:
            st = self.settings[device_id]
            hint = compute_day_hint(
                now_local,
                st["auto_open"],
                st["auto_open_time"],
                st["auto_close"],
                st["auto_close_time"],
            )
            planned = compute_sync_commands(
                dev,
                st,
                hint,
                codec,
                time_changed=bool(dev["sync_time_changed"]),
            )
            dev["sync_pending"] = False
            dev["sync_time_changed"] = False
            for p_kind, p_args in planned:
                self._insert_command(device_id, p_kind, p_args)
            queued = [
                c
                for c in self.commands
                if c["device_id"] == device_id and c["status"] == CommandStatus.QUEUED.value
            ]

        if not queued:
            if need_clear:
                return DispatchAction(
                    ttn_device_id=dev["ttn_device_id"], clear_downlinks=True
                )
            return None

        queued.sort(key=lambda c: c["id"])
        row = queued[0]
        cmd_id = dev["next_cmd_id"]
        dl = codec.encode_command(cmd_id, row["command_kind"], row["command_args"])
        dev["next_cmd_id"] = codec.next_cmd_id(cmd_id)
        row["cmd_id"] = cmd_id
        row["wire_fport"] = dl.f_port
        row["wire_payload_hex"] = dl.payload.hex()
        row["status"] = CommandStatus.IN_FLIGHT.value
        row["attempts"] = 1
        row["sent_at"] = now_utc

        return DispatchAction(
            ttn_device_id=dev["ttn_device_id"],
            clear_downlinks=need_clear,
            f_port=dl.f_port,
            payload=dl.payload,
            confirmed=dl.confirmed,
            command_id=int(row["id"]),
        )

    async def record_downlink_transmission(
        self,
        device_id: uuid.UUID,
        command_id: int | None,
        f_port: int,
        confirmed: bool,
        sent_at: datetime,
    ) -> int:
        sent_at_utc = _to_utc(sent_at)
        self.transmissions.append(
            {
                "id": self._next_tx_pk,
                "device_id": device_id,
                "command_id": command_id,
                "f_port": f_port,
                "confirmed": confirmed,
                "sent_at": sent_at_utc,
            }
        )
        self._next_tx_pk += 1
        window_start = sent_at_utc - timedelta(hours=24)
        return sum(
            1
            for t in self.transmissions
            if t["device_id"] == device_id and t["sent_at"] > window_start
        )

    async def run_schedule_tick(self, now_local: datetime) -> bool:
        hhmm = now_local.strftime("%H:%M")
        today: date = now_local.date()
        enqueued_any = False
        for dev in self.devices.values():
            if dev["mode"] != "automatic":
                continue
            st = self.settings[dev["id"]]
            if (
                st["auto_open"] == 0
                and st["auto_open_time"] == hhmm
                and st["last_open_sched_date"] != today
            ):
                self._insert_command(
                    dev["id"], CommandKind.SET_SCHEDULE, {"action": "open"}
                )
                st["last_open_sched_date"] = today
                enqueued_any = True
            if (
                st["auto_close"] == 0
                and st["auto_close_time"] == hhmm
                and st["last_close_sched_date"] != today
            ):
                self._insert_command(
                    dev["id"], CommandKind.SET_SCHEDULE, {"action": "close"}
                )
                st["last_close_sched_date"] = today
                enqueued_any = True
        return enqueued_any


# ---------------------------------------------------------------------------
# Helpers & Pure Logic Tests
# ---------------------------------------------------------------------------


def test_compute_day_hint_all_four_cases() -> None:
    tz = ZoneInfo("Europe/Copenhagen")

    # 1. Both by time (0, 0): normal 07:30..21:00 and overnight 22:00..06:00
    assert compute_day_hint(datetime(2026, 11, 21, 7, 29, tzinfo=tz), 0, "07:30", 0, "21:00") is False
    assert compute_day_hint(datetime(2026, 11, 21, 7, 30, tzinfo=tz), 0, "07:30", 0, "21:00") is True
    assert compute_day_hint(datetime(2026, 11, 21, 21, 0, tzinfo=tz), 0, "07:30", 0, "21:00") is False
    assert compute_day_hint(datetime(2026, 11, 21, 23, 0, tzinfo=tz), 0, "22:00", 0, "06:00") is True
    assert compute_day_hint(datetime(2026, 11, 21, 6, 0, tzinfo=tz), 0, "22:00", 0, "06:00") is False
    assert compute_is_day(datetime(2026, 11, 21, 8, 0, tzinfo=tz), "08:00", "08:00") is False

    # 2. Only open by time (0, 1): False before open_time, True at/after
    assert compute_day_hint(datetime(2026, 11, 21, 7, 29, tzinfo=tz), 0, "07:30", 1, "21:00") is False
    assert compute_day_hint(datetime(2026, 11, 21, 7, 30, tzinfo=tz), 0, "07:30", 1, "21:00") is True
    assert compute_day_hint(datetime(2026, 11, 21, 23, 30, tzinfo=tz), 0, "07:30", 1, "21:00") is True

    # 3. Only close by time (1, 0): None before close_time (unknown!), False at/after
    assert compute_day_hint(datetime(2026, 11, 21, 14, 0, tzinfo=tz), 1, "07:30", 0, "21:00") is None
    assert compute_day_hint(datetime(2026, 11, 21, 20, 59, tzinfo=tz), 1, "07:30", 0, "21:00") is None
    assert compute_day_hint(datetime(2026, 11, 21, 21, 0, tzinfo=tz), 1, "07:30", 0, "21:00") is False

    # 4. Both by sun (1, 1): always None (device decides)
    assert compute_day_hint(datetime(2026, 11, 21, 12, 0, tzinfo=tz), 1, "07:30", 1, "21:00") is None
    assert compute_day_hint(datetime(2026, 11, 21, 23, 0, tzinfo=tz), 1, "07:30", 1, "21:00") is None


async def _login(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/login", json={"username": "  DEMO@Example.com ", "password": DEMO_MD5.upper()}
    )
    assert resp.status_code == 200
    return resp.json()["token"]


def _seed_synced_device_state(
    db: FakeDatabase,
    *,
    position: int | None = 50,
    calibrated: bool = True,
    mode: str = "manual",
    is_day: bool = False,
) -> None:
    """Helper setting DB + last_confirmed_auto_settings in sync so tests control downlinks cleanly."""
    dev = next(iter(db.devices.values()))
    st = db.settings[dev["id"]]
    dev["position"] = position
    dev["is_calibrated"] = calibrated
    dev["mode"] = mode
    dev["desired_mode"] = mode
    dev["is_day"] = is_day
    dev["last_seen_at"] = datetime.now(timezone.utc)
    dev["device_flags"] = (
        (1 if mode == "automatic" else 0)
        | (4 if calibrated else 0)
        | (8 if is_day else 0)
        | (16 if st["auto_open"] else 0)
        | (32 if st["auto_close"] else 0)
        | (64 if st["maintain_light"] else 0)
    )
    dev["last_confirmed_auto_settings"] = {
        "auto_open": st["auto_open"],
        "auto_close": st["auto_close"],
        "maintain_light": st["maintain_light"],
        "target_light_level": st["target_light_level"],
        "outside_threshold_open": st["outside_threshold_open"],
        "outside_threshold_close": st["outside_threshold_close"],
    }


# ---------------------------------------------------------------------------
# End-to-End FastAPI + MockTransport Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_validation_and_uncalibrated_guards() -> None:
    settings = make_test_settings()
    codec = ProtocolCodec.from_file(PROTOCOL_JSON_PATH)
    db = FakeDatabase()
    transport = MockTransport(codec=codec, dev_id=settings.ttn_devid, emit_initial_heartbeat=False)
    app = create_app(settings=settings, db=db, transport=transport)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json() == {
                "ok": True,
                "transportConnected": True,
                "protocolVersion": "v4",
            }

            assert (await client.get("/status")).status_code == 401
            assert (await client.get("/debug")).status_code == 401
            assert (await client.get("/status", headers={"Authorization": "Basic abc"})).status_code == 401

            bad_login = await client.post(
                "/login", json={"username": "demo@example.com", "password": "wrong"}
            )
            assert bad_login.status_code == 401

            t1 = await _login(client)
            t2 = await _login(client)
            assert t1 != t2
            headers = {"Authorization": f"Bearer {t1}"}
            headers2 = {"Authorization": f"Bearer {t2}"}

            st = await client.get("/status", headers=headers)
            assert st.status_code == 200
            assert st.json()["mode"] == "automatic"
            assert st.json()["position"] is None
            assert st.json()["calibrated"] is False
            assert st.json()["lastSeenAt"] is None

            pos_uncal = await client.post(
                "/position", json={"position": 50}, headers=headers2
            )
            assert pos_uncal.status_code == 409
            assert pos_uncal.json() == {"error": "curtain is not calibrated"}

            # Strict validation -> 400 with {"error": ...}
            for method, url, body in [
                ("POST", "/position", {"position": True}),
                ("POST", "/position", {"position": 50.5}),
                ("POST", "/position", {"position": 50, "extra": 1}),
                (
                    "PUT",
                    "/auto",
                    {"autoOpen": 1, "autoOpenTime": "07:30", "autoClose": 0},
                ),
                (
                    "PUT",
                    "/auto",
                    {
                        "autoOpen": True,
                        "autoOpenTime": "07:30",
                        "autoClose": 0,
                        "autoCloseTime": "22:45",
                        "maintainLight": 0,
                        "targetLightLevel": 50,
                    },
                ),
                (
                    "PUT",
                    "/settings",
                    {"outsideThresholdOpen": 50, "outsideThresholdClose": 50},
                ),
                (
                    "PUT",
                    "/settings",
                    {
                        "outsideThresholdOpen": 50,
                        "outsideThresholdClose": 20,
                        "reverseMode": 1,
                    },
                ),
            ]:
                resp = await client.request(method, url, json=body, headers=headers)
                assert resp.status_code == 400
                assert "error" in resp.json()

            assert (await client.get("/telemetry?hours=0", headers=headers)).status_code == 400
            assert (await client.get("/debug?limit=0", headers=headers)).status_code == 400
            assert (await client.get("/debug?limit=101", headers=headers)).status_code == 400


@pytest.mark.asyncio
async def test_v4_position_from_auto_button_override_and_startup_lastid() -> None:
    settings = make_test_settings()
    codec = ProtocolCodec.from_file(PROTOCOL_JSON_PATH)
    db = FakeDatabase()
    transport = MockTransport(
        codec=codec,
        dev_id=settings.ttn_devid,
        reply_delay_sec=0.02,
        startup_delay_sec=0.01,
        emit_initial_heartbeat=True,
    )
    app = create_app(settings=settings, db=db, transport=transport)

    async with app.router.lifespan_context(app):
        # Default automatic boot emits 1 Type 3 auto settings sync
        await asyncio.sleep(0.12)
        assert len(transport.sent_downlinks) == 1
        assert transport.sent_downlinks[0].kind == CommandKind.SET_AUTO_SETTINGS

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            token = await _login(client)
            headers = {"Authorization": f"Bearer {token}"}
            st_boot = (await client.get("/status", headers=headers)).json()
            assert st_boot["downlinksUsed24h"] == 1
            assert st_boot["mode"] == "automatic"

            # v4 rule: POST /position IS ALLOWED directly from default automatic mode -> 1 Type 1 downlink!
            before_pos = len(transport.sent_downlinks)
            r_pos = await client.post("/position", json={"position": 40}, headers=headers)
            assert r_pos.status_code == 200
            assert (await client.get("/status", headers=headers)).json()["mode"] == "manual"
            await asyncio.sleep(0.12)

            pos_dls = transport.sent_downlinks[before_pos:]
            assert [d.kind for d in pos_dls] == [CommandKind.SET_POSITION]
            assert pos_dls[0].args == {"position": 40}
            st_pos = (await client.get("/status", headers=headers)).json()
            assert st_pos["position"] == 40
            assert st_pos["mode"] == "manual"
            assert st_pos["moving"] is False

            # Now in manual mode: PUT /auto -> 0 DLs; PUT /mode automatic -> 1 consolidated Type 3!
            before_auto = len(transport.sent_downlinks)
            await client.put(
                "/auto",
                json={
                    "autoOpen": 0,
                    "autoOpenTime": "00:00",
                    "autoClose": 0,
                    "autoCloseTime": "23:59",
                    "maintainLight": 0,
                    "targetLightLevel": 60,
                },
                headers=headers,
            )
            await asyncio.sleep(0.12)
            assert len(transport.sent_downlinks) == before_auto

            await client.put("/mode", json={"mode": "automatic"}, headers=headers)
            await asyncio.sleep(0.12)
            assert len(transport.sent_downlinks) == before_auto + 1
            assert transport.sent_downlinks[-1].kind == CommandKind.SET_AUTO_SETTINGS
            assert transport.sent_downlinks[-1].args["set_mode"] == 1
            assert transport.sent_downlinks[-1].args["automatic"] == 1
            assert (await client.get("/status", headers=headers)).json()["mode"] == "automatic"

            # Physical button override switches reported & desired mode back to manual
            await client.put("/mode", json={"mode": "automatic"}, headers=headers)
            await asyncio.sleep(0.12)
            assert (await client.get("/status", headers=headers)).json()["mode"] == "automatic"

            f_port, btn_payload = codec.encode_state_uplink(
                DecodedStateUplink(
                    last_cmd_id=transport.last_cmd_id,
                    reason="button",
                    mode="manual",
                    is_moving=False,
                    is_calibrated=True,
                    is_day=True,
                    open_by_sun=False,
                    close_by_sun=False,
                    keep_indoor_light=False,
                    raw_flags=0x0C,
                    position=15,
                )
            )
            await transport.inject_uplink(f_port, btn_payload)
            st_btn = (await client.get("/status", headers=headers)).json()
            assert st_btn["mode"] == "manual"
            assert st_btn["position"] == 15

            # EEPROM lastId continuity + startup adopts device's reported mode (manual)
            before_startup = len(transport.sent_downlinks)
            f_port, startup_payload = codec.encode_state_uplink(
                DecodedStateUplink(
                    last_cmd_id=42,
                    reason="startup",
                    mode="manual",
                    is_moving=False,
                    is_calibrated=True,
                    is_day=True,
                    open_by_sun=False,
                    close_by_sun=False,
                    keep_indoor_light=False,
                    raw_flags=0x0C,
                    position=15,
                )
            )
            await transport.inject_uplink(f_port, startup_payload)
            await asyncio.sleep(0.12)
            # Settings already match last_confirmed_auto_settings + flags, and mode is manual -> 0 DLs!
            assert len(transport.sent_downlinks) == before_startup
            dev = next(iter(db.devices.values()))
            assert dev["next_cmd_id"] == 43


@pytest.mark.asyncio
async def test_plan_sync_coalescing_time_flip_and_golden_rule() -> None:
    settings = make_test_settings(dispatch_poll_sec=10.0)
    codec = ProtocolCodec.from_file(PROTOCOL_JSON_PATH)
    db = FakeDatabase()
    transport = MockTransport(
        codec=codec,
        dev_id=settings.ttn_devid,
        reply_delay_sec=0.01,
        emit_initial_heartbeat=False,
    )
    app = create_app(settings=settings, db=db, transport=transport)

    async with app.router.lifespan_context(app):
        manager: DeviceManager = app.state.manager
        if manager._dispatch_task is not None:
            manager._dispatch_task.cancel()
            try:
                await manager._dispatch_task
            except asyncio.CancelledError:
                pass
            manager._dispatch_task = None

        await db.seed_defaults(settings, codec)
        _seed_synced_device_state(db, position=50, calibrated=True, mode="manual", is_day=False)
        # Mark already synced for this manager instance
        manager._synced_since_boot.add(settings.ttn_devid)

        tz = ZoneInfo("Europe/Copenhagen")
        day_utc = datetime(2026, 11, 21, 12, 0, 0, tzinfo=tz).astimezone(timezone.utc)  # 12:00 local -> daytime

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            token = await _login(client)
            headers = {"Authorization": f"Bearer {token}"}

            # 1. Golden rule: no-op PUT /mode manual -> 0 DLs
            await client.put("/mode", json={"mode": "manual"}, headers=headers)
            await manager.dispatch_once(now_utc=day_utc)
            assert len(transport.sent_downlinks) == 0

            # 2. Golden rule: no-op PUT /settings -> 0 DLs
            await client.put(
                "/settings",
                json={"outsideThresholdOpen": 80, "outsideThresholdClose": 10},
                headers=headers,
            )
            await manager.dispatch_once(now_utc=day_utc)
            assert len(transport.sent_downlinks) == 0

            # 3. Time-only PUT /auto in automatic mode that does NOT flip day/night -> 0 DLs
            _seed_synced_device_state(db, position=50, calibrated=True, mode="automatic", is_day=False)
            early_utc = datetime(2026, 11, 21, 6, 0, 0, tzinfo=tz).astimezone(timezone.utc)
            await client.put(
                "/auto",
                json={
                    "autoOpen": 0,
                    "autoOpenTime": "08:00",
                    "autoClose": 1,
                    "autoCloseTime": "21:00",
                    "maintainLight": 0,
                    "targetLightLevel": 60,
                },
                headers=headers,
            )
            await manager.dispatch_once(now_utc=early_utc)
            assert len(transport.sent_downlinks) == 0

            # 4. Time-only PUT /auto in automatic mode that FLIPS day/night right now -> Type 4!
            await client.put(
                "/auto",
                json={
                    "autoOpen": 0,
                    "autoOpenTime": "05:30",
                    "autoClose": 1,
                    "autoCloseTime": "21:00",
                    "maintainLight": 0,
                    "targetLightLevel": 60,
                },
                headers=headers,
            )
            await manager.dispatch_once(now_utc=early_utc)
            assert len(transport.sent_downlinks) == 1
            assert transport.sent_downlinks[0].kind == CommandKind.SET_SCHEDULE
            assert transport.sent_downlinks[0].args == {"action": "open"}
            await asyncio.sleep(0.05)

            # 5. Rapid coalescing: PUT /settings + PUT /auto + PUT /mode automatic -> 1 single Type 3!
            _seed_synced_device_state(db, position=50, calibrated=True, mode="manual", is_day=True)
            before_coalesce = len(transport.sent_downlinks)
            await client.put(
                "/settings",
                json={"outsideThresholdOpen": 85, "outsideThresholdClose": 15},
                headers=headers,
            )
            await client.put(
                "/auto",
                json={
                    "autoOpen": 0,
                    "autoOpenTime": "05:30",
                    "autoClose": 0,
                    "autoCloseTime": "21:00",
                    "maintainLight": 1,
                    "targetLightLevel": 30,
                },
                headers=headers,
            )
            await client.put("/mode", json={"mode": "automatic"}, headers=headers)
            # Notice at 22:00 local, is_day (True) != hint (False) -> sets mode + day + settings in ONE Type 3!
            late_utc = datetime(2026, 11, 21, 22, 0, 0, tzinfo=tz).astimezone(timezone.utc)
            await manager.dispatch_once(now_utc=late_utc)
            sent_coalesced = transport.sent_downlinks[before_coalesce:]
            assert len(sent_coalesced) == 1
            assert sent_coalesced[0].kind == CommandKind.SET_AUTO_SETTINGS
            assert sent_coalesced[0].args["set_mode"] == 1
            assert sent_coalesced[0].args["automatic"] == 1
            assert sent_coalesced[0].args["set_day"] == 1
            assert sent_coalesced[0].args["is_day"] == 0
            assert sent_coalesced[0].args["maintain_light"] == 1
            assert sent_coalesced[0].args["target_light_level"] == 30
            await asyncio.sleep(0.05)

            # 6. Startup uplink in automatic mode when last_confirmed_auto_settings is unset -> 1 Type 3!
            dev_row = next(iter(db.devices.values()))
            dev_row["last_confirmed_auto_settings"] = None
            before_auto_startup = len(transport.sent_downlinks)
            f_port, auto_startup = codec.encode_state_uplink(
                DecodedStateUplink(
                    last_cmd_id=0,
                    reason="startup",
                    mode="automatic",
                    is_moving=False,
                    is_calibrated=True,
                    is_day=False,
                    open_by_sun=False,
                    close_by_sun=False,
                    keep_indoor_light=False,
                    raw_flags=0x05,
                    position=50,
                )
            )
            await transport.inject_uplink(f_port, auto_startup)
            await manager.dispatch_once(now_utc=late_utc)
            sent_auto_startup = transport.sent_downlinks[before_auto_startup:]
            assert len(sent_auto_startup) == 1
            assert sent_auto_startup[0].kind == CommandKind.SET_AUTO_SETTINGS


@pytest.mark.asyncio
async def test_v3_fallback_cfg_guard() -> None:
    loaded = load_settings(
        {"TTN_DEVID": "curtain-01", "TRANSPORT_MODE": "mock", "PROTOCOL_VERSION": "v3"}
    )
    assert loaded.protocol_version == "v3"
    assert loaded.protocol_config_path.name == "protocol.v3.json"

    settings = make_test_settings(
        protocol_version="v3",
        protocol_config_path=loaded.protocol_config_path,
    )
    codec = ProtocolCodec.from_file(settings.protocol_config_path)
    db = FakeDatabase()
    transport = MockTransport(
        codec=codec,
        dev_id=settings.ttn_devid,
        reply_delay_sec=0.01,
        emit_initial_heartbeat=False,
    )
    app = create_app(settings=settings, db=db, transport=transport)

    async with app.router.lifespan_context(app):
        _seed_synced_device_state(db, position=50, calibrated=True, mode="manual", is_day=False)
        app.state.manager._synced_since_boot.add(settings.ttn_devid)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            token = await _login(client)
            headers = {"Authorization": f"Bearer {token}"}
            await client.put(
                "/auto",
                json={
                    "autoOpen": 0,
                    "autoOpenTime": "00:00",
                    "autoClose": 0,
                    "autoCloseTime": "23:59",
                    "maintainLight": 1,
                    "targetLightLevel": 45,
                },
                headers=headers,
            )
            await client.put("/mode", json={"mode": "automatic"}, headers=headers)
            await asyncio.sleep(0.25)
            kinds = [d.kind for d in transport.sent_downlinks]
            assert kinds == [
                CommandKind.SET_AUTO_SETTINGS,
                CommandKind.SET_SCHEDULE,
                CommandKind.SET_MODE,
            ]
            # v3 never sets bits 3..6
            assert "set_mode" not in transport.sent_downlinks[0].args


@pytest.mark.asyncio
async def test_timeout_retry_not_delivered_scheduler_and_debug_endpoint() -> None:
    settings = make_test_settings(dispatch_poll_sec=10.0)
    codec = ProtocolCodec.from_file(PROTOCOL_JSON_PATH)
    db = FakeDatabase()
    transport = MockTransport(
        codec=codec,
        dev_id=settings.ttn_devid,
        reply_delay_sec=10.0,
        emit_initial_heartbeat=False,
    )
    app = create_app(settings=settings, db=db, transport=transport)

    async with app.router.lifespan_context(app):
        manager: DeviceManager = app.state.manager
        if manager._dispatch_task is not None:
            manager._dispatch_task.cancel()
            try:
                await manager._dispatch_task
            except asyncio.CancelledError:
                pass
            manager._dispatch_task = None

        _seed_synced_device_state(db, position=50, calibrated=True, mode="manual", is_day=False)
        manager._synced_since_boot.add(settings.ttn_devid)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            token = await _login(client)
            headers = {"Authorization": f"Bearer {token}"}

            # Switch to automatic while day_hint=True -> v4 emits 1 Type 3 (or 1 Type 2 if day matches)
            await client.put("/mode", json={"mode": "automatic"}, headers=headers)
            tz = ZoneInfo("Europe/Copenhagen")
            t0 = datetime(2026, 11, 21, 12, 0, 0, tzinfo=tz).astimezone(timezone.utc)
            await manager.dispatch_once(now_utc=t0)
            assert len(transport.sent_downlinks) == 1
            first_dl = transport.sent_downlinks[0]

            # Before 25s timeout -> no retry yet
            await manager.dispatch_once(now_utc=t0 + timedelta(seconds=24))
            assert len(transport.sent_downlinks) == 1

            # Timeout 1 (+25s): retries exact same cmd_id
            await manager.dispatch_once(now_utc=t0 + timedelta(seconds=25))
            assert len(transport.sent_downlinks) == 2
            assert transport.sent_downlinks[1].cmd_id == first_dl.cmd_id

            # Inspect /debug while IN_FLIGHT
            dbg_inflight = (await client.get("/debug?limit=10", headers=headers)).json()
            assert dbg_inflight["inFlightCommand"] is not None
            assert dbg_inflight["inFlightCommand"]["attempts"] == 2
            assert dbg_inflight["lastCommandSent"]["cmdId"] == first_dl.cmd_id
            assert dbg_inflight["server"]["protocolVersion"] == "v4"
            assert dbg_inflight["server"]["confirmedDownlinks"] is False
            assert dbg_inflight["server"]["handshakeTimeoutSec"] == 25

            # Timeout 2 (+50s): marks NOT_DELIVERED, clears downlinks, rolls back desired_mode
            await manager.dispatch_once(now_utc=t0 + timedelta(seconds=50))
            assert transport.cleared_count == 1
            st_rolled_back = (await client.get("/status", headers=headers)).json()
            assert st_rolled_back["mode"] == "manual"

            # Now send a successful position command and verify /debug
            await client.post("/position", json={"position": 30}, headers=headers)
            t_pos = t0 + timedelta(seconds=60)
            await manager.dispatch_once(now_utc=t_pos)
            _, reply_bytes = codec.encode_state_uplink(
                DecodedStateUplink(
                    last_cmd_id=transport.sent_downlinks[-1].cmd_id,
                    reason="app_command",
                    mode="manual",
                    is_moving=False,
                    is_calibrated=True,
                    is_day=False,
                    open_by_sun=False,
                    close_by_sun=True,
                    keep_indoor_light=False,
                    raw_flags=0x24,
                    position=30,
                )
            )
            await transport.inject_uplink(1, reply_bytes, received_at=t_pos + timedelta(seconds=2))

            dbg_done = (await client.get("/debug?limit=5", headers=headers)).json()
            assert dbg_done["inFlightCommand"] is None
            assert dbg_done["device"]["position"] == 30
            assert dbg_done["device"]["flagsHex"] == "0x24"
            assert dbg_done["device"]["decodedFlags"]["closeBySun"] is True
            assert dbg_done["device"]["settingsInSync"] is True
            assert dbg_done["lastCommandSent"]["kind"] == "set_position"
            assert dbg_done["lastCommandSent"]["status"] == "CONFIRMED"
            assert dbg_done["lastCommandSent"]["roundTripMs"] == 2000
            assert dbg_done["lastUplinkReceived"]["reason"] == "app_command"
            assert dbg_done["lastUplinkReceived"]["rawPayloadHex"].endswith("03241E")
            assert len(dbg_done["recentCommands"]) <= 5
            assert len(dbg_done["recentUplinks"]) >= 1

            # Scheduler loop: automatic device triggers Type 4 once per date
            _, auto_up = codec.encode_state_uplink(
                DecodedStateUplink(
                    last_cmd_id=0,
                    reason="app_command",
                    mode="automatic",
                    is_moving=False,
                    is_calibrated=True,
                    is_day=False,
                    open_by_sun=False,
                    close_by_sun=True,
                    keep_indoor_light=False,
                    raw_flags=0x25,
                    position=30,
                )
            )
            await transport.inject_uplink(1, auto_up)
            open_dt = datetime(2026, 11, 21, 7, 30, 5, tzinfo=tz)
            assert await manager.scheduler_once(now_local=open_dt) is True
            assert await manager.scheduler_once(now_local=open_dt + timedelta(seconds=20)) is False


def test_load_settings_defaults_and_postgres_password() -> None:
    base_env = {
        "TTN_DEVID": "curtain-01",
        "TRANSPORT_MODE": "mock",
    }
    default_s = load_settings(base_env)
    assert default_s.protocol_version == "v4"
    assert default_s.protocol_config_path.name == "protocol.json"

    s_timeout = load_settings({**base_env, "HANDSHAKE_TIMEOUT_SEC": "75"})
    assert s_timeout.handshake_timeout_sec == 75

    s_plain_pw = load_settings(
        {
            **base_env,
            "DEMO_USER_PASSWORD": "my-new-password",
            "DEMO_USER_PASSWORD_MD5": "00000000000000000000000000000000",
        }
    )
    assert (
        s_plain_pw.demo_user_password_md5 == hashlib.md5(b"my-new-password").hexdigest()
    )

    s_md5_pw = load_settings(
        {
            **base_env,
            "DEMO_USER_PASSWORD_MD5": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        }
    )
    assert s_md5_pw.demo_user_password_md5 == "a" * 32

    settings_pw = load_settings(
        {
            **base_env,
            "POSTGRES_USER": "curtain_admin",
            "POSTGRES_PASSWORD": "p@ss:word#1/2%",
            "POSTGRES_DB": "curtain_db",
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5433",
            "DATABASE_URL": "postgresql://ignored:ignored@localhost:5432/ignored",
        }
    )
    assert (
        settings_pw.database_url
        == "postgresql://curtain_admin:p%40ss%3Aword%231%2F2%25@postgres:5433/curtain_db"
    )


@pytest.mark.asyncio
async def test_sync_endpoint_and_rolling_24h_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_test_settings(dispatch_poll_sec=10.0)
    codec = ProtocolCodec.from_file(PROTOCOL_JSON_PATH)
    db = FakeDatabase()
    transport = MockTransport(
        codec=codec,
        dev_id=settings.ttn_devid,
        reply_delay_sec=10.0,
        emit_initial_heartbeat=False,
    )
    app = create_app(settings=settings, db=db, transport=transport)

    async with app.router.lifespan_context(app):
        manager: DeviceManager = app.state.manager
        if manager._dispatch_task is not None:
            manager._dispatch_task.cancel()
            try:
                await manager._dispatch_task
            except asyncio.CancelledError:
                pass
            manager._dispatch_task = None

        user_id = next(iter(db.users.values()))["id"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            token = await _login(client)
            headers = {"Authorization": f"Bearer {token}"}

            assert (await client.post("/sync", headers=headers)).status_code == 200
            t1 = datetime(2026, 11, 21, 23, 59, 50, tzinfo=timezone.utc)
            await manager.dispatch_once(now_utc=t1)
            t2 = datetime(2026, 11, 22, 0, 0, 15, tzinfo=timezone.utc)
            await manager.dispatch_once(now_utc=t2)

            st_after_retry = await manager.get_status(
                user_id, now_utc=datetime(2026, 11, 22, 0, 1, 0, tzinfo=timezone.utc)
            )
            assert st_after_retry.downlinksUsed24h == 2
            assert st_after_retry.downlinkNextSlotAt == "2026-11-22T23:59:50Z"

            t_burst = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)
            await manager.dispatch_once(now_utc=t_burst)

            with caplog.at_level("WARNING", logger="device_manager"):
                for i in range(1, 12):
                    assert (await client.post("/sync", headers=headers)).status_code == 200
                    await manager.dispatch_once(now_utc=t_burst + timedelta(minutes=i))
                    _, ack_bytes = codec.encode_state_uplink(
                        DecodedStateUplink(
                            last_cmd_id=transport.last_cmd_id if transport.last_cmd_id else i,
                            reason="app_command",
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
                    # Match actual cmd_id sent on wire
                    actual_cmd_id = transport.sent_downlinks[-1].cmd_id
                    _, ack_bytes = codec.encode_state_uplink(
                        DecodedStateUplink(
                            last_cmd_id=actual_cmd_id,
                            reason="app_command",
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
                    await transport.inject_uplink(1, ack_bytes)

            assert "Rolling 24h downlink count for curtain-01 reached 10" in caplog.text
            assert "Rolling 24h downlink count for curtain-01 reached 11" in caplog.text
