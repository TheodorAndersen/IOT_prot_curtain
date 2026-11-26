"""PostgreSQL persistence layer using asyncpg and plain parameterized SQL."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg

from config import Settings
from models import (
    CommandKind,
    CommandStatus,
    DecodedStateUplink,
)
from protocol import ProtocolCodec


class DatabaseConflictError(Exception):
    """Raised when a DB state guard rejects an operation (HTTP 409)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


MappingLike = Any


def _to_utc(dt: datetime | None = None) -> datetime:
    """Normalize a datetime (or current time) to timezone-aware UTC."""
    val = dt if dt is not None else datetime.now(timezone.utc)
    if val.tzinfo is None:
        return val.replace(tzinfo=timezone.utc)
    return val.astimezone(timezone.utc)


def _hhmm_to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":", 1)
    return int(hours) * 60 + int(minutes)


def compute_day_hint(
    now_local: datetime,
    auto_open: int,
    auto_open_time: str,
    auto_close: int,
    auto_close_time: str,
) -> bool | None:
    """Compute day/night state implied by the clock for time-based sides.

    Returns None when both sides are sun-driven or when only close-by-time is set
    before the close time (device decides; backend must not overwrite day/night).
    """
    now_m = now_local.hour * 60 + now_local.minute
    open_m = _hhmm_to_minutes(auto_open_time)
    close_m = _hhmm_to_minutes(auto_close_time)
    time_open = int(auto_open) == 0
    time_close = int(auto_close) == 0

    if time_open and time_close:
        if open_m < close_m:
            return open_m <= now_m < close_m
        if open_m > close_m:
            return now_m >= open_m or now_m < close_m
        return False
    if time_open:
        return now_m >= open_m
    if time_close:
        return False if now_m >= close_m else None
    return None


def compute_is_day(
    now_local: datetime, auto_open_time: str, auto_close_time: str
) -> bool:
    """Compute whether current local time falls inside a time-based open window."""
    return bool(compute_day_hint(now_local, 0, auto_open_time, 0, auto_close_time))


def _auto_settings_args(settings_row: MappingLike) -> dict[str, int]:
    return {
        "auto_open": int(settings_row["auto_open"]),
        "auto_close": int(settings_row["auto_close"]),
        "maintain_light": int(settings_row["maintain_light"]),
        "target_light_level": int(settings_row["target_light_level"]),
        "outside_threshold_open": int(settings_row["outside_threshold_open"]),
        "outside_threshold_close": int(settings_row["outside_threshold_close"]),
    }


def _parse_json_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else None
    if isinstance(value, dict):
        return dict(value)
    return dict(value)


def is_settings_in_sync(
    dev: MappingLike, settings_row: MappingLike, codec: ProtocolCodec
) -> bool:
    """Return True when last confirmed Type 3 and live uplink flags match device_settings."""
    if dev.get("last_seen_at") is None:
        return False
    confirmed = _parse_json_dict(dev.get("last_confirmed_auto_settings"))
    if confirmed is None:
        return False
    expected = _auto_settings_args(settings_row)
    if any(confirmed.get(k) != v for k, v in expected.items()):
        return False
    flags = int(dev.get("device_flags") or 0)
    fb = codec.spec.state_uplink.flag_bits
    return (
        bool(flags & (1 << fb["open_by_sun"])) == bool(expected["auto_open"])
        and bool(flags & (1 << fb["close_by_sun"])) == bool(expected["auto_close"])
        and bool(flags & (1 << fb["keep_indoor_light"])) == bool(expected["maintain_light"])
    )


def compute_sync_commands(
    dev: MappingLike,
    settings_row: MappingLike,
    day_hint: bool | None,
    codec: ProtocolCodec,
    *,
    time_changed: bool = False,
) -> list[tuple[CommandKind, dict[str, Any]]]:
    """Compute minimal v4 (or v3) downlink commands needed to reconcile device state."""
    desired_mode = str(dev["desired_mode"])
    reported_mode = str(dev["mode"])
    mode_differs = desired_mode != reported_mode

    # While manual, auto settings and day/night have no effect and Type 3
    # reconciles them upon entering automatic mode (so cold boot starts at 0 DLs).
    if desired_mode == "manual":
        if mode_differs:
            return [(CommandKind.SET_MODE, {"mode": "manual"})]
        return []

    auto_differs = not is_settings_in_sync(dev, settings_row, codec)
    day_differs = (
        day_hint is not None
        and (desired_mode == "automatic" or time_changed)
        and (dev.get("last_seen_at") is None or bool(dev["is_day"]) != day_hint)
    )

    if codec.spec.consolidated_config_downlink:
        if auto_differs or (mode_differs and day_differs):
            args: dict[str, Any] = dict(_auto_settings_args(settings_row))
            if mode_differs:
                args["set_mode"] = 1
                args["automatic"] = 1 if desired_mode == "automatic" else 0
            if day_differs and day_hint is not None:
                args["set_day"] = 1
                args["is_day"] = 1 if day_hint else 0
            return [(CommandKind.SET_AUTO_SETTINGS, args)]
        if mode_differs:
            return [(CommandKind.SET_MODE, {"mode": desired_mode})]
        if day_differs and day_hint is not None:
            return [(CommandKind.SET_SCHEDULE, {"action": "open" if day_hint else "close"})]
        return []

    cmds: list[tuple[CommandKind, dict[str, Any]]] = []
    if auto_differs:
        cmds.append((CommandKind.SET_AUTO_SETTINGS, _auto_settings_args(settings_row)))
    if day_differs and day_hint is not None:
        cmds.append((CommandKind.SET_SCHEDULE, {"action": "open" if day_hint else "close"}))
    if mode_differs:
        cmds.append((CommandKind.SET_MODE, {"mode": desired_mode}))
    return cmds


