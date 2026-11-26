"""Application configuration loaded from environment variables and .env files."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env", override=False)
load_dotenv(_BASE_DIR.parent / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the curtain motor backend."""

    mqtt_api_key: str
    ttn_appid: str
    ttn_devid: str
    ttn_region: str
    database_url: str
    app_host: str
    app_port: int
    transport_mode: str
    protocol_config_path: Path
    dispatch_poll_sec: float
    scheduler_poll_sec: float
    schedule_timezone: str
    token_ttl_hours: int
    demo_user_email: str
    demo_user_password_md5: str
    device_name: str
    device_dev_eui: str
    ttn_tenant: str = "ttn"
    ttn_mqtt_host: str = ""
    ttn_mqtt_port: int = 8883
    ttn_mqtt_use_tls: bool = True
    ttn_mqtt_ca_certs: str = ""
    sync_on_startup: bool = False
    protocol_version: str = "v4"
    handshake_timeout_sec: int | None = None
    app_root_path: str = ""
    # USB/TTN switch: a second MQTT broker where usb_bridge/mqtt_usb_bridge.py stands in for TTN over USB
    usb_mqtt_host: str = ""
    usb_mqtt_port: int = 1883
    usb_mqtt_username: str = ""
    usb_mqtt_password: str = ""
    link_default: str = "usb"
    link_state_file: Path | None = None


def _get_str(source: Mapping[str, str], key: str, default: str = "") -> str:
    raw = source.get(key)
    if raw is None:
        return default
    return raw.strip()


