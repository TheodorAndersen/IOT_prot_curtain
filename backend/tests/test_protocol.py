"""Pure unit tests for protocol.py and protocol.json (v4 default) / protocol.v3.json."""

from __future__ import annotations

import copy
import json
import struct
from pathlib import Path

import pytest

from models import CommandKind, DecodedLightTelemetry, DecodedStateUplink
from protocol import ProtocolCodec, load_protocol_spec

PROTOCOL_JSON_PATH = Path(__file__).resolve().parent.parent / "protocol.json"
PROTOCOL_V3_JSON_PATH = Path(__file__).resolve().parent.parent / "protocol.v3.json"


@pytest.fixture
def codec() -> ProtocolCodec:
    return ProtocolCodec.from_file(PROTOCOL_JSON_PATH)


@pytest.fixture
def codec_v3() -> ProtocolCodec:
    return ProtocolCodec.from_file(PROTOCOL_V3_JSON_PATH)


def test_default_v4_spec_settings(codec: ProtocolCodec, codec_v3: ProtocolCodec) -> None:
    assert codec.spec.version == "v4"
    assert codec.spec.confirmed_downlinks is False
    assert codec.spec.consolidated_config_downlink is True
    assert codec.spec.handshake.timeout_sec == 25
    assert codec.spec.handshake.max_attempts == 2
    assert codec.spec.telemetry_uplink.enabled is False

    assert codec_v3.spec.version == "v3"
    assert codec_v3.spec.confirmed_downlinks is False
    assert codec_v3.spec.consolidated_config_downlink is False
    assert codec_v3.spec.handshake.timeout_sec == 25


def test_downlink_vectors(codec: ProtocolCodec) -> None:
    dl_pos = codec.encode_command(1, CommandKind.SET_POSITION, {"position": 40})
    assert dl_pos.f_port == 1
    assert dl_pos.confirmed is False
    assert dl_pos.payload == bytes([1, 0x01, 40])

    dl_mode_manual = codec.encode_command(2, CommandKind.SET_MODE, {"mode": "manual"})
    assert dl_mode_manual.payload == bytes([2, 0x02, 0])

    dl_mode_auto = codec.encode_command(3, CommandKind.SET_MODE, {"mode": "automatic"})
    assert dl_mode_auto.payload == bytes([3, 0x02, 1])

    dl_auto = codec.encode_command(
        4,
        CommandKind.SET_AUTO_SETTINGS,
        {
            "auto_open": 1,
            "auto_close": 0,
            "maintain_light": 1,
            "target_light_level": 35,
            "outside_threshold_open": 80,
            "outside_threshold_close": 25,
        },
    )
    assert dl_auto.payload == bytes([4, 0x03, 0x05, 35, 80, 25])

    dl_sched_open = codec.encode_command(5, CommandKind.SET_SCHEDULE, {"action": "open"})
    assert dl_sched_open.payload == bytes([5, 0x04, 0])

    dl_sched_close = codec.encode_command(255, CommandKind.SET_SCHEDULE, {"action": "close"})
    assert dl_sched_close.payload == bytes([255, 0x04, 1])


def test_protocol_final_canonical_v4_examples(codec: ProtocolCodec) -> None:
    """Verify the 3 exact Type 3 hex vectors in assets/PROTOCOL_FINAL.md."""
    base = {
        "auto_open": 0,
        "auto_close": 0,
        "maintain_light": 1,
        "target_light_level": 27,
        "outside_threshold_open": 80,
        "outside_threshold_close": 10,
    }
    # 1. 16 03 04 1B 50 0A: settings only
    dl1 = codec.encode_command(0x16, CommandKind.SET_AUTO_SETTINGS, base)
    assert dl1.payload.hex().upper() == "1603041B500A"

    # 2. 16 03 7C 1B 50 0A: settings + automatic + day (bits 2,3,4,5,6 = 0x7C)
    dl2 = codec.encode_command(
        0x16,
        CommandKind.SET_AUTO_SETTINGS,
        {**base, "set_mode": 1, "automatic": 1, "set_day": 1, "is_day": 1},
    )
    assert dl2.payload.hex().upper() == "16037C1B500A"

    # 3. 16 03 24 1B 50 0A: settings + manual, day stays as is (bits 2,5 = 0x24)
    dl3 = codec.encode_command(
        0x16,
        CommandKind.SET_AUTO_SETTINGS,
        {**base, "set_mode": 1, "automatic": 0},
    )
    assert dl3.payload.hex().upper() == "1603241B500A"

    # Separate gate check: setting automatic=1 with set_mode=0 must NOT set bit 3
    dl_gated = codec.encode_command(
        0x16,
        CommandKind.SET_AUTO_SETTINGS,
        {**base, "set_mode": 0, "automatic": 1, "set_day": 1, "is_day": 1},
    )
    assert dl_gated.payload.hex().upper() == "1603541B500A"


