"""Semantic domain dataclasses, enums, and strict Pydantic v2 REST models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import (
    ConfigDict,
    BaseModel,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class CommandKind(str, Enum):
    """Semantic command types dispatched to the curtain device."""

    SET_POSITION = "set_position"
    SET_MODE = "set_mode"
    SET_AUTO_SETTINGS = "set_auto_settings"
    SET_SCHEDULE = "set_schedule"


class CommandStatus(str, Enum):
    """Lifecycle states of a row in device_commands."""

    QUEUED = "QUEUED"
    IN_FLIGHT = "IN_FLIGHT"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    NOT_DELIVERED = "NOT_DELIVERED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class DecodedStateUplink:
    """Semantic representation of an FPort 1 state uplink."""

    last_cmd_id: int
    reason: str
    mode: Literal["manual", "automatic"]
    is_moving: bool
    is_calibrated: bool
    is_day: bool
    open_by_sun: bool
    close_by_sun: bool
    keep_indoor_light: bool
    raw_flags: int
    position: int | None


@dataclass(frozen=True)
class DecodedLightTelemetry:
    """Semantic representation of an optional FPort 2 outside light telemetry batch."""

    version: int
    status_flags: int
    samples: tuple[int, ...]


@dataclass(frozen=True)
class EncodedDownlink:
    """Encoded binary downlink payload ready for LoRaTransport."""

    f_port: int
    payload: bytes
    confirmed: bool


@dataclass(frozen=True)
class DecodedDownlink:
    """Decoded downlink command (used by MockTransport and protocol tests)."""

    cmd_id: int
    kind: CommandKind
    args: dict[str, Any]


# ---------------------------------------------------------------------------
# Strict REST Request Models (extra="forbid", StrictInt / StrictStr)
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: StrictStr
    password: StrictStr = Field(min_length=1)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not cleaned:
            raise ValueError("username must not be empty")
        return cleaned


class PositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: StrictInt = Field(ge=0, le=100)


class ModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["manual", "automatic"]


class AutoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    autoOpen: StrictInt = Field(ge=0, le=1)
    autoOpenTime: StrictStr = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    autoClose: StrictInt = Field(ge=0, le=1)
    autoCloseTime: StrictStr = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    maintainLight: StrictInt = Field(ge=0, le=1)
    targetLightLevel: StrictInt = Field(ge=0, le=100)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outsideThresholdOpen: StrictInt = Field(ge=0, le=100)
    outsideThresholdClose: StrictInt = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _check_thresholds(self) -> SettingsRequest:
        if self.outsideThresholdClose >= self.outsideThresholdOpen:
            raise ValueError(
                "outsideThresholdClose must be lower than outsideThresholdOpen"
            )
        return self


# ---------------------------------------------------------------------------
# REST Response Models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    ok: bool
    transportConnected: bool
    protocolVersion: str


class OkResponse(BaseModel):
    ok: bool = True


class LoginResponse(BaseModel):
    token: str


class StatusResponse(BaseModel):
    position: int | None
    mode: Literal["manual", "automatic"]
    autoOpen: int
    autoOpenTime: str
    autoClose: int
    autoCloseTime: str
    maintainLight: int
    targetLightLevel: int
    outsideThresholdOpen: int
    outsideThresholdClose: int
    moving: bool
    calibrated: bool
    day: bool
    targetPosition: int | None
    lastSeenAt: str | None
    downlinksUsed24h: int
    downlinkBudget24h: int
    downlinkNextSlotAt: str | None


class DecodedFlagsModel(BaseModel):
    automatic: bool
    moving: bool
    calibrated: bool
    day: bool
    openBySun: bool
    closeBySun: bool
    keepIndoorLight: bool


class DebugSavedSettingsModel(BaseModel):
    autoOpen: int
    autoOpenTime: str
    autoClose: int
    autoCloseTime: str
    maintainLight: int
    targetLightLevel: int
    outsideThresholdOpen: int
    outsideThresholdClose: int


class DebugConfirmedAutoSettingsModel(BaseModel):
    autoOpen: int
    autoClose: int
    maintainLight: int
    targetLightLevel: int
    outsideThresholdOpen: int
    outsideThresholdClose: int


class DebugServerModel(BaseModel):
    serverTime: str
    scheduleTimezone: str
    localTime: str
    transportMode: Literal["ttn_mqtt", "mock"]
    transportConnected: bool
    protocolVersion: str
    confirmedDownlinks: bool
    handshakeTimeoutSec: int
    handshakeMaxAttempts: int


class DebugDeviceModel(BaseModel):
    ttnDeviceId: str
    devEui: str
    name: str
    position: int | None
    targetPosition: int | None
    reportedMode: Literal["manual", "automatic"]
    desiredMode: Literal["manual", "automatic"]
    effectiveMode: Literal["manual", "automatic"]
    moving: bool
    calibrated: bool
    day: bool
    computedDayHint: bool | None
    flagsRaw: int
    flagsHex: str
    decodedFlags: DecodedFlagsModel
    lastReason: str | None
    lastCmdIdAck: int
    nextCmdId: int
    lastSeenAt: str | None
    lastSeenAgoSec: int | None
    settingsInSync: bool
    savedSettings: DebugSavedSettingsModel
    lastConfirmedAutoSettings: DebugConfirmedAutoSettingsModel | None


class DebugBudgetModel(BaseModel):
    downlinksUsed24h: int
    downlinkBudget24h: int
    downlinkNextSlotAt: str | None


class DebugCommandModel(BaseModel):
    id: int
    cmdId: int | None
    kind: CommandKind
    args: dict[str, Any]
    status: CommandStatus
    attempts: int
    wireFport: int | None
    wirePayloadHex: str | None
    description: str
    createdAt: str
    sentAt: str | None
    resolvedAt: str | None
    roundTripMs: int | None


class DebugUplinkModel(BaseModel):
    id: int
    receivedAt: str
    ageSec: int
    lastCmdId: int
    reason: str
    mode: Literal["manual", "automatic"]
    flags: int
    flagsHex: str
    decodedFlags: DecodedFlagsModel
    position: int | None
    rawFport: int
    rawPayloadHex: str
    description: str


class DebugResponse(BaseModel):
    server: DebugServerModel
    device: DebugDeviceModel
    budget: DebugBudgetModel
    inFlightCommand: DebugCommandModel | None
    queuedCommands: list[DebugCommandModel]
    lastCommandSent: DebugCommandModel | None
    lastUplinkReceived: DebugUplinkModel | None
    recentCommands: list[DebugCommandModel]
    recentUplinks: list[DebugUplinkModel]


class TelemetryReading(BaseModel):
    recordedAt: str
    lightLevel: int


class TelemetryResponse(BaseModel):
    deviceId: str
    sensor: str
    unit: str
    range: list[int]
    readings: list[TelemetryReading]