def _get_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = _get_str(source, key, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _get_float(source: Mapping[str, str], key: str, default: float) -> float:
    raw = _get_str(source, key, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number, got {raw!r}") from exc


def _get_bool(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get_str(source, key, "")
    if not raw:
        return default
    val = raw.lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean, got {raw!r}")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load and validate Settings from ``env`` (defaults to ``os.environ``)."""
    source = os.environ if env is None else env

    transport_mode = _get_str(source, "TRANSPORT_MODE", "ttn_mqtt").lower()
    if transport_mode not in {"ttn_mqtt", "mock"}:
        raise ValueError(
            f"TRANSPORT_MODE must be 'ttn_mqtt' or 'mock', got {transport_mode!r}"
        )

    ttn_devid = _get_str(source, "TTN_DEVID")
    if not ttn_devid:
        raise ValueError("TTN_DEVID is required")

    mqtt_api_key = _get_str(source, "MQTT_API_KEY")
    ttn_appid = _get_str(source, "TTN_APPID")
    if transport_mode == "ttn_mqtt":
        if not mqtt_api_key:
            raise ValueError("MQTT_API_KEY is required when TRANSPORT_MODE=ttn_mqtt")
        if not ttn_appid:
            raise ValueError("TTN_APPID is required when TRANSPORT_MODE=ttn_mqtt")

    ttn_region = _get_str(source, "TTN_REGION", "eu1") or "eu1"
    pg_password = _get_str(source, "POSTGRES_PASSWORD", "")
    if pg_password:
        pg_user = _get_str(source, "POSTGRES_USER", "curtain") or "curtain"
        pg_db = _get_str(source, "POSTGRES_DB", "curtain") or "curtain"
        pg_host = _get_str(source, "POSTGRES_HOST", "localhost") or "localhost"
        pg_port = _get_int(source, "POSTGRES_PORT", 5432)
        if not (1 <= pg_port <= 65535):
            raise ValueError(f"POSTGRES_PORT must be in 1..65535, got {pg_port}")
        database_url = (
            f"postgresql://{quote_plus(pg_user)}:{quote_plus(pg_password)}"
            f"@{pg_host}:{pg_port}/{quote_plus(pg_db)}"
        )
    else:
        database_url = (
            _get_str(
                source,
                "DATABASE_URL",
                "postgresql://curtain:curtain@localhost:5432/curtain",
            )
            or "postgresql://curtain:curtain@localhost:5432/curtain"
        )
    app_host = _get_str(source, "APP_HOST", "127.0.0.1") or "127.0.0.1"
    app_port = _get_int(source, "APP_PORT", 8000)
    if not (1 <= app_port <= 65535):
        raise ValueError(f"APP_PORT must be in 1..65535, got {app_port}")
    raw_root_path = _get_str(source, "APP_ROOT_PATH", "").rstrip("/")
    app_root_path = (
        raw_root_path
        if not raw_root_path or raw_root_path.startswith("/")
        else f"/{raw_root_path}"
    )

    protocol_version = (_get_str(source, "PROTOCOL_VERSION", "v4") or "v4").lower()
    if protocol_version not in {"v3", "v4"}:
        raise ValueError(
            f"PROTOCOL_VERSION must be 'v3' or 'v4', got {protocol_version!r}"
        )

    default_proto_file = "protocol.v3.json" if protocol_version == "v3" else "protocol.json"
    raw_proto_path = _get_str(source, "PROTOCOL_CONFIG_PATH", "") or default_proto_file
    proto_path = Path(raw_proto_path)
    if not proto_path.is_absolute():
        proto_path = (_BASE_DIR / proto_path).resolve()

    raw_hs_timeout = _get_str(source, "HANDSHAKE_TIMEOUT_SEC", "")
    handshake_timeout_sec: int | None = None
    if raw_hs_timeout:
        handshake_timeout_sec = _get_int(source, "HANDSHAKE_TIMEOUT_SEC", 25)
        if not (1 <= handshake_timeout_sec <= 86400):
            raise ValueError(
                f"HANDSHAKE_TIMEOUT_SEC must be in 1..86400, got {handshake_timeout_sec}"
            )

    dispatch_poll_sec = _get_float(source, "DISPATCH_POLL_SEC", 2.0)
    if dispatch_poll_sec <= 0:
        raise ValueError(f"DISPATCH_POLL_SEC must be > 0, got {dispatch_poll_sec}")

    scheduler_poll_sec = _get_float(source, "SCHEDULER_POLL_SEC", 10.0)
    if scheduler_poll_sec <= 0:
        raise ValueError(f"SCHEDULER_POLL_SEC must be > 0, got {scheduler_poll_sec}")

    schedule_timezone = _get_str(source, "SCHEDULE_TIMEZONE", "Europe/Copenhagen") or "Europe/Copenhagen"
    try:
        ZoneInfo(schedule_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid SCHEDULE_TIMEZONE: {schedule_timezone!r}") from exc

    token_ttl_hours = _get_int(source, "TOKEN_TTL_HOURS", 168)
    if token_ttl_hours <= 0:
        raise ValueError(f"TOKEN_TTL_HOURS must be > 0, got {token_ttl_hours}")

    demo_user_email = _get_str(source, "DEMO_USER_EMAIL", "demo@example.com").lower()
    if not demo_user_email:
        raise ValueError("DEMO_USER_EMAIL must not be empty")

    demo_user_password = _get_str(source, "DEMO_USER_PASSWORD", "")
    if demo_user_password:
        demo_user_password_md5 = hashlib.md5(
            demo_user_password.encode("utf-8")
        ).hexdigest()
    else:
        demo_user_password_md5 = _get_str(
            source, "DEMO_USER_PASSWORD_MD5", "2805ba35a46fd703543fb61625cd9393"
        ).lower()
        if not demo_user_password_md5:
            raise ValueError("DEMO_USER_PASSWORD_MD5 must not be empty")

    device_name = _get_str(source, "DEVICE_NAME", "Roller curtain") or "Roller curtain"
    device_dev_eui = _get_str(source, "DEVICE_DEV_EUI", "0004A30B00E8F83E").upper()
    if len(device_dev_eui) != 16:
        raise ValueError(
            f"DEVICE_DEV_EUI must be 16 hex characters, got length {len(device_dev_eui)}"
        )

    ttn_tenant = source["TTN_TENANT"].strip() if "TTN_TENANT" in source else "ttn"
    ttn_mqtt_host = _get_str(source, "TTN_MQTT_HOST", "")
    ttn_mqtt_port = _get_int(source, "TTN_MQTT_PORT", 8883)
    if not (1 <= ttn_mqtt_port <= 65535):
        raise ValueError(f"TTN_MQTT_PORT must be in 1..65535, got {ttn_mqtt_port}")
    ttn_mqtt_use_tls = _get_bool(source, "TTN_MQTT_USE_TLS", True)
    ttn_mqtt_ca_certs = _get_str(source, "TTN_MQTT_CA_CERTS", "")
    sync_on_startup = _get_bool(source, "SYNC_ON_STARTUP", False)

    usb_mqtt_host = _get_str(source, "USB_MQTT_HOST", "")
    usb_mqtt_port = _get_int(source, "USB_MQTT_PORT", 1883)
    if not (1 <= usb_mqtt_port <= 65535):
        raise ValueError(f"USB_MQTT_PORT must be in 1..65535, got {usb_mqtt_port}")
    usb_mqtt_password = _get_str(source, "USB_MQTT_PASSWORD", "")
    usb_mqtt_username = _get_str(source, "USB_MQTT_USERNAME", "") or ("bridge" if usb_mqtt_password else "")
    link_default = (_get_str(source, "LINK_DEFAULT", "usb") or "usb").lower()
    if link_default not in {"usb", "ttn"}:
        raise ValueError(f"LINK_DEFAULT must be 'usb' or 'ttn', got {link_default!r}")
    raw_link_file = _get_str(source, "LINK_STATE_FILE", "") or "link_state.json"
    link_state_file = Path(raw_link_file)
    if not link_state_file.is_absolute():
        link_state_file = (_BASE_DIR / link_state_file).resolve()

    return Settings(
        mqtt_api_key=mqtt_api_key,
        ttn_appid=ttn_appid,
        ttn_devid=ttn_devid,
        ttn_region=ttn_region,
        database_url=database_url,
        app_host=app_host,
        app_port=app_port,
        transport_mode=transport_mode,
        protocol_config_path=proto_path,
        dispatch_poll_sec=dispatch_poll_sec,
        scheduler_poll_sec=scheduler_poll_sec,
        schedule_timezone=schedule_timezone,
        token_ttl_hours=token_ttl_hours,
        demo_user_email=demo_user_email,
        demo_user_password_md5=demo_user_password_md5,
        device_name=device_name,
        device_dev_eui=device_dev_eui,
        ttn_tenant=ttn_tenant,
        ttn_mqtt_host=ttn_mqtt_host,
        ttn_mqtt_port=ttn_mqtt_port,
        ttn_mqtt_use_tls=ttn_mqtt_use_tls,
        ttn_mqtt_ca_certs=ttn_mqtt_ca_certs,
        sync_on_startup=sync_on_startup,
        protocol_version=protocol_version,
        handshake_timeout_sec=handshake_timeout_sec,
        app_root_path=app_root_path,
        usb_mqtt_host=usb_mqtt_host,
        usb_mqtt_port=usb_mqtt_port,
        usb_mqtt_username=usb_mqtt_username,
        usb_mqtt_password=usb_mqtt_password,
        link_default=link_default,
        link_state_file=link_state_file,
    )