def test_v3_fallback_rejects_v4_bits(codec_v3: ProtocolCodec) -> None:
    base = {
        "auto_open": 1,
        "auto_close": 0,
        "maintain_light": 1,
        "target_light_level": 40,
        "outside_threshold_open": 80,
        "outside_threshold_close": 20,
    }
    dl = codec_v3.encode_command(10, CommandKind.SET_AUTO_SETTINGS, base)
    assert dl.payload == bytes([10, 0x03, 0x05, 40, 80, 20])

    with pytest.raises(ValueError):
        codec_v3.encode_command(
            11,
            CommandKind.SET_AUTO_SETTINGS,
            {**base, "set_mode": 1, "automatic": 1},
        )
    with pytest.raises(ValueError):
        codec_v3.encode_command(
            11,
            CommandKind.SET_AUTO_SETTINGS,
            {**base, "set_day": 1, "is_day": 1},
        )


def test_downlink_roundtrip(codec: ProtocolCodec) -> None:
    cases = [
        (1, CommandKind.SET_POSITION, {"position": 75}),
        (12, CommandKind.SET_MODE, {"mode": "automatic"}),
        (
            99,
            CommandKind.SET_AUTO_SETTINGS,
            {
                "auto_open": 1,
                "auto_close": 1,
                "maintain_light": 0,
                "automatic": 1,
                "is_day": 0,
                "set_mode": 1,
                "set_day": 1,
                "target_light_level": 55,
                "outside_threshold_open": 85,
                "outside_threshold_close": 15,
            },
        ),
        (254, CommandKind.SET_SCHEDULE, {"action": "close"}),
    ]
    for cmd_id, kind, args in cases:
        encoded = codec.encode_command(cmd_id, kind, args)
        decoded = codec.decode_downlink(encoded.f_port, encoded.payload)
        assert decoded.cmd_id == cmd_id
        assert decoded.kind == kind
        assert decoded.args == args


@pytest.mark.parametrize(
    ("code", "expected_reason"),
    [
        (0, "heartbeat"),
        (1, "startup"),
        (2, "button"),
        (3, "app_command"),
        (4, "auto"),
        (5, "schedule"),
        (6, "calibrated"),
        (7, "rejected"),
    ],
)
def test_state_uplink_reasons(
    codec: ProtocolCodec, code: int, expected_reason: str
) -> None:
    decoded = codec.decode_uplink(1, bytes([42, code, 0x04, 50]))
    assert isinstance(decoded, DecodedStateUplink)
    assert decoded.last_cmd_id == 42
    assert decoded.reason == expected_reason
    assert decoded.position == 50
    assert decoded.is_calibrated is True


def test_state_uplink_flags_and_positions(codec: ProtocolCodec) -> None:
    all_flags = 0b01111111
    decoded = codec.decode_uplink(1, bytes([7, 3, all_flags, 0]))
    assert isinstance(decoded, DecodedStateUplink)
    assert decoded.mode == "automatic"
    assert decoded.is_moving is True
    assert decoded.is_calibrated is True
    assert decoded.is_day is True
    assert decoded.open_by_sun is True
    assert decoded.close_by_sun is True
    assert decoded.keep_indoor_light is True
    assert decoded.raw_flags == all_flags
    assert decoded.position == 0

    assert codec.decode_flags_dict(0x24) == {
        "automatic": False,
        "moving": False,
        "calibrated": True,
        "day": False,
        "openBySun": False,
        "closeBySun": True,
        "keepIndoorLight": False,
        "calibrating": False,
    }

    decoded_100 = codec.decode_uplink(1, bytes([0, 0, 0x00, 100]))
    assert isinstance(decoded_100, DecodedStateUplink)
    assert decoded_100.mode == "manual"
    assert decoded_100.is_moving is False
    assert decoded_100.is_calibrated is False
    assert decoded_100.position == 100

    uncalibrated = codec.decode_uplink(1, bytes([0, 1, 0x00, 255]))
    assert isinstance(uncalibrated, DecodedStateUplink)
    assert uncalibrated.position is None
    assert uncalibrated.reason == "startup"


def test_telemetry_uplink_disabled_by_default_and_optional_decode(
    codec: ProtocolCodec, tmp_path: Path
) -> None:
    samples = [100 + i * 50 for i in range(12)]
    payload = struct.pack(">BB12H", 1, 0b00000101, *samples)
    # Disabled by default in PROTOCOL_FINAL.md v4
    assert codec.decode_uplink(2, payload) is None

    # Unknown f_port returns None
    assert codec.decode_uplink(99, b"\x00\x01") is None

    # When enabled via config, decodes cleanly and validates lengths/schema
    raw_cfg = copy.deepcopy(json.loads(PROTOCOL_JSON_PATH.read_text(encoding="utf-8")))
    raw_cfg["telemetry_uplink"]["enabled"] = True
    enabled_path = tmp_path / "proto_tel_enabled.json"
    enabled_path.write_text(json.dumps(raw_cfg), encoding="utf-8")
    enabled_codec = ProtocolCodec.from_file(enabled_path)

    decoded = enabled_codec.decode_uplink(2, payload)
    assert isinstance(decoded, DecodedLightTelemetry)
    assert decoded.version == 1
    assert decoded.status_flags == 5
    assert decoded.samples == tuple(samples)

    with pytest.raises(ValueError):
        enabled_codec.decode_uplink(2, b"\x01\x00" + b"\x00\x10" * 11)
    bad_ver = struct.pack(">BB12H", 2, 0, *([100] * 12))
    with pytest.raises(ValueError):
        enabled_codec.decode_uplink(2, bad_ver)