@dataclass(frozen=True)
class DispatchAction:
    """Single-publisher instruction emitted by Database.prepare_dispatch_action."""

    ttn_device_id: str
    clear_downlinks: bool = False
    f_port: int | None = None
    payload: bytes | None = None
    confirmed: bool = False
    command_id: int | None = None


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash VARCHAR(64) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    ttn_device_id VARCHAR(255) NOT NULL UNIQUE,
    dev_eui CHAR(16) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    position SMALLINT NULL CHECK (position IS NULL OR (position >= 0 AND position <= 100)),
    target_position SMALLINT NULL CHECK (target_position IS NULL OR (target_position >= 0 AND target_position <= 100)),
    mode VARCHAR(16) NOT NULL DEFAULT 'automatic' CHECK (mode IN ('manual', 'automatic')),
    desired_mode VARCHAR(16) NOT NULL DEFAULT 'automatic' CHECK (desired_mode IN ('manual', 'automatic')),
    is_moving BOOLEAN NOT NULL DEFAULT FALSE,
    is_calibrated BOOLEAN NOT NULL DEFAULT FALSE,
    is_day BOOLEAN NOT NULL DEFAULT FALSE,
    device_flags SMALLINT NOT NULL DEFAULT 0,
    last_reason VARCHAR(32) NULL,
    last_cmd_id_ack SMALLINT NOT NULL DEFAULT 0,
    next_cmd_id SMALLINT NOT NULL DEFAULT 1,
    last_confirmed_auto_settings JSONB NULL,
    sync_pending BOOLEAN NOT NULL DEFAULT FALSE,
    sync_time_changed BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen_at TIMESTAMPTZ NULL
);

ALTER TABLE devices ADD COLUMN IF NOT EXISTS last_confirmed_auto_settings JSONB NULL;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS sync_pending BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS sync_time_changed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE devices ALTER COLUMN mode SET DEFAULT 'automatic';
ALTER TABLE devices ALTER COLUMN desired_mode SET DEFAULT 'automatic';

