"""Generic declarative binary LoRaWAN payload codec driven by protocol.json.

Zero DB, MQTT, FastAPI, or Pydantic imports.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from models import (
    CommandKind,
    DecodedDownlink,
    DecodedLightTelemetry,
    DecodedStateUplink,
    EncodedDownlink,
)

_ENDIANNESS_PREFIXES = {">", "<", "!", "="}
_OPTIONAL_COMMANDS = {CommandKind.CALIBRATE}  # v4.1 additions; protocol.v3.json may omit them
_REQUIRED_STATE_FLAGS = (
    "automatic",
    "is_moving",
    "is_calibrated",
    "is_day",
    "open_by_sun",
    "close_by_sun",
    "keep_indoor_light",
)


@dataclass(frozen=True)
class CmdIdSpec:
    min: int
    max: int


@dataclass(frozen=True)
class HandshakeSpec:
    timeout_sec: int
    max_attempts: int
    daily_downlink_warn_threshold: int


@dataclass(frozen=True)
class StateUplinkSpec:
    f_port: int
    struct_format: str
    compiled_struct: struct.Struct
    fields: tuple[str, ...]
    uncalibrated_position: int
    reasons: dict[int, str]
    reason_to_code: dict[str, int]
    flag_bits: dict[str, int]


@dataclass(frozen=True)
class CommandSpec:
    kind: CommandKind
    opcode: int
    struct_format: str
    compiled_struct: struct.Struct
    fields: tuple[str, ...]
    mode_values: dict[str, int]
    value_to_mode: dict[int, str]
    cfg_bits: dict[str, int]
    action_values: dict[str, int]
    value_to_action: dict[int, str]


@dataclass(frozen=True)
class DownlinksSpec:
    f_port: int
    commands: dict[CommandKind, CommandSpec]
    commands_by_opcode: dict[int, CommandSpec]


@dataclass(frozen=True)
class TelemetryUplinkSpec:
    enabled: bool
    f_port: int
    struct_format: str
    compiled_struct: struct.Struct
    fields: tuple[str, ...]
    schema_version: int
    sample_count: int
    sample_interval_min: int
    max_raw_value: int
    sensor: str
    unit: str


@dataclass(frozen=True)
class ProtocolSpec:
    version: str
    confirmed_downlinks: bool
    cmd_id: CmdIdSpec
    handshake: HandshakeSpec
    state_uplink: StateUplinkSpec
    downlinks: DownlinksSpec
    telemetry_uplink: TelemetryUplinkSpec
    consolidated_config_downlink: bool = False


def _require_strict_int(value: Any, name: str, min_val: int = 0, max_val: int = 255) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if value < min_val or value > max_val:
        raise ValueError(f"{name} must be in {min_val}..{max_val}, got {value}")
    return value


def _compile_struct(fmt: str, context: str) -> struct.Struct:
    if not isinstance(fmt, str) or not fmt or fmt[0] not in _ENDIANNESS_PREFIXES:
        raise ValueError(
            f"{context}.struct_format must start with explicit endianness ({sorted(_ENDIANNESS_PREFIXES)}), got {fmt!r}"
        )
    try:
        return struct.Struct(fmt)
    except struct.error as exc:
        raise ValueError(f"Invalid struct_format in {context}: {fmt!r}") from exc


def _validate_struct_field_count(compiled: struct.Struct, expected_items: int, context: str) -> None:
    dummy = b"\x00" * compiled.size
    unpacked_len = len(compiled.unpack(dummy))
    if unpacked_len != expected_items:
        raise ValueError(
            f"{context}: struct_format unpacks {unpacked_len} values, expected {expected_items}"
        )


def load_protocol_spec(path: Path | str) -> ProtocolSpec:
    """Load and validate a protocol.json file into a typed ProtocolSpec."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    cmd_id_raw = raw["cmd_id"]
    cmd_min = _require_strict_int(cmd_id_raw["min"], "cmd_id.min", 1, 255)
    cmd_max = _require_strict_int(cmd_id_raw["max"], "cmd_id.max", 1, 255)
    if cmd_min > cmd_max:
        raise ValueError(f"cmd_id.min ({cmd_min}) must be <= cmd_id.max ({cmd_max})")
    cmd_id_spec = CmdIdSpec(min=cmd_min, max=cmd_max)

    hs_raw = raw["handshake"]
    handshake_spec = HandshakeSpec(
        timeout_sec=_require_strict_int(hs_raw["timeout_sec"], "handshake.timeout_sec", 1, 86400),
        max_attempts=_require_strict_int(hs_raw["max_attempts"], "handshake.max_attempts", 1, 20),
        daily_downlink_warn_threshold=_require_strict_int(
            hs_raw["daily_downlink_warn_threshold"],
            "handshake.daily_downlink_warn_threshold",
            1,
            10000,
        ),
    )

    su_raw = raw["state_uplink"]
    su_struct = _compile_struct(su_raw["struct_format"], "state_uplink")
    su_fields = tuple(su_raw["fields"])
    _validate_struct_field_count(su_struct, len(su_fields), "state_uplink")
    for req_field in ("last_cmd_id", "reason", "flags", "position"):
        if req_field not in su_fields:
            raise ValueError(f"state_uplink.fields missing required field {req_field!r}")

    reasons: dict[int, str] = {}
    for k, v in su_raw["reasons"].items():
        code = _require_strict_int(int(k), f"state_uplink.reasons[{k!r}]", 0, 255)
        if not isinstance(v, str) or not v:
            raise ValueError(f"state_uplink.reasons[{k!r}] must be a non-empty string")
        reasons[code] = v
    reason_to_code = {v: k for k, v in reasons.items()}

    flag_bits: dict[str, int] = {}
    for flag_name in _REQUIRED_STATE_FLAGS:
        if flag_name not in su_raw["flag_bits"]:
            raise ValueError(f"state_uplink.flag_bits missing required flag {flag_name!r}")
    for k, v in su_raw["flag_bits"].items():
        flag_bits[str(k)] = _require_strict_int(v, f"state_uplink.flag_bits[{k!r}]", 0, 7)

    state_uplink_spec = StateUplinkSpec(
        f_port=_require_strict_int(su_raw["f_port"], "state_uplink.f_port", 1, 223),
        struct_format=su_raw["struct_format"],
        compiled_struct=su_struct,
        fields=su_fields,
        uncalibrated_position=_require_strict_int(
            su_raw["uncalibrated_position"], "state_uplink.uncalibrated_position", 0, 255
        ),
        reasons=reasons,
        reason_to_code=reason_to_code,
        flag_bits=flag_bits,
    )

    dl_raw = raw["downlinks"]
    dl_f_port = _require_strict_int(dl_raw["f_port"], "downlinks.f_port", 1, 223)
    commands: dict[CommandKind, CommandSpec] = {}
    commands_by_opcode: dict[int, CommandSpec] = {}
    raw_cmds = dl_raw["commands"]
    for kind in CommandKind:
        if kind.value not in raw_cmds:
            if kind in _OPTIONAL_COMMANDS:
                continue
            raise ValueError(f"downlinks.commands missing required command {kind.value!r}")
        c_raw = raw_cmds[kind.value]
        c_struct = _compile_struct(c_raw["struct_format"], f"downlinks.commands.{kind.value}")
        c_fields = tuple(c_raw["fields"])
        _validate_struct_field_count(c_struct, len(c_fields), f"downlinks.commands.{kind.value}")
        if "cmd_id" not in c_fields or "opcode" not in c_fields:
            raise ValueError(
                f"downlinks.commands.{kind.value}.fields must include 'cmd_id' and 'opcode'"
            )
        opcode = _require_strict_int(c_raw["opcode"], f"{kind.value}.opcode", 0, 255)
        if opcode in commands_by_opcode:
            raise ValueError(f"Duplicate downlink opcode {opcode}")

        mode_values = {
            str(k): _require_strict_int(v, f"{kind.value}.mode_values[{k!r}]", 0, 255)
            for k, v in c_raw.get("mode_values", {}).items()
        }
        cfg_bits = {
            str(k): _require_strict_int(v, f"{kind.value}.cfg_bits[{k!r}]", 0, 7)
            for k, v in c_raw.get("cfg_bits", {}).items()
        }
        action_values = {
            str(k): _require_strict_int(v, f"{kind.value}.action_values[{k!r}]", 0, 255)
            for k, v in c_raw.get("action_values", {}).items()
        }
        c_spec = CommandSpec(
            kind=kind,
            opcode=opcode,
            struct_format=c_raw["struct_format"],
            compiled_struct=c_struct,
            fields=c_fields,
            mode_values=mode_values,
            value_to_mode={v: k for k, v in mode_values.items()},
            cfg_bits=cfg_bits,
            action_values=action_values,
            value_to_action={v: k for k, v in action_values.items()},
        )
        commands[kind] = c_spec
        commands_by_opcode[opcode] = c_spec

    downlinks_spec = DownlinksSpec(
        f_port=dl_f_port,
        commands=commands,
        commands_by_opcode=commands_by_opcode,
    )

    tu_raw = raw["telemetry_uplink"]
    tu_struct = _compile_struct(tu_raw["struct_format"], "telemetry_uplink")
    tu_fields = tuple(tu_raw["fields"])
    if "version" not in tu_fields or "status_flags" not in tu_fields or "light_samples" not in tu_fields:
        raise ValueError(
            "telemetry_uplink.fields must include 'version', 'status_flags', and 'light_samples'"
        )
    sample_count = _require_strict_int(tu_raw["sample_count"], "telemetry_uplink.sample_count", 1, 256)
    _validate_struct_field_count(tu_struct, len(tu_fields) - 1 + sample_count, "telemetry_uplink")

    telemetry_spec = TelemetryUplinkSpec(
        enabled=bool(tu_raw["enabled"]),
        f_port=_require_strict_int(tu_raw["f_port"], "telemetry_uplink.f_port", 1, 223),
        struct_format=tu_raw["struct_format"],
        compiled_struct=tu_struct,
        fields=tu_fields,
        schema_version=_require_strict_int(tu_raw["schema_version"], "telemetry_uplink.schema_version", 0, 255),
        sample_count=sample_count,
        sample_interval_min=_require_strict_int(
            tu_raw["sample_interval_min"], "telemetry_uplink.sample_interval_min", 1, 1440
        ),
        max_raw_value=_require_strict_int(tu_raw["max_raw_value"], "telemetry_uplink.max_raw_value", 1, 65535),
        sensor=str(tu_raw["sensor"]),
        unit=str(tu_raw["unit"]),
    )

    if telemetry_spec.f_port == state_uplink_spec.f_port:
        raise ValueError("state_uplink.f_port and telemetry_uplink.f_port must be distinct")

    return ProtocolSpec(
        version=str(raw["version"]),
        confirmed_downlinks=bool(raw["confirmed_downlinks"]),
        cmd_id=cmd_id_spec,
        handshake=handshake_spec,
        state_uplink=state_uplink_spec,
        downlinks=downlinks_spec,
        telemetry_uplink=telemetry_spec,
        consolidated_config_downlink=bool(
            raw.get("features", {}).get("consolidated_config_downlink", False)
        ),
    )