def test_malformed_uplinks_raise_value_error(codec: ProtocolCodec) -> None:
    with pytest.raises(ValueError):
        codec.decode_uplink(1, bytes([1, 0, 0]))
    with pytest.raises(ValueError):
        codec.decode_uplink(1, bytes([1, 0, 0, 50, 0]))
    with pytest.raises(ValueError):
        codec.decode_uplink(1, bytes([1, 8, 0, 50]))
    with pytest.raises(ValueError):
        codec.decode_uplink(1, bytes([1, 0, 0, 101]))
    with pytest.raises(ValueError):
        codec.decode_uplink(1, bytes([1, 0, 0, 254]))


def test_invalid_command_encoding_raises_value_error(codec: ProtocolCodec) -> None:
    for bad_id in (0, 256, -1, True):
        with pytest.raises(ValueError):
            codec.encode_command(bad_id, CommandKind.SET_POSITION, {"position": 50})  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        codec.encode_command(1, CommandKind.SET_POSITION, {"position": True})  # type: ignore[dict-item]

    with pytest.raises(ValueError):
        codec.encode_command(1, CommandKind.SET_POSITION, {"position": 101})

    with pytest.raises(ValueError):
        codec.encode_command(
            1,
            CommandKind.SET_AUTO_SETTINGS,
            {
                "auto_open": 1,
                "auto_close": 1,
                "maintain_light": 0,
                "target_light_level": 50,
                "outside_threshold_open": 40,
                "outside_threshold_close": 40,
            },
        )


def test_next_cmd_id_wrap_and_helpers(codec: ProtocolCodec) -> None:
    assert codec.next_cmd_id(1) == 2
    assert codec.next_cmd_id(254) == 255
    assert codec.next_cmd_id(255) == 1
    assert codec.next_cmd_id(0) == 1

    assert codec.describe_command(22, CommandKind.SET_POSITION, {"position": 40}) == "#22 go to 40 % closed"
    assert codec.describe_state_uplink(22, "app_command", "manual", 40, False, False) == "#22 app_command · 40 % closed · manual"
    overridden = codec.with_handshake_timeout(75)
    assert overridden.spec.handshake.timeout_sec == 75
    assert codec.spec.handshake.timeout_sec == 25


def test_custom_protocol_json_overrides(tmp_path: Path) -> None:
    raw = copy.deepcopy(json.loads(PROTOCOL_JSON_PATH.read_text(encoding="utf-8")))
    raw["state_uplink"]["f_port"] = 10
    raw["state_uplink"]["flag_bits"]["automatic"] = 7
    raw["state_uplink"]["flag_bits"]["keep_indoor_light"] = 0
    raw["downlinks"]["f_port"] = 11
    raw["downlinks"]["commands"]["set_position"]["opcode"] = 99
    custom_path = tmp_path / "custom_protocol.json"
    custom_path.write_text(json.dumps(raw), encoding="utf-8")

    custom = ProtocolCodec(load_protocol_spec(custom_path))
    dl = custom.encode_command(5, CommandKind.SET_POSITION, {"position": 33})
    assert dl.f_port == 11
    assert dl.payload == bytes([5, 99, 33])

    assert custom.decode_uplink(1, bytes([1, 0, 0x04, 50])) is None
    decoded = custom.decode_uplink(10, bytes([5, 3, 0x81, 33]))
    assert isinstance(decoded, DecodedStateUplink)
    assert decoded.mode == "automatic"
    assert decoded.keep_indoor_light is True
    assert decoded.position == 33


def test_v41_calibrate_command_and_calibrating_flag(codec: ProtocolCodec) -> None:
    """v4.1: downlink [id] 05 01 starts calibration; uplink flag bit 7 = calibrating."""
    dl = codec.encode_command(9, "calibrate", {"action": "start"})
    assert dl.payload == bytes([9, 5, 1])
    assert codec.decode_downlink(1, dl.payload).kind.value == "calibrate"

    # reply to the command: calibrating, not calibrated, position unknown
    step1 = codec.decode_uplink(1, bytes([9, 3, 0x80, 255]))
    assert isinstance(step1, DecodedStateUplink)
    assert step1.is_calibrating is True and step1.is_calibrated is False and step1.position is None
    # bottom set: position 100 while still calibrating
    step2 = codec.decode_uplink(1, bytes([9, 2, 0x80, 100]))
    assert isinstance(step2, DecodedStateUplink)
    assert step2.is_calibrating is True and step2.position == 100
    # done
    done = codec.decode_uplink(1, bytes([9, 6, 0x04, 0]))
    assert isinstance(done, DecodedStateUplink)
    assert done.is_calibrating is False and done.is_calibrated is True and done.reason == "calibrated"
    assert codec.encode_state_uplink(step1) == (1, bytes([9, 3, 0x80, 255]))