CREATE TABLE IF NOT EXISTS device_settings (
    device_id UUID PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    auto_open SMALLINT NOT NULL DEFAULT 0 CHECK (auto_open IN (0, 1)),
    auto_open_time VARCHAR(5) NOT NULL DEFAULT '07:30' CHECK (auto_open_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    auto_close SMALLINT NOT NULL DEFAULT 1 CHECK (auto_close IN (0, 1)),
    auto_close_time VARCHAR(5) NOT NULL DEFAULT '21:00' CHECK (auto_close_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    maintain_light SMALLINT NOT NULL DEFAULT 0 CHECK (maintain_light IN (0, 1)),
    target_light_level SMALLINT NOT NULL DEFAULT 60 CHECK (target_light_level >= 0 AND target_light_level <= 100),
    outside_threshold_open SMALLINT NOT NULL DEFAULT 80 CHECK (outside_threshold_open >= 0 AND outside_threshold_open <= 100),
    outside_threshold_close SMALLINT NOT NULL DEFAULT 10 CHECK (outside_threshold_close >= 0 AND outside_threshold_close <= 100),
    last_open_sched_date DATE NULL,
    last_close_sched_date DATE NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (outside_threshold_close < outside_threshold_open)
);

CREATE TABLE IF NOT EXISTS device_commands (
    id BIGSERIAL PRIMARY KEY,
    device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    command_kind VARCHAR(32) NOT NULL,
    command_args JSONB NOT NULL,
    cmd_id SMALLINT NULL,
    wire_fport SMALLINT NULL,
    wire_payload_hex VARCHAR(64) NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('QUEUED', 'IN_FLIGHT', 'CONFIRMED', 'REJECTED', 'NOT_DELIVERED', 'SUPERSEDED')),
    attempts SMALLINT NOT NULL DEFAULT 0,
    sent_at TIMESTAMPTZ NULL,
    resolved_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_one_inflight_per_device
    ON device_commands (device_id) WHERE status = 'IN_FLIGHT';

CREATE INDEX IF NOT EXISTS idx_device_commands_device_status_id
    ON device_commands (device_id, status, id);

CREATE TABLE IF NOT EXISTS device_events (
    id BIGSERIAL PRIMARY KEY,
    device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    received_at TIMESTAMPTZ NOT NULL,
    last_cmd_id SMALLINT NOT NULL,
    reason VARCHAR(32) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    flags SMALLINT NOT NULL,
    position SMALLINT NULL,
    raw_fport SMALLINT NOT NULL,
    raw_payload_hex VARCHAR(128) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_device_events_device_received
    ON device_events (device_id, received_at DESC);

CREATE TABLE IF NOT EXISTS light_telemetry (
    id BIGSERIAL PRIMARY KEY,
    device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    recorded_at TIMESTAMPTZ NOT NULL,
    light_level SMALLINT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_light_telemetry_device_recorded
    ON light_telemetry (device_id, recorded_at);

CREATE INDEX IF NOT EXISTS idx_light_telemetry_device_recorded
    ON light_telemetry (device_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS downlink_transmissions (
    id BIGSERIAL PRIMARY KEY,
    device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    command_id BIGINT NULL REFERENCES device_commands(id) ON DELETE SET NULL,
    f_port SMALLINT NOT NULL,
    confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    sent_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_downlink_transmissions_device_sent
    ON downlink_transmissions (device_id, sent_at DESC);
"""


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


def _is_mode_changing_command(command_kind: str, command_args: MappingLike) -> bool:
    if command_kind in (CommandKind.SET_MODE.value, CommandKind.SET_POSITION.value):
        return True
    if command_kind == CommandKind.SET_AUTO_SETTINGS.value:
        args = _parse_json_dict(command_args) or {}
        return int(args.get("set_mode") or 0) == 1
    return False


class Database:
    """Plain SQL PostgreSQL datastore backed by an asyncpg pool."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database pool is not initialized")
        return self._pool

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self._database_url,
                min_size=1,
                max_size=10,
                init=_init_connection,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def init_schema(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(_SCHEMA_DDL)

    async def seed_defaults(self, settings: Settings, codec: ProtocolCodec) -> None:
        password_hash = hashlib.sha256(
            settings.demo_user_password_md5.lower().encode("utf-8")
        ).hexdigest()
        user_uuid = uuid.uuid4()
        device_uuid = uuid.uuid4()
        initial_cmd_id = codec.spec.cmd_id.min

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row_user = await conn.fetchrow(
                    """
                    INSERT INTO users (id, email, password_hash)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (email) DO UPDATE
                        SET password_hash = EXCLUDED.password_hash
                    RETURNING id
                    """,
                    user_uuid,
                    settings.demo_user_email,
                    password_hash,
                )
                actual_user_id = row_user["id"]

                row_dev = await conn.fetchrow(
                    """
                    INSERT INTO devices (
                        id, user_id, ttn_device_id, dev_eui, name, next_cmd_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (user_id) DO UPDATE
                        SET ttn_device_id = EXCLUDED.ttn_device_id,
                            dev_eui = EXCLUDED.dev_eui,
                            name = EXCLUDED.name
                    RETURNING id
                    """,
                    device_uuid,
                    actual_user_id,
                    settings.ttn_devid,
                    settings.device_dev_eui,
                    settings.device_name,
                    initial_cmd_id,
                )
                actual_device_id = row_dev["id"]

                await conn.execute(
                    """
                    INSERT INTO device_settings (device_id)
                    VALUES ($1)
                    ON CONFLICT (device_id) DO NOTHING
                    """,
                    actual_device_id,
                )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT id, email, password_hash FROM users WHERE email = $1",
            email,
        )
        return dict(row) if row is not None else None

    async def create_auth_token(
        self, user_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM auth_tokens WHERE expires_at < NOW()")
                await conn.execute(
                    """
                    INSERT INTO auth_tokens (token_hash, user_id, expires_at)
                    VALUES ($1, $2, $3)
                    """,
                    token_hash,
                    user_id,
                    expires_at,
                )

    async def get_user_id_by_token_hash(self, token_hash: str) -> uuid.UUID | None:
        row = await self.pool.fetchrow(
            """
            SELECT user_id
            FROM auth_tokens
            WHERE token_hash = $1 AND expires_at > NOW()
            """,
            token_hash,
        )
        return row["user_id"] if row is not None else None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_device_and_settings_by_user(
        self, user_id: uuid.UUID, now_utc: datetime | None = None
    ) -> dict[str, Any] | None:
        window_start = _to_utc(now_utc) - timedelta(hours=24)
        row = await self.pool.fetchrow(
            """
            SELECT
                d.id AS device_id,
                d.ttn_device_id,
                d.position,
                d.target_position,
                d.mode,
                d.desired_mode,
                d.is_moving,
                d.is_calibrated,
                d.is_day,
                d.device_flags,
                d.last_seen_at,
                d.sync_pending,
                EXISTS (
                    SELECT 1 FROM device_commands c
                    WHERE c.device_id = d.id
                      AND c.command_kind = 'calibrate'
                      AND c.status IN ('QUEUED', 'IN_FLIGHT')
                ) AS calibration_pending,
                s.auto_open,
                s.auto_open_time,
                s.auto_close,
                s.auto_close_time,
                s.maintain_light,
                s.target_light_level,
                s.outside_threshold_open,
                s.outside_threshold_close,
                (
                    (d.sync_pending AND d.desired_mode <> d.mode)
                    OR EXISTS (
                        SELECT 1
                        FROM device_commands c
                        WHERE c.device_id = d.id
                          AND (
                              c.command_kind IN ('set_mode', 'set_position')
                              OR (
                                  c.command_kind = 'set_auto_settings'
                                  AND COALESCE((c.command_args->>'set_mode')::int, 0) = 1
                              )
                          )
                          AND c.status IN ('QUEUED', 'IN_FLIGHT')
                    )
                ) AS has_pending_set_mode,
                COALESCE(dl.downlinks_used_24h, 0) AS downlinks_used_24h,
                dl.oldest_downlink_sent_at
            FROM devices d
            JOIN device_settings s ON s.device_id = d.id
            LEFT JOIN LATERAL (
                SELECT
                    COUNT(*)::int AS downlinks_used_24h,
                    MIN(t.sent_at) AS oldest_downlink_sent_at
                FROM downlink_transmissions t
                WHERE t.device_id = d.id
                  AND t.sent_at > $2
            ) dl ON TRUE
            WHERE d.user_id = $1
            """,
            user_id,
            window_start,
        )
        return dict(row) if row is not None else None

    async def get_debug_snapshot(
        self, user_id: uuid.UUID, limit: int, now_utc: datetime | None = None
    ) -> dict[str, Any] | None:
        window_start = _to_utc(now_utc) - timedelta(hours=24)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    d.id AS device_id,
                    d.ttn_device_id,
                    d.dev_eui,
                    d.name,
                    d.position,
                    d.target_position,
                    d.mode,
                    d.desired_mode,
                    d.is_moving,
                    d.is_calibrated,
                    d.is_day,
                    d.device_flags,
                    d.last_reason,
                    d.last_cmd_id_ack,
                    d.next_cmd_id,
                    d.last_confirmed_auto_settings,
                    d.sync_pending,
                    d.last_seen_at,
                    s.auto_open,
                    s.auto_open_time,
                    s.auto_close,
                    s.auto_close_time,
                    s.maintain_light,
                    s.target_light_level,
                    s.outside_threshold_open,
                    s.outside_threshold_close,
                    (
                        (d.sync_pending AND d.desired_mode <> d.mode)
                        OR EXISTS (
                            SELECT 1
                            FROM device_commands c
                            WHERE c.device_id = d.id
                              AND (
                                  c.command_kind IN ('set_mode', 'set_position')
                                  OR (
                                      c.command_kind = 'set_auto_settings'
                                      AND COALESCE((c.command_args->>'set_mode')::int, 0) = 1
                                  )
                              )
                              AND c.status IN ('QUEUED', 'IN_FLIGHT')
                        )
                    ) AS has_pending_set_mode,
                    COALESCE(dl.downlinks_used_24h, 0) AS downlinks_used_24h,
                    dl.oldest_downlink_sent_at
                FROM devices d
                JOIN device_settings s ON s.device_id = d.id
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*)::int AS downlinks_used_24h,
                        MIN(t.sent_at) AS oldest_downlink_sent_at
                    FROM downlink_transmissions t
                    WHERE t.device_id = d.id
                      AND t.sent_at > $2
                ) dl ON TRUE
                WHERE d.user_id = $1
                """,
                user_id,
                window_start,
            )
            if row is None:
                return None

            device_id = row["device_id"]
            in_flight = await conn.fetchrow(
                """
                SELECT * FROM device_commands
                WHERE device_id = $1 AND status = 'IN_FLIGHT'
                LIMIT 1
                """,
                device_id,
            )
            queued = await conn.fetch(
                """
                SELECT * FROM device_commands
                WHERE device_id = $1 AND status = 'QUEUED'
                ORDER BY id ASC
                """,
                device_id,
            )
            last_sent = await conn.fetchrow(
                """
                SELECT * FROM device_commands
                WHERE device_id = $1 AND sent_at IS NOT NULL
                ORDER BY sent_at DESC, id DESC
                LIMIT 1
                """,
                device_id,
            )
            recent_commands = await conn.fetch(
                """
                SELECT * FROM device_commands
                WHERE device_id = $1
                ORDER BY id DESC
                LIMIT $2
                """,
                device_id,
                limit,
            )
            recent_uplinks = await conn.fetch(
                """
                SELECT * FROM device_events
                WHERE device_id = $1
                ORDER BY received_at DESC, id DESC
                LIMIT $2
                """,
                device_id,
                limit,
            )
            return {
                "device": dict(row),
                "in_flight": dict(in_flight) if in_flight is not None else None,
                "queued": [dict(r) for r in queued],
                "last_sent": dict(last_sent) if last_sent is not None else None,
                "recent_commands": [dict(r) for r in recent_commands],
                "recent_uplinks": [dict(r) for r in recent_uplinks],
            }

    async def list_devices(self) -> list[dict[str, Any]]:
        rows = await self.pool.fetch("SELECT id, ttn_device_id FROM devices ORDER BY id ASC")
        return [dict(r) for r in rows]

    async def get_telemetry(
        self, user_id: uuid.UUID, hours: int
    ) -> tuple[str, list[dict[str, Any]]] | None:
        async with self.pool.acquire() as conn:
            dev = await conn.fetchrow(
                "SELECT id, ttn_device_id FROM devices WHERE user_id = $1",
                user_id,
            )
            if dev is None:
                return None
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
            rows = await conn.fetch(
                """
                SELECT recorded_at, light_level
                FROM light_telemetry
                WHERE device_id = $1 AND recorded_at >= $2
                ORDER BY recorded_at ASC
                """,
                dev["id"],
                since,
            )
            return str(dev["ttn_device_id"]), [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # REST Mutations -> Semantic Commands / Just-in-Time Sync
    # ------------------------------------------------------------------

    async def enqueue_position(
        self, user_id: uuid.UUID, position: int, codec: ProtocolCodec
    ) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                dev = await conn.fetchrow(
                    "SELECT * FROM devices WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
                if dev is None:
                    raise DatabaseConflictError("device not found")
                if not dev["is_calibrated"]:
                    raise DatabaseConflictError("curtain is not calibrated")

                settings_row = await conn.fetchrow(
                    "SELECT * FROM device_settings WHERE device_id = $1",
                    dev["id"],
                )
                assert settings_row is not None
                keep_sync = bool(dev["sync_pending"]) and not is_settings_in_sync(
                    dev, settings_row, codec
                )

                await conn.execute(
                    """
                    UPDATE devices
                    SET desired_mode = 'manual',
                        target_position = $2,
                        sync_pending = $3,
                        sync_time_changed = FALSE
                    WHERE id = $1
                    """,
                    dev["id"],
                    position,
                    keep_sync,
                )
                await conn.execute(
                    """
                    UPDATE device_commands
                    SET status = $2, resolved_at = NOW()
                    WHERE device_id = $1
                      AND (
                          command_kind IN ('set_position', 'set_mode', 'set_schedule')
                          OR (
                              command_kind = 'set_auto_settings'
                              AND COALESCE((command_args->>'set_mode')::int, 0) = 1
                          )
                      )
                      AND status = $3
                    """,
                    dev["id"],
                    CommandStatus.SUPERSEDED.value,
                    CommandStatus.QUEUED.value,
                )
                await conn.execute(
                    """
                    INSERT INTO device_commands (device_id, command_kind, command_args, status)
                    VALUES ($1, $2, $3, $4)
                    """,
                    dev["id"],
                    CommandKind.SET_POSITION.value,
                    {"position": position},
                    CommandStatus.QUEUED.value,
                )

    async def enqueue_calibrate(self, user_id: uuid.UUID) -> None:
        """Queue a v4.1 'calibrate' command; queued moves are dropped (they would be rejected)."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                dev = await conn.fetchrow(
                    "SELECT * FROM devices WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
                if dev is None:
                    raise DatabaseConflictError("device not found")
                await conn.execute(
                    """
                    UPDATE device_commands
                    SET status = $2, resolved_at = NOW()
                    WHERE device_id = $1
                      AND command_kind IN ('set_position', 'calibrate')
                      AND status = $3
                    """,
                    dev["id"],
                    CommandStatus.SUPERSEDED.value,
                    CommandStatus.QUEUED.value,
                )
                await conn.execute(
                    "UPDATE devices SET target_position = NULL WHERE id = $1",
                    dev["id"],
                )
                await conn.execute(
                    """
                    INSERT INTO device_commands (device_id, command_kind, command_args, status)
                    VALUES ($1, $2, $3, $4)
                    """,
                    dev["id"],
                    CommandKind.CALIBRATE.value,
                    {"action": "start"},
                    CommandStatus.QUEUED.value,
                )

    async def enqueue_mode(self, user_id: uuid.UUID, mode: str) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                dev = await conn.fetchrow(
                    "SELECT * FROM devices WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
                if dev is None:
                    raise DatabaseConflictError("device not found")

                if (
                    mode == "manual"
                    and str(dev["mode"]) == "manual"
                    and str(dev["desired_mode"]) == "manual"
                    and not bool(dev["sync_pending"])
                ):
                    has_pending_mode = await conn.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM device_commands
                            WHERE device_id = $1
                              AND (
                                  command_kind IN ('set_mode', 'set_position')
                                  OR (
                                      command_kind = 'set_auto_settings'
                                      AND COALESCE((command_args->>'set_mode')::int, 0) = 1
                                  )
                              )
                              AND status IN ('QUEUED', 'IN_FLIGHT')
                        )
                        """,
                        dev["id"],
                    )
                    if not has_pending_mode:
                        return False

                if mode == "automatic":
                    await conn.execute(
                        """
                        UPDATE devices
                        SET desired_mode = $2,
                            target_position = NULL,
                            sync_pending = TRUE
                        WHERE id = $1
                        """,
                        dev["id"],
                        mode,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE devices
                        SET desired_mode = $2,
                            sync_pending = TRUE
                        WHERE id = $1
                        """,
                        dev["id"],
                        mode,
                    )

                # Clear stale queued mode/schedule commands so plan_sync decides just-in-time
                await conn.execute(
                    """
                    UPDATE device_commands
                    SET status = $2, resolved_at = NOW()
                    WHERE device_id = $1
                      AND (
                          command_kind IN ('set_mode', 'set_schedule')
                          OR (
                              command_kind = 'set_auto_settings'
                              AND COALESCE((command_args->>'set_mode')::int, 0) = 1
                          )
                      )
                      AND status = 'QUEUED'
                    """,
                    dev["id"],
                    CommandStatus.SUPERSEDED.value,
                )
                return True

    async def enqueue_sync(self, user_id: uuid.UUID) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                dev = await conn.fetchrow(
                    "SELECT * FROM devices WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
                if dev is None:
                    raise DatabaseConflictError("device not found")

                has_pending_mode = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM device_commands
                        WHERE device_id = $1
                          AND (
                              command_kind IN ('set_mode', 'set_position')
                              OR (
                                  command_kind = 'set_auto_settings'
                                  AND COALESCE((command_args->>'set_mode')::int, 0) = 1
                              )
                          )
                          AND status IN ('QUEUED', 'IN_FLIGHT')
                    )
                    """,
                    dev["id"],
                )
                effective_mode = (
                    str(dev["desired_mode"])
                    if (has_pending_mode or dev["sync_pending"])
                    else str(dev["mode"])
                )

                await conn.execute(
                    """
                    UPDATE device_commands
                    SET status = $3, resolved_at = NOW()
                    WHERE device_id = $1
                      AND command_kind = $2
                      AND status = $4
                    """,
                    dev["id"],
                    CommandKind.SET_MODE.value,
                    CommandStatus.SUPERSEDED.value,
                    CommandStatus.QUEUED.value,
                )
                await conn.execute(
                    """
                    INSERT INTO device_commands (device_id, command_kind, command_args, status)
                    VALUES ($1, $2, $3, $4)
                    """,
                    dev["id"],
                    CommandKind.SET_MODE.value,
                    {"mode": effective_mode},
                    CommandStatus.QUEUED.value,
                )

    async def enqueue_startup_sync_for_unseen_devices(self) -> bool:
        enqueued_any = False
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id, desired_mode
                    FROM devices
                    WHERE last_seen_at IS NULL
                    FOR UPDATE
                    """
                )
                for row in rows:
                    has_pending_mode = await conn.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM device_commands
                            WHERE device_id = $1
                              AND (
                                  command_kind = 'set_mode'
                                  OR (
                                      command_kind = 'set_auto_settings'
                                      AND COALESCE((command_args->>'set_mode')::int, 0) = 1
                                  )
                              )
                              AND status IN ('QUEUED', 'IN_FLIGHT')
                        )
                        """,
                        row["id"],
                    )
                    if has_pending_mode:
                        continue
                    await conn.execute(
                        """
                        INSERT INTO device_commands (device_id, command_kind, command_args, status)
                        VALUES ($1, $2, $3, $4)
                        """,
                        row["id"],
                        CommandKind.SET_MODE.value,
                        {"mode": str(row["desired_mode"])},
                        CommandStatus.QUEUED.value,
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
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                dev = await conn.fetchrow(
                    "SELECT id FROM devices WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
                if dev is None:
                    raise DatabaseConflictError("device not found")
                existing = await conn.fetchrow(
                    "SELECT * FROM device_settings WHERE device_id = $1",
                    dev["id"],
                )
                assert existing is not None

                open_time_changed = auto_open_time != existing["auto_open_time"]
                close_time_changed = auto_close_time != existing["auto_close_time"]
                last_open = None if open_time_changed else existing["last_open_sched_date"]
                last_close = None if close_time_changed else existing["last_close_sched_date"]

                await conn.execute(
                    """
                    UPDATE device_settings
                    SET auto_open = $2,
                        auto_open_time = $3,
                        auto_close = $4,
                        auto_close_time = $5,
                        maintain_light = $6,
                        target_light_level = $7,
                        last_open_sched_date = $8,
                        last_close_sched_date = $9,
                        updated_at = NOW()
                    WHERE device_id = $1
                    """,
                    dev["id"],
                    auto_open,
                    auto_open_time,
                    auto_close,
                    auto_close_time,
                    maintain_light,
                    target_light_level,
                    last_open,
                    last_close,
                )
                await conn.execute(
                    """
                    UPDATE devices
                    SET sync_pending = TRUE,
                        sync_time_changed = sync_time_changed OR $2
                    WHERE id = $1
                    """,
                    dev["id"],
                    open_time_changed or close_time_changed,
                )
                await conn.execute(
                    """
                    UPDATE device_commands
                    SET status = $2, resolved_at = NOW()
                    WHERE device_id = $1
                      AND command_kind = 'set_auto_settings'
                      AND status = 'QUEUED'
                    """,
                    dev["id"],
                    CommandStatus.SUPERSEDED.value,
                )
                return True

    async def update_thresholds(
        self,
        user_id: uuid.UUID,
        outside_threshold_open: int,
        outside_threshold_close: int,
    ) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                dev = await conn.fetchrow(
                    "SELECT id FROM devices WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
                if dev is None:
                    raise DatabaseConflictError("device not found")
                await conn.execute(
                    """
                    UPDATE device_settings
                    SET outside_threshold_open = $2,
                        outside_threshold_close = $3,
                        updated_at = NOW()
                    WHERE device_id = $1
                    """,
                    dev["id"],
                    outside_threshold_open,
                    outside_threshold_close,
                )
                await conn.execute(
                    "UPDATE devices SET sync_pending = TRUE WHERE id = $1",
                    dev["id"],
                )
                await conn.execute(
                    """
                    UPDATE device_commands
                    SET status = $2, resolved_at = NOW()
                    WHERE device_id = $1
                      AND command_kind = 'set_auto_settings'
                      AND status = 'QUEUED'
                    """,
                    dev["id"],
                    CommandStatus.SUPERSEDED.value,
                )
                return True

    # ------------------------------------------------------------------
    # Uplink Reconciliation
    # ------------------------------------------------------------------

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
        """Persist state uplink and reconcile commands in one transaction.

        Returns (device_found, should_wake_dispatcher).
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                dev = await conn.fetchrow(
                    "SELECT * FROM devices WHERE ttn_device_id = $1 FOR UPDATE",
                    ttn_device_id,
                )
                if dev is None:
                    return False, False

                device_id = dev["id"]
                await conn.execute(
                    """
                    INSERT INTO device_events (
                        device_id, received_at, last_cmd_id, reason, mode,
                        flags, position, raw_fport, raw_payload_hex
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    device_id,
                    received_at,
                    decoded.last_cmd_id,
                    decoded.reason,
                    decoded.mode,
                    decoded.raw_flags,
                    decoded.position,
                    raw_fport,
                    raw_payload_hex,
                )

                new_target = (
                    None
                    if (not decoded.is_moving or decoded.mode == "automatic")
                    else dev["target_position"]
                )
                await conn.execute(
                    """
                    UPDATE devices
                    SET last_seen_at = $2,
                        last_reason = $3,
                        last_cmd_id_ack = $4,
                        device_flags = $5,
                        mode = $6,
                        is_moving = $7,
                        is_calibrated = $8,
                        is_day = $9,
                        position = $10,
                        target_position = $11
                    WHERE id = $1
                    """,
                    device_id,
                    received_at,
                    decoded.reason,
                    decoded.last_cmd_id,
                    decoded.raw_flags,
                    decoded.mode,
                    decoded.is_moving,
                    decoded.is_calibrated,
                    decoded.is_day,
                    decoded.position,
                    new_target,
                )

                should_wake = False

                if decoded.reason == "startup":
                    await conn.execute(
                        """
                        UPDATE device_commands
                        SET status = $2, resolved_at = $3
                        WHERE device_id = $1 AND status = $4
                        """,
                        device_id,
                        CommandStatus.SUPERSEDED.value,
                        received_at,
                        CommandStatus.IN_FLIGHT.value,
                    )
                    await conn.execute(
                        """
                        UPDATE device_commands
                        SET status = $2, resolved_at = $3
                        WHERE device_id = $1
                          AND command_kind IN ('set_auto_settings', 'set_schedule', 'set_mode')
                          AND status = $4
                        """,
                        device_id,
                        CommandStatus.SUPERSEDED.value,
                        received_at,
                        CommandStatus.QUEUED.value,
                    )
                    # Per PROTOCOL_FINAL.md: backend's settings win; device's reported mode is truth
                    await conn.execute(
                        """
                        UPDATE devices
                        SET desired_mode = $2,
                            sync_pending = TRUE,
                            sync_time_changed = FALSE
                        WHERE id = $1
                        """,
                        device_id,
                        decoded.mode,
                    )
                    should_wake = True
                else:
                    inflight = await conn.fetchrow(
                        """
                        SELECT id, cmd_id, command_kind, command_args
                        FROM device_commands
                        WHERE device_id = $1 AND status = $2
                        """,
                        device_id,
                        CommandStatus.IN_FLIGHT.value,
                    )
                    if inflight is not None and inflight["cmd_id"] == decoded.last_cmd_id:
                        resolved_status = (
                            CommandStatus.REJECTED.value
                            if decoded.reason == "rejected"
                            else CommandStatus.CONFIRMED.value
                        )
                        await conn.execute(
                            """
                            UPDATE device_commands
                            SET status = $2, resolved_at = $3
                            WHERE id = $1
                            """,
                            inflight["id"],
                            resolved_status,
                            received_at,
                        )
                        if (
                            resolved_status == CommandStatus.CONFIRMED.value
                            and inflight["command_kind"] == CommandKind.SET_AUTO_SETTINGS.value
                        ):
                            args = _parse_json_dict(inflight["command_args"]) or {}
                            confirmed_snapshot = {
                                "auto_open": int(args["auto_open"]),
                                "auto_close": int(args["auto_close"]),
                                "maintain_light": int(args["maintain_light"]),
                                "target_light_level": int(args["target_light_level"]),
                                "outside_threshold_open": int(args["outside_threshold_open"]),
                                "outside_threshold_close": int(args["outside_threshold_close"]),
                            }
                            await conn.execute(
                                "UPDATE devices SET last_confirmed_auto_settings = $2 WHERE id = $1",
                                device_id,
                                confirmed_snapshot,
                            )
                        elif (
                            resolved_status == CommandStatus.REJECTED.value
                            and inflight["command_kind"] == CommandKind.SET_POSITION.value
                        ):
                            await conn.execute(
                                "UPDATE devices SET target_position = NULL WHERE id = $1",
                                device_id,
                            )
                        should_wake = True

                    if first_uplink_since_boot:
                        await conn.execute(
                            "UPDATE devices SET sync_pending = TRUE WHERE id = $1",
                            device_id,
                        )
                        should_wake = True

                    has_pending_mode = await conn.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM device_commands
                            WHERE device_id = $1
                              AND (
                                  command_kind IN ('set_mode', 'set_position')
                                  OR (
                                      command_kind = 'set_auto_settings'
                                      AND COALESCE((command_args->>'set_mode')::int, 0) = 1
                                  )
                              )
                              AND status IN ('QUEUED', 'IN_FLIGHT')
                        )
                        """,
                        device_id,
                    )
                    if not has_pending_mode and not bool(dev["sync_pending"]):
                        await conn.execute(
                            "UPDATE devices SET desired_mode = $2 WHERE id = $1",
                            device_id,
                            decoded.mode,
                        )

                # EEPROM lastId continuity: avoid duplicate-ID collision after fresh DB / restart
                has_active_cmds = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM device_commands
                        WHERE device_id = $1 AND status IN ('QUEUED', 'IN_FLIGHT')
                    )
                    """,
                    device_id,
                )
                if decoded.last_cmd_id > 0 and not has_active_cmds:
                    await conn.execute(
                        "UPDATE devices SET next_cmd_id = $2 WHERE id = $1",
                        device_id,
                        codec.next_cmd_id(decoded.last_cmd_id),
                    )

                return True, should_wake

    async def insert_light_telemetry(
        self,
        ttn_device_id: str,
        received_at: datetime,
        samples: list[tuple[datetime, int]],
    ) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                dev = await conn.fetchrow(
                    "SELECT id FROM devices WHERE ttn_device_id = $1 FOR UPDATE",
                    ttn_device_id,
                )
                if dev is None:
                    return False

                device_id = dev["id"]
                await conn.executemany(
                    """
                    INSERT INTO light_telemetry (device_id, recorded_at, light_level)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (device_id, recorded_at) DO NOTHING
                    """,
                    [(device_id, recorded_at, level) for recorded_at, level in samples],
                )
                await conn.execute(
                    "UPDATE devices SET last_seen_at = $2 WHERE id = $1",
                    device_id,
                    received_at,
                )
                return True

    # ------------------------------------------------------------------
    # Dispatcher & Scheduler Loops
    # ------------------------------------------------------------------

    async def prepare_dispatch_action(
        self,
        device_id: uuid.UUID,
        now_utc: datetime,
        now_local: datetime,
        codec: ProtocolCodec,
    ) -> DispatchAction | None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                dev = await conn.fetchrow(
                    "SELECT * FROM devices WHERE id = $1 FOR UPDATE",
                    device_id,
                )
                if dev is None:
                    return None

                ttn_dev_id = str(dev["ttn_device_id"])
                need_clear = False
                inflight = await conn.fetchrow(
                    """
                    SELECT * FROM device_commands
                    WHERE device_id = $1 AND status = $2
                    """,
                    device_id,
                    CommandStatus.IN_FLIGHT.value,
                )
                if inflight is not None:
                    sent_at: datetime = inflight["sent_at"]
                    elapsed = (now_utc - sent_at).total_seconds()
                    if elapsed < codec.spec.handshake.timeout_sec:
                        return None

                    attempts = int(inflight["attempts"])
                    if attempts < codec.spec.handshake.max_attempts:
                        new_attempts = attempts + 1
                        await conn.execute(
                            """
                            UPDATE device_commands
                            SET attempts = $2, sent_at = $3
                            WHERE id = $1
                            """,
                            inflight["id"],
                            new_attempts,
                            now_utc,
                        )
                        return DispatchAction(
                            ttn_device_id=ttn_dev_id,
                            clear_downlinks=False,
                            f_port=int(inflight["wire_fport"]),
                            payload=bytes.fromhex(str(inflight["wire_payload_hex"])),
                            confirmed=codec.spec.confirmed_downlinks,
                            command_id=int(inflight["id"]),
                        )

                    # Exceeded max_attempts -> NOT_DELIVERED
                    await conn.execute(
                        """
                        UPDATE device_commands
                        SET status = $2, resolved_at = $3
                        WHERE id = $1
                        """,
                        inflight["id"],
                        CommandStatus.NOT_DELIVERED.value,
                        now_utc,
                    )
                    if inflight["command_kind"] == CommandKind.SET_POSITION.value:
                        await conn.execute(
                            "UPDATE devices SET target_position = NULL WHERE id = $1",
                            device_id,
                        )
                    if _is_mode_changing_command(
                        str(inflight["command_kind"]), inflight["command_args"]
                    ):
                        has_newer_mode = await conn.fetchval(
                            """
                            SELECT EXISTS (
                                SELECT 1 FROM device_commands
                                WHERE device_id = $1
                                  AND (
                                      command_kind IN ('set_mode', 'set_position')
                                      OR (
                                          command_kind = 'set_auto_settings'
                                          AND COALESCE((command_args->>'set_mode')::int, 0) = 1
                                      )
                                  )
                                  AND status = 'QUEUED'
                            )
                            """,
                            device_id,
                        )
                        if not has_newer_mode and not bool(dev["sync_pending"]):
                            await conn.execute(
                                "UPDATE devices SET desired_mode = mode WHERE id = $1",
                                device_id,
                            )
                    need_clear = True

                # 1. Explicit FIFO queued command (set_position, scheduled set_schedule, POST /sync)
                queued = await conn.fetchrow(
                    """
                    SELECT * FROM device_commands
                    WHERE device_id = $1 AND status = $2
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    device_id,
                    CommandStatus.QUEUED.value,
                )

                # 2. If queue is empty and sync_pending is set, compute minimal plan_sync now
                if queued is None and bool(dev["sync_pending"]):
                    settings_row = await conn.fetchrow(
                        "SELECT * FROM device_settings WHERE device_id = $1",
                        device_id,
                    )
                    assert settings_row is not None
                    hint = compute_day_hint(
                        now_local,
                        int(settings_row["auto_open"]),
                        str(settings_row["auto_open_time"]),
                        int(settings_row["auto_close"]),
                        str(settings_row["auto_close_time"]),
                    )
                    planned = compute_sync_commands(
                        dev,
                        settings_row,
                        hint,
                        codec,
                        time_changed=bool(dev["sync_time_changed"]),
                    )
                    await conn.execute(
                        """
                        UPDATE devices
                        SET sync_pending = FALSE, sync_time_changed = FALSE
                        WHERE id = $1
                        """,
                        device_id,
                    )
                    for p_kind, p_args in planned:
                        await conn.execute(
                            """
                            INSERT INTO device_commands (device_id, command_kind, command_args, status)
                            VALUES ($1, $2, $3, $4)
                            """,
                            device_id,
                            p_kind.value,
                            p_args,
                            CommandStatus.QUEUED.value,
                        )
                    if planned:
                        queued = await conn.fetchrow(
                            """
                            SELECT * FROM device_commands
                            WHERE device_id = $1 AND status = $2
                            ORDER BY id ASC
                            LIMIT 1
                            """,
                            device_id,
                            CommandStatus.QUEUED.value,
                        )

                if queued is None:
                    if need_clear:
                        return DispatchAction(
                            ttn_device_id=ttn_dev_id, clear_downlinks=True
                        )
                    return None

                cmd_id = int(dev["next_cmd_id"])
                if not (codec.spec.cmd_id.min <= cmd_id <= codec.spec.cmd_id.max):
                    cmd_id = codec.spec.cmd_id.min
                next_cmd_id = codec.next_cmd_id(cmd_id)

                dl = codec.encode_command(
                    cmd_id,
                    str(queued["command_kind"]),
                    dict(queued["command_args"]),
                )
                await conn.execute(
                    "UPDATE devices SET next_cmd_id = $2 WHERE id = $1",
                    device_id,
                    next_cmd_id,
                )
                await conn.execute(
                    """
                    UPDATE device_commands
                    SET cmd_id = $2,
                        wire_fport = $3,
                        wire_payload_hex = $4,
                        status = $5,
                        attempts = 1,
                        sent_at = $6
                    WHERE id = $1
                    """,
                    queued["id"],
                    cmd_id,
                    dl.f_port,
                    dl.payload.hex(),
                    CommandStatus.IN_FLIGHT.value,
                    now_utc,
                )
                return DispatchAction(
                    ttn_device_id=ttn_dev_id,
                    clear_downlinks=need_clear,
                    f_port=dl.f_port,
                    payload=dl.payload,
                    confirmed=dl.confirmed,
                    command_id=int(queued["id"]),
                )

    async def record_downlink_transmission(
        self,
        device_id: uuid.UUID,
        command_id: int | None,
        f_port: int,
        confirmed: bool,
        sent_at: datetime,
    ) -> int:
        """Append one dispatched downlink and return the rolling 24h count."""
        sent_at_utc = _to_utc(sent_at)
        window_start = sent_at_utc - timedelta(hours=24)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO downlink_transmissions (
                        device_id, command_id, f_port, confirmed, sent_at
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    device_id,
                    command_id,
                    f_port,
                    confirmed,
                    sent_at_utc,
                )
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*)::int
                    FROM downlink_transmissions
                    WHERE device_id = $1 AND sent_at > $2
                    """,
                    device_id,
                    window_start,
                )
                return int(total or 0)

    async def run_schedule_tick(self, now_local: datetime) -> bool:
        """Check time-based open/close schedules for automatic devices.

        Returns True if one or more set_schedule commands were enqueued.
        """
        hhmm = now_local.strftime("%H:%M")
        today: date = now_local.date()
        enqueued_any = False

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT
                        d.id AS device_id,
                        d.mode,
                        s.auto_open,
                        s.auto_open_time,
                        s.last_open_sched_date,
                        s.auto_close,
                        s.auto_close_time,
                        s.last_close_sched_date
                    FROM devices d
                    JOIN device_settings s ON s.device_id = d.id
                    WHERE d.mode = 'automatic'
                    FOR UPDATE OF d
                    """
                )
                for row in rows:
                    device_id = row["device_id"]
                    if (
                        int(row["auto_open"]) == 0
                        and str(row["auto_open_time"]) == hhmm
                        and row["last_open_sched_date"] != today
                    ):
                        await conn.execute(
                            """
                            INSERT INTO device_commands (device_id, command_kind, command_args, status)
                            VALUES ($1, $2, $3, $4)
                            """,
                            device_id,
                            CommandKind.SET_SCHEDULE.value,
                            {"action": "open"},
                            CommandStatus.QUEUED.value,
                        )
                        await conn.execute(
                            "UPDATE device_settings SET last_open_sched_date = $2 WHERE device_id = $1",
                            device_id,
                            today,
                        )
                        enqueued_any = True

                    if (
                        int(row["auto_close"]) == 0
                        and str(row["auto_close_time"]) == hhmm
                        and row["last_close_sched_date"] != today
                    ):
                        await conn.execute(
                            """
                            INSERT INTO device_commands (device_id, command_kind, command_args, status)
                            VALUES ($1, $2, $3, $4)
                            """,
                            device_id,
                            CommandKind.SET_SCHEDULE.value,
                            {"action": "close"},
                            CommandStatus.QUEUED.value,
                        )
                        await conn.execute(
                            "UPDATE device_settings SET last_close_sched_date = $2 WHERE device_id = $1",
                            device_id,
                            today,
                        )
                        enqueued_any = True

        return enqueued_any
