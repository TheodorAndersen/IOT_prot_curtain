"""Protocol-agnostic device state orchestration, dispatch loop, and scheduler loop."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from config import Settings
from db import DatabaseConflictError, compute_day_hint, compute_is_day, is_settings_in_sync
from models import (
    AutoRequest,
    CommandKind,
    CommandStatus,
    DebugBudgetModel,
    DebugCommandModel,
    DebugConfirmedAutoSettingsModel,
    DebugDeviceModel,
    DebugResponse,
    DebugSavedSettingsModel,
    DebugServerModel,
    DebugUplinkModel,
    DecodedFlagsModel,
    DecodedLightTelemetry,
    DecodedStateUplink,
    SettingsRequest,
    StatusResponse,
    TelemetryReading,
    TelemetryResponse,
)
from protocol import ProtocolCodec
from transport import LoRaTransport

logger = logging.getLogger(__name__)


class ConflictError(Exception):
    """Raised for HTTP 409 Conflict responses."""


class UnauthorizedError(Exception):
    """Raised for HTTP 401 Unauthorized responses."""


class BadRequestError(Exception):
    """Raised for HTTP 400 Bad Request responses."""


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fmt_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    val = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return val.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        parsed = json.loads(raw)
        return dict(parsed) if isinstance(parsed, dict) else {}
    if isinstance(raw, dict):
        return dict(raw)
    return dict(raw or {})


class DeviceManager:
    """Coordinates REST endpoints, uplinks, single-publisher dispatch, and schedules."""

    def __init__(
        self,
        settings: Settings,
        codec: ProtocolCodec,
        db: Any,
        transport: LoRaTransport,
    ) -> None:
        self.settings = settings
        self.codec = codec
        self.db = db
        self.transport = transport
        self.dispatch_event = asyncio.Event()
        self._tz = ZoneInfo(settings.schedule_timezone)
        self._dispatch_task: asyncio.Task[None] | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._running = False
        self._synced_since_boot: set[str] = set()

    async def start(self) -> None:
        self._running = True
        self._synced_since_boot.clear()
        await self.transport.start(self.handle_uplink)
        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(), name="device-dispatch-loop"
        )
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(), name="device-scheduler-loop"
        )
        if self.settings.sync_on_startup:
            await self.db.enqueue_startup_sync_for_unseen_devices()
        # Wake dispatcher immediately in case queued commands persisted across restart
        self.dispatch_event.set()

    async def stop(self) -> None:
        self._running = False
        for task in (self._dispatch_task, self._scheduler_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._dispatch_task = None
        self._scheduler_task = None
        await self.transport.stop()

    def _is_day_now(self, auto_open_time: str, auto_close_time: str) -> bool:
        now_local = datetime.now(self._tz)
        return compute_is_day(now_local, auto_open_time, auto_close_time)

    # ------------------------------------------------------------------
    # Authentication & REST Operations
    # ------------------------------------------------------------------

    async def login(self, username: str, password_md5: str) -> str:
        email = username.strip().lower()
        user = await self.db.get_user_by_email(email)
        if user is None:
            raise UnauthorizedError("invalid username or password")

        candidate_hash = _hash_secret(password_md5.lower())
        if not hmac.compare_digest(candidate_hash, str(user["password_hash"])):
            raise UnauthorizedError("invalid username or password")

        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_secret(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=self.settings.token_ttl_hours
        )
        await self.db.create_auth_token(user["id"], token_hash, expires_at)
        return raw_token

    async def authenticate_token(self, raw_token: str) -> uuid.UUID:
        if not raw_token:
            raise UnauthorizedError("not logged in")
        token_hash = _hash_secret(raw_token)
        user_id = await self.db.get_user_id_by_token_hash(token_hash)
        if user_id is None:
            raise UnauthorizedError("not logged in")
        return user_id

    async def get_status(
        self, user_id: uuid.UUID, now_utc: datetime | None = None
    ) -> StatusResponse:
        row = await self.db.get_device_and_settings_by_user(user_id, now_utc=now_utc)
        if row is None:
            raise ConflictError("device not found")
        effective_mode = (
            row["desired_mode"] if row["has_pending_set_mode"] else row["mode"]
        )
        oldest_dl = row.get("oldest_downlink_sent_at")
        next_slot_at = (
            _fmt_utc(oldest_dl + timedelta(hours=24))
            if oldest_dl is not None
            else None
        )
        return StatusResponse(
            position=row["position"],
            mode=effective_mode,
            autoOpen=int(row["auto_open"]),
            autoOpenTime=str(row["auto_open_time"]),
            autoClose=int(row["auto_close"]),
            autoCloseTime=str(row["auto_close_time"]),
            maintainLight=int(row["maintain_light"]),
            targetLightLevel=int(row["target_light_level"]),
            outsideThresholdOpen=int(row["outside_threshold_open"]),
            outsideThresholdClose=int(row["outside_threshold_close"]),
            moving=bool(row["is_moving"]),
            calibrated=bool(row["is_calibrated"]),
            day=bool(row["is_day"]),
            targetPosition=row["target_position"],
            lastSeenAt=_fmt_utc(row.get("last_seen_at")),
            downlinksUsed24h=int(row.get("downlinks_used_24h") or 0),
            downlinkBudget24h=self.codec.spec.handshake.daily_downlink_warn_threshold,
            downlinkNextSlotAt=next_slot_at,
        )

    async def set_position(self, user_id: uuid.UUID, position: int) -> None:
        try:
            await self.db.enqueue_position(user_id, position, self.codec)
        except DatabaseConflictError as exc:
            raise ConflictError(exc.message) from exc
        self.dispatch_event.set()

    async def set_mode(self, user_id: uuid.UUID, mode: str) -> None:
        try:
            changed = await self.db.enqueue_mode(user_id, mode)
        except DatabaseConflictError as exc:
            raise ConflictError(exc.message) from exc
        if changed:
            self.dispatch_event.set()

    async def sync_device(self, user_id: uuid.UUID) -> None:
        try:
            await self.db.enqueue_sync(user_id)
        except DatabaseConflictError as exc:
            raise ConflictError(exc.message) from exc
        self.dispatch_event.set()

    async def set_auto(self, user_id: uuid.UUID, req: AutoRequest) -> None:
        try:
            changed = await self.db.update_auto_settings(
                user_id=user_id,
                auto_open=req.autoOpen,
                auto_open_time=req.autoOpenTime,
                auto_close=req.autoClose,
                auto_close_time=req.autoCloseTime,
                maintain_light=req.maintainLight,
                target_light_level=req.targetLightLevel,
            )
        except DatabaseConflictError as exc:
            raise ConflictError(exc.message) from exc
        if changed:
            self.dispatch_event.set()

    async def set_settings(self, user_id: uuid.UUID, req: SettingsRequest) -> None:
        try:
            changed = await self.db.update_thresholds(
                user_id=user_id,
                outside_threshold_open=req.outsideThresholdOpen,
                outside_threshold_close=req.outsideThresholdClose,
            )
        except DatabaseConflictError as exc:
            raise ConflictError(exc.message) from exc
        if changed:
            self.dispatch_event.set()

    def _to_debug_command(self, row: dict[str, Any]) -> DebugCommandModel:
        args = _parse_args(row.get("command_args"))
        cmd_id = row.get("cmd_id")
        kind = CommandKind(str(row["command_kind"]))
        sent_at = row.get("sent_at")
        resolved_at = row.get("resolved_at")
        round_trip_ms: int | None = None
        if sent_at is not None and resolved_at is not None:
            round_trip_ms = max(0, int(round((resolved_at - sent_at).total_seconds() * 1000)))
        wire_hex = row.get("wire_payload_hex")
        return DebugCommandModel(
            id=int(row["id"]),
            cmdId=int(cmd_id) if cmd_id is not None else None,
            kind=kind,
            args=args,
            status=CommandStatus(str(row["status"])),
            attempts=int(row["attempts"]),
            wireFport=int(row["wire_fport"]) if row.get("wire_fport") is not None else None,
            wirePayloadHex=str(wire_hex).upper() if wire_hex else None,
            description=self.codec.describe_command(
                int(cmd_id) if cmd_id is not None else None, kind, args
            ),
            createdAt=str(_fmt_utc(row["created_at"])),
            sentAt=_fmt_utc(sent_at),
            resolvedAt=_fmt_utc(resolved_at),
            roundTripMs=round_trip_ms,
        )

    def _to_debug_uplink(
        self, row: dict[str, Any], now_utc: datetime
    ) -> DebugUplinkModel:
        received_at: datetime = row["received_at"]
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        flags = int(row["flags"])
        flags_dict = self.codec.decode_flags_dict(flags)
        last_cmd_id = int(row["last_cmd_id"])
        reason = str(row["reason"])
        mode = str(row["mode"])
        pos = row["position"]
        return DebugUplinkModel(
            id=int(row["id"]),
            receivedAt=str(_fmt_utc(received_at)),
            ageSec=max(0, int((now_utc - received_at).total_seconds())),
            lastCmdId=last_cmd_id,
            reason=reason,
            mode="automatic" if mode == "automatic" else "manual",
            flags=flags,
            flagsHex=f"0x{flags:02X}",
            decodedFlags=DecodedFlagsModel(**flags_dict),
            position=int(pos) if pos is not None else None,
            rawFport=int(row["raw_fport"]),
            rawPayloadHex=str(row["raw_payload_hex"]).upper(),
            description=self.codec.describe_state_uplink(
                last_cmd_id,
                reason,
                mode,
                int(pos) if pos is not None else None,
                flags_dict["moving"],
                flags_dict["day"],
            ),
        )

    async def get_debug(
        self, user_id: uuid.UUID, limit: int = 20, now_utc: datetime | None = None
    ) -> DebugResponse:
        if limit < 1 or limit > 100:
            raise BadRequestError("limit must be between 1 and 100")

        ref_utc = now_utc.astimezone(timezone.utc) if now_utc is not None else datetime.now(timezone.utc)
        now_local = ref_utc.astimezone(self._tz)

        snap = await self.db.get_debug_snapshot(user_id, limit=limit, now_utc=ref_utc)
        if snap is None:
            raise ConflictError("device not found")

        dev = snap["device"]
        effective_mode = (
            dev["desired_mode"] if dev["has_pending_set_mode"] else dev["mode"]
        )
        flags_raw = int(dev.get("device_flags") or 0)
        flags_dict = self.codec.decode_flags_dict(flags_raw)

        last_seen_at = dev.get("last_seen_at")
        if last_seen_at is not None and last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
        last_seen_ago = (
            max(0, int((ref_utc - last_seen_at).total_seconds()))
            if last_seen_at is not None
            else None
        )

        confirmed_raw = _parse_args(dev.get("last_confirmed_auto_settings")) if dev.get("last_confirmed_auto_settings") is not None else None
        confirmed_model = (
            DebugConfirmedAutoSettingsModel(
                autoOpen=int(confirmed_raw["auto_open"]),
                autoClose=int(confirmed_raw["auto_close"]),
                maintainLight=int(confirmed_raw["maintain_light"]),
                targetLightLevel=int(confirmed_raw["target_light_level"]),
                outsideThresholdOpen=int(confirmed_raw["outside_threshold_open"]),
                outsideThresholdClose=int(confirmed_raw["outside_threshold_close"]),
            )
            if confirmed_raw is not None
            else None
        )

        oldest_dl = dev.get("oldest_downlink_sent_at")
        next_slot_at = (
            _fmt_utc(oldest_dl + timedelta(hours=24))
            if oldest_dl is not None
            else None
        )

        day_hint = compute_day_hint(
            now_local,
            int(dev["auto_open"]),
            str(dev["auto_open_time"]),
            int(dev["auto_close"]),
            str(dev["auto_close_time"]),
        )

        recent_uplinks = [
            self._to_debug_uplink(u, ref_utc) for u in snap["recent_uplinks"]
        ]
        recent_commands = [self._to_debug_command(c) for c in snap["recent_commands"]]

        return DebugResponse(
            server=DebugServerModel(
                serverTime=str(_fmt_utc(ref_utc)),
                scheduleTimezone=self.settings.schedule_timezone,
                localTime=now_local.isoformat(timespec="seconds"),
                transportMode="mock" if self.settings.transport_mode == "mock" else "ttn_mqtt",
                transportConnected=self.transport.is_connected,
                protocolVersion=self.codec.spec.version,
                confirmedDownlinks=self.codec.spec.confirmed_downlinks,
                handshakeTimeoutSec=self.codec.spec.handshake.timeout_sec,
                handshakeMaxAttempts=self.codec.spec.handshake.max_attempts,
            ),
            device=DebugDeviceModel(
                ttnDeviceId=str(dev["ttn_device_id"]),
                devEui=str(dev["dev_eui"]),
                name=str(dev["name"]),
                position=dev["position"],
                targetPosition=dev["target_position"],
                reportedMode=dev["mode"],
                desiredMode=dev["desired_mode"],
                effectiveMode=effective_mode,
                moving=bool(dev["is_moving"]),
                calibrated=bool(dev["is_calibrated"]),
                day=bool(dev["is_day"]),
                computedDayHint=day_hint,
                flagsRaw=flags_raw,
                flagsHex=f"0x{flags_raw:02X}",
                decodedFlags=DecodedFlagsModel(**flags_dict),
                lastReason=dev.get("last_reason"),
                lastCmdIdAck=int(dev.get("last_cmd_id_ack") or 0),
                nextCmdId=int(dev.get("next_cmd_id") or 1),
                lastSeenAt=_fmt_utc(last_seen_at),
                lastSeenAgoSec=last_seen_ago,
                settingsInSync=is_settings_in_sync(dev, dev, self.codec),
                savedSettings=DebugSavedSettingsModel(
                    autoOpen=int(dev["auto_open"]),
                    autoOpenTime=str(dev["auto_open_time"]),
                    autoClose=int(dev["auto_close"]),
                    autoCloseTime=str(dev["auto_close_time"]),
                    maintainLight=int(dev["maintain_light"]),
                    targetLightLevel=int(dev["target_light_level"]),
                    outsideThresholdOpen=int(dev["outside_threshold_open"]),
                    outsideThresholdClose=int(dev["outside_threshold_close"]),
                ),
                lastConfirmedAutoSettings=confirmed_model,
            ),
            budget=DebugBudgetModel(
                downlinksUsed24h=int(dev.get("downlinks_used_24h") or 0),
                downlinkBudget24h=self.codec.spec.handshake.daily_downlink_warn_threshold,
                downlinkNextSlotAt=next_slot_at,
            ),
            inFlightCommand=(
                self._to_debug_command(snap["in_flight"])
                if snap["in_flight"] is not None
                else None
            ),
            queuedCommands=[self._to_debug_command(c) for c in snap["queued"]],
            lastCommandSent=(
                self._to_debug_command(snap["last_sent"])
                if snap["last_sent"] is not None
                else None
            ),
            lastUplinkReceived=recent_uplinks[0] if recent_uplinks else None,
            recentCommands=recent_commands,
            recentUplinks=recent_uplinks,
        )

    async def get_telemetry(self, user_id: uuid.UUID, hours: int) -> TelemetryResponse:
        if hours < 1 or hours > 168:
            raise BadRequestError("hours must be between 1 and 168")
        result = await self.db.get_telemetry(user_id, hours)
        if result is None:
            raise ConflictError("device not found")
        ttn_device_id, rows = result
        tu = self.codec.spec.telemetry_uplink
        readings = [
            TelemetryReading(
                recordedAt=str(_fmt_utc(r["recorded_at"])),
                lightLevel=int(r["light_level"]),
            )
            for r in rows
        ]
        return TelemetryResponse(
            deviceId=ttn_device_id,
            sensor=tu.sensor,
            unit=tu.unit,
            range=[0, tu.max_raw_value],
            readings=readings,
        )

    # ------------------------------------------------------------------
    # Uplink Ingestion
    # ------------------------------------------------------------------

    async def handle_uplink(
        self, dev_id: str, received_at: datetime, f_port: int, payload: bytes
    ) -> None:
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        else:
            received_at = received_at.astimezone(timezone.utc)

        try:
            decoded = self.codec.decode_uplink(f_port, payload)
        except ValueError as exc:
            logger.warning(
                "Discarding malformed uplink from %s on f_port=%d (hex=%s): %s",
                dev_id,
                f_port,
                payload.hex(),
                exc,
            )
            return

        if decoded is None:
            logger.debug(
                "Ignoring unknown/disabled uplink f_port=%d from %s", f_port, dev_id
            )
            return

        if isinstance(decoded, DecodedStateUplink):
            first_since_boot = dev_id not in self._synced_since_boot
            found, should_wake = await self.db.apply_state_uplink(
                ttn_device_id=dev_id,
                received_at=received_at,
                decoded=decoded,
                raw_fport=f_port,
                raw_payload_hex=payload.hex(),
                codec=self.codec,
                first_uplink_since_boot=first_since_boot,
            )
            if not found:
                logger.warning("Ignoring uplink for unknown device %s", dev_id)
                return
            self._synced_since_boot.add(dev_id)
            if should_wake:
                self.dispatch_event.set()
            return

        if isinstance(decoded, DecodedLightTelemetry):
            tu = self.codec.spec.telemetry_uplink
            n = len(decoded.samples)
            rows: list[tuple[datetime, int]] = []
            for idx, raw_val in enumerate(decoded.samples):
                val = raw_val
                if val > tu.max_raw_value:
                    logger.warning(
                        "Clamping telemetry sample %d from %s (%d -> %d)",
                        idx,
                        dev_id,
                        val,
                        tu.max_raw_value,
                    )
                    val = tu.max_raw_value
                ts = received_at - timedelta(
                    minutes=(n - 1 - idx) * tu.sample_interval_min
                )
                rows.append((ts, val))

            found = await self.db.insert_light_telemetry(dev_id, received_at, rows)
            if not found:
                logger.warning("Ignoring telemetry uplink for unknown device %s", dev_id)

    # ------------------------------------------------------------------
    # Single-Publisher Dispatch Loop & Scheduler Loop
    # ------------------------------------------------------------------

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                woken_by_event = True
                try:
                    await asyncio.wait_for(
                        self.dispatch_event.wait(),
                        timeout=self.settings.dispatch_poll_sec,
                    )
                except asyncio.TimeoutError:
                    woken_by_event = False
                if woken_by_event:
                    await asyncio.sleep(0.03)
                # Clear AFTER 30ms coalesce window & BEFORE scanning so concurrent
                # REST calls collapse cleanly without losing later events.
                self.dispatch_event.clear()
                await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in dispatch_loop iteration")

    async def dispatch_once(self, now_utc: datetime | None = None) -> None:
        if not self.transport.is_connected:
            return
        now = now_utc or datetime.now(timezone.utc)
        now_local = now.astimezone(self._tz)
        devices = await self.db.list_devices()
        warn_threshold = self.codec.spec.handshake.daily_downlink_warn_threshold

        for dev in devices:
            action = await self.db.prepare_dispatch_action(
                dev["id"], now, now_local, self.codec
            )
            if action is None:
                continue
            if action.clear_downlinks:
                await self.transport.clear_downlinks(action.ttn_device_id)
            if action.f_port is not None and action.payload is not None:
                await self.transport.send_downlink(
                    action.ttn_device_id,
                    action.f_port,
                    action.payload,
                    confirmed=action.confirmed,
                )
                rolling_count = await self.db.record_downlink_transmission(
                    device_id=dev["id"],
                    command_id=action.command_id,
                    f_port=action.f_port,
                    confirmed=action.confirmed,
                    sent_at=now,
                )
                if rolling_count >= warn_threshold:
                    logger.warning(
                        "Rolling 24h downlink count for %s reached %d (budget=%d)",
                        action.ttn_device_id,
                        rolling_count,
                        warn_threshold,
                    )

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                await self.scheduler_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in scheduler_loop iteration")
            await asyncio.sleep(self.settings.scheduler_poll_sec)

    async def scheduler_once(self, now_local: datetime | None = None) -> bool:
        target_now = now_local or datetime.now(self._tz)
        enqueued = await self.db.run_schedule_tick(target_now)
        if enqueued:
            self.dispatch_event.set()
        return enqueued