class ProtocolCodec:
    """Pure binary encoder/decoder parameterized entirely by a ProtocolSpec."""

    def __init__(self, spec: ProtocolSpec) -> None:
        self.spec = spec

    @classmethod
    def from_file(cls, path: Path | str) -> ProtocolCodec:
        return cls(load_protocol_spec(path))

    def with_handshake_timeout(self, timeout_sec: int) -> ProtocolCodec:
        """Return a copy of this codec with an overridden handshake timeout."""
        valid = _require_strict_int(timeout_sec, "handshake.timeout_sec", 1, 86400)
        return ProtocolCodec(
            replace(self.spec, handshake=replace(self.spec.handshake, timeout_sec=valid))
        )

    def next_cmd_id(self, current: int) -> int:
        """Return next command ID wrapping max -> min (never 0)."""
        if current < self.spec.cmd_id.min or current >= self.spec.cmd_id.max:
            return self.spec.cmd_id.min
        return current + 1

    def decode_uplink(
        self, f_port: int, payload: bytes
    ) -> DecodedStateUplink | DecodedLightTelemetry | None:
        """Decode raw uplink bytes on f_port.

        Returns None for unknown or disabled f_port.
        Raises ValueError on malformed payloads.
        """
        raw = bytes(payload)
        if f_port == self.spec.state_uplink.f_port:
            return self._decode_state_uplink(raw)
        if f_port == self.spec.telemetry_uplink.f_port:
            if not self.spec.telemetry_uplink.enabled:
                return None
            return self._decode_telemetry_uplink(raw)
        return None

    def _decode_state_uplink(self, payload: bytes) -> DecodedStateUplink:
        su = self.spec.state_uplink
        if len(payload) != su.compiled_struct.size:
            raise ValueError(
                f"State uplink length {len(payload)} != expected {su.compiled_struct.size}"
            )
        unpacked = su.compiled_struct.unpack(payload)
        mapped = dict(zip(su.fields, unpacked))

        last_cmd_id = int(mapped["last_cmd_id"])
        reason_code = int(mapped["reason"])
        if reason_code not in su.reasons:
            raise ValueError(f"Unknown state uplink reason code: {reason_code}")
        reason = su.reasons[reason_code]

        raw_flags = int(mapped["flags"])
        raw_pos = int(mapped["position"])
        if raw_pos == su.uncalibrated_position:
            position: int | None = None
        elif 0 <= raw_pos <= 100:
            position = raw_pos
        else:
            raise ValueError(f"Invalid curtain position byte: {raw_pos}")

        fb = su.flag_bits
        automatic = bool(raw_flags & (1 << fb["automatic"]))
        return DecodedStateUplink(
            last_cmd_id=last_cmd_id,
            reason=reason,
            mode="automatic" if automatic else "manual",
            is_moving=bool(raw_flags & (1 << fb["is_moving"])),
            is_calibrated=bool(raw_flags & (1 << fb["is_calibrated"])),
            is_day=bool(raw_flags & (1 << fb["is_day"])),
            open_by_sun=bool(raw_flags & (1 << fb["open_by_sun"])),
            close_by_sun=bool(raw_flags & (1 << fb["close_by_sun"])),
            keep_indoor_light=bool(raw_flags & (1 << fb["keep_indoor_light"])),
            raw_flags=raw_flags,
            position=position,
            is_calibrating="is_calibrating" in fb and bool(raw_flags & (1 << fb["is_calibrating"])),
        )

    def _decode_telemetry_uplink(self, payload: bytes) -> DecodedLightTelemetry:
        tu = self.spec.telemetry_uplink
        if len(payload) != tu.compiled_struct.size:
            raise ValueError(
                f"Telemetry uplink length {len(payload)} != expected {tu.compiled_struct.size}"
            )
        unpacked = tu.compiled_struct.unpack(payload)
        idx = 0
        mapped: dict[str, Any] = {}
        for field_name in tu.fields:
            if field_name == "light_samples":
                mapped[field_name] = tuple(
                    int(x) for x in unpacked[idx : idx + tu.sample_count]
                )
                idx += tu.sample_count
            else:
                mapped[field_name] = int(unpacked[idx])
                idx += 1

        version = mapped["version"]
        if version != tu.schema_version:
            raise ValueError(
                f"Unexpected telemetry schema_version {version}, expected {tu.schema_version}"
            )
        samples: tuple[int, ...] = mapped["light_samples"]
        if len(samples) != tu.sample_count:
            raise ValueError(
                f"Telemetry sample count {len(samples)} != expected {tu.sample_count}"
            )
        return DecodedLightTelemetry(
            version=version,
            status_flags=mapped["status_flags"],
            samples=samples,
        )

    def encode_command(
        self, cmd_id: int, kind: CommandKind | str, args: dict[str, Any]
    ) -> EncodedDownlink:
        """Encode semantic command + snake_case args into wire downlink bytes."""
        valid_cmd_id = _require_strict_int(
            cmd_id, "cmd_id", self.spec.cmd_id.min, self.spec.cmd_id.max
        )
        try:
            cmd_kind = kind if isinstance(kind, CommandKind) else CommandKind(kind)
        except ValueError as exc:
            raise ValueError(f"Unknown command kind: {kind!r}") from exc

        cmd_spec = self.spec.downlinks.commands.get(cmd_kind)
        if cmd_spec is None:
            raise ValueError(f"Command {cmd_kind.value!r} not configured in protocol.json")

        if not isinstance(args, dict):
            raise ValueError("command args must be a dict")

        # Validate cross-field threshold relationship when encoding auto settings
        if (
            "outside_threshold_open" in cmd_spec.fields
            and "outside_threshold_close" in cmd_spec.fields
        ):
            if "outside_threshold_open" not in args or "outside_threshold_close" not in args:
                raise ValueError("Missing threshold args for set_auto_settings")
            thr_open = _require_strict_int(
                args["outside_threshold_open"], "outside_threshold_open", 0, 100
            )
            thr_close = _require_strict_int(
                args["outside_threshold_close"], "outside_threshold_close", 0, 100
            )
            if thr_close >= thr_open:
                raise ValueError(
                    "outside_threshold_close must be lower than outside_threshold_open"
                )

        packed_values: list[int] = []
        for field_name in cmd_spec.fields:
            if field_name == "cmd_id":
                packed_values.append(valid_cmd_id)
            elif field_name == "opcode":
                packed_values.append(cmd_spec.opcode)
            elif field_name == "position":
                if "position" not in args:
                    raise ValueError("Missing 'position' arg")
                packed_values.append(
                    _require_strict_int(args["position"], "position", 0, 100)
                )
            elif field_name == "mode":
                mode = args.get("mode")
                if not isinstance(mode, str) or mode not in cmd_spec.mode_values:
                    raise ValueError(f"Invalid mode arg: {mode!r}")
                packed_values.append(cmd_spec.mode_values[mode])
            elif field_name == "action":
                action = args.get("action")
                if not isinstance(action, str) or action not in cmd_spec.action_values:
                    raise ValueError(f"Invalid action arg: {action!r}")
                packed_values.append(cmd_spec.action_values[action])
            elif field_name == "cfg":
                cfg = 0
                required_bits = {"auto_open", "auto_close", "maintain_light"}
                v4_only_bits = ("automatic", "is_day", "set_mode", "set_day")
                if not self.spec.consolidated_config_downlink:
                    for v4_key in v4_only_bits:
                        if v4_key in args and args[v4_key]:
                            raise ValueError(
                                f"Argument {v4_key!r} is not supported when consolidated_config_downlink is disabled"
                            )

                set_mode_val = (
                    _require_strict_int(args["set_mode"], "set_mode", 0, 1)
                    if "set_mode" in args
                    else 0
                )
                set_day_val = (
                    _require_strict_int(args["set_day"], "set_day", 0, 1)
                    if "set_day" in args
                    else 0
                )

                for bit_name, bit_pos in cmd_spec.cfg_bits.items():
                    if bit_name not in args:
                        if bit_name in required_bits:
                            raise ValueError(f"Missing {bit_name!r} arg for cfg bitmask")
                        continue
                    bit_val = _require_strict_int(args[bit_name], bit_name, 0, 1)
                    if bit_name == "automatic" and not set_mode_val:
                        continue
                    if bit_name == "is_day" and not set_day_val:
                        continue
                    if bit_val:
                        cfg |= 1 << bit_pos

                if cfg & 0x80:
                    raise ValueError("cfg bit 7 (0x80) must be 0")
                if not self.spec.consolidated_config_downlink and cfg > 7:
                    raise ValueError("v3 cfg byte must be <= 7")
                packed_values.append(cfg)
            elif field_name in (
                "target_light_level",
                "outside_threshold_open",
                "outside_threshold_close",
            ):
                if field_name not in args:
                    raise ValueError(f"Missing {field_name!r} arg")
                packed_values.append(
                    _require_strict_int(args[field_name], field_name, 0, 100)
                )
            else:
                raise ValueError(f"Unsupported downlink field {field_name!r}")

        payload = cmd_spec.compiled_struct.pack(*packed_values)
        return EncodedDownlink(
            f_port=self.spec.downlinks.f_port,
            payload=payload,
            confirmed=self.spec.confirmed_downlinks,
        )

    def decode_downlink(self, f_port: int, payload: bytes) -> DecodedDownlink:
        """Decode downlink bytes into a semantic DecodedDownlink (for tests/MockTransport)."""
        raw = bytes(payload)
        if f_port != self.spec.downlinks.f_port:
            raise ValueError(
                f"Unexpected downlink f_port {f_port}, expected {self.spec.downlinks.f_port}"
            )
        if not raw:
            raise ValueError("Empty downlink payload")

        # Locate opcode by scanning configured commands whose struct size matches
        matched_spec: CommandSpec | None = None
        matched_values: dict[str, int] | None = None
        for cmd_spec in self.spec.downlinks.commands.values():
            if len(raw) != cmd_spec.compiled_struct.size:
                continue
            unpacked = dict(zip(cmd_spec.fields, cmd_spec.compiled_struct.unpack(raw)))
            if unpacked.get("opcode") == cmd_spec.opcode:
                matched_spec = cmd_spec
                matched_values = unpacked
                break

        if matched_spec is None or matched_values is None:
            raise ValueError(f"Unrecognized downlink payload: {raw.hex()}")

        cmd_id = _require_strict_int(
            matched_values["cmd_id"],
            "cmd_id",
            self.spec.cmd_id.min,
            self.spec.cmd_id.max,
        )
        args: dict[str, Any] = {}
        for field_name, val in matched_values.items():
            if field_name in ("cmd_id", "opcode"):
                continue
            if field_name == "mode":
                if val not in matched_spec.value_to_mode:
                    raise ValueError(f"Unknown mode byte {val}")
                args["mode"] = matched_spec.value_to_mode[val]
            elif field_name == "action":
                if val not in matched_spec.value_to_action:
                    raise ValueError(f"Unknown action byte {val}")
                args["action"] = matched_spec.value_to_action[val]
            elif field_name == "cfg":
                for bit_name, bit_pos in matched_spec.cfg_bits.items():
                    args[bit_name] = 1 if (val & (1 << bit_pos)) else 0
            else:
                args[field_name] = int(val)

        return DecodedDownlink(cmd_id=cmd_id, kind=matched_spec.kind, args=args)

    def encode_state_uplink(self, state: DecodedStateUplink) -> tuple[int, bytes]:
        """Encode a DecodedStateUplink back to (f_port, bytes) using protocol.json."""
        su = self.spec.state_uplink
        if state.reason not in su.reason_to_code:
            raise ValueError(f"Unknown state reason: {state.reason!r}")

        fb = su.flag_bits
        flags = 0
        if state.mode == "automatic":
            flags |= 1 << fb["automatic"]
        if state.is_moving:
            flags |= 1 << fb["is_moving"]
        if state.is_calibrated:
            flags |= 1 << fb["is_calibrated"]
        if state.is_day:
            flags |= 1 << fb["is_day"]
        if state.open_by_sun:
            flags |= 1 << fb["open_by_sun"]
        if state.close_by_sun:
            flags |= 1 << fb["close_by_sun"]
        if state.keep_indoor_light:
            flags |= 1 << fb["keep_indoor_light"]
        if state.is_calibrating and "is_calibrating" in fb:
            flags |= 1 << fb["is_calibrating"]

        pos_byte = (
            su.uncalibrated_position
            if state.position is None
            else _require_strict_int(state.position, "position", 0, 100)
        )
        field_values = {
            "last_cmd_id": _require_strict_int(state.last_cmd_id, "last_cmd_id", 0, 255),
            "reason": su.reason_to_code[state.reason],
            "flags": flags,
            "position": pos_byte,
        }
        packed = su.compiled_struct.pack(*(field_values[f] for f in su.fields))
        return su.f_port, packed

    def decode_flags_dict(self, raw_flags: int) -> dict[str, bool]:
        """Decode raw FPort 1 flags byte into REST/debug boolean keys."""
        fb = self.spec.state_uplink.flag_bits
        return {
            "automatic": bool(raw_flags & (1 << fb["automatic"])),
            "moving": bool(raw_flags & (1 << fb["is_moving"])),
            "calibrated": bool(raw_flags & (1 << fb["is_calibrated"])),
            "day": bool(raw_flags & (1 << fb["is_day"])),
            "openBySun": bool(raw_flags & (1 << fb["open_by_sun"])),
            "closeBySun": bool(raw_flags & (1 << fb["close_by_sun"])),
            "keepIndoorLight": bool(raw_flags & (1 << fb["keep_indoor_light"])),
            "calibrating": "is_calibrating" in fb and bool(raw_flags & (1 << fb["is_calibrating"])),
        }

    def describe_command(
        self, cmd_id: int | None, kind: CommandKind | str, args: dict[str, Any]
    ) -> str:
        """Build a concise human-readable description of a semantic command."""
        prefix = f"#{cmd_id} " if cmd_id is not None else ""
        kind_val = kind.value if isinstance(kind, CommandKind) else str(kind)
        if kind_val == CommandKind.SET_POSITION.value:
            return f"{prefix}go to {args.get('position')} % closed"
        if kind_val == CommandKind.SET_MODE.value:
            return f"{prefix}mode {args.get('mode')}"
        if kind_val == CommandKind.CALIBRATE.value:
            return f"{prefix}start calibration (buttons set bottom, then top)"
        if kind_val == CommandKind.SET_SCHEDULE.value:
            action = args.get("action")
            detail = "open (day starts)" if action == "open" else "close (day ends)"
            return f"{prefix}schedule: {detail}"
        if kind_val == CommandKind.SET_AUTO_SETTINGS.value:
            open_src = "sun" if args.get("auto_open") else "time"
            close_src = "sun" if args.get("auto_close") else "time"
            keep = "on" if args.get("maintain_light") else "off"
            parts = [
                f"open by {open_src}",
                f"close by {close_src}",
                f"maintain {keep} @ {args.get('target_light_level')} %",
                f"outdoor > {args.get('outside_threshold_open')} % / < {args.get('outside_threshold_close')} %",
            ]
            if args.get("set_mode"):
                parts.append(f"mode={'automatic' if args.get('automatic') else 'manual'}")
            if args.get("set_day"):
                parts.append(f"day={'day' if args.get('is_day') else 'night'}")
            return f"{prefix}auto: " + ", ".join(parts)
        return f"{prefix}{kind_val}"

    def describe_state_uplink(
        self,
        last_cmd_id: int,
        reason: str,
        mode: str,
        position: int | None,
        is_moving: bool,
        is_day: bool,
    ) -> str:
        """Build a concise human-readable summary of an FPort 1 state uplink."""
        pos_txt = "uncalibrated" if position is None else f"{position} % closed"
        bits = [reason, pos_txt, mode]
        if is_moving:
            bits.append("moving")
        if mode == "automatic":
            bits.append("day" if is_day else "night")
        return f"#{last_cmd_id} " + " · ".join(bits)
