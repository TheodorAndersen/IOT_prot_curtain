"""Full-stack Docker Compose E2E deployment & security verification tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECT_NAME = "curtain-e2e-test"
NGINX_PORT = 18080
BASE_URL = f"http://127.0.0.1:{NGINX_PORT}"
PG_USER = "curtain"
PG_DB = "curtain"
PG_PASSWORD = "Test@Pass:2026#Cur!tain"
DEMO_MD5 = hashlib.md5(b"curtain").hexdigest()


def _run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"Command failed (rc={proc.returncode}): {' '.join(args)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def _compose(
    args: list[str],
    *,
    env: dict[str, str],
    check: bool = True,
    files: list[str] | None = None,
    project: str | None = PROJECT_NAME,
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose"]
    if project:
        cmd.extend(["-p", project])
    for f in files or ["docker-compose.yml"]:
        cmd.extend(["-f", f])
    cmd.extend(args)
    return _run(cmd, env=env, check=check)


def _psql(
    sql: str,
    *,
    env: dict[str, str],
    password: str = PG_PASSWORD,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _compose(
        [
            "exec",
            "-T",
            "-e",
            f"PGPASSWORD={password}",
            "postgres",
            "psql",
            "-h",
            "127.0.0.1",
            "-U",
            PG_USER,
            "-d",
            PG_DB,
            "-tAc",
            sql,
        ],
        env=env,
        check=check,
    )


def _parse_nginx_version(version_output: str) -> tuple[int, int, int]:
    match = re.search(r"nginx/(\d+)\.(\d+)\.(\d+)", version_output)
    assert match is not None, f"Could not parse nginx version from: {version_output!r}"
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


@pytest.mark.docker
def test_docker_full_stack_deployment_and_gcp_configs() -> None:
    compose_env = {
        **os.environ,
        "POSTGRES_USER": PG_USER,
        "POSTGRES_PASSWORD": PG_PASSWORD,
        "POSTGRES_DB": PG_DB,
        "TRANSPORT_MODE": "mock",
        "PROTOCOL_VERSION": "v4",
        "NGINX_HTTP_PORT": str(NGINX_PORT),
    }

    _compose(["down", "-v", "--remove-orphans"], env=compose_env, check=False)

    try:
        _compose(["up", "-d", "--build", "--wait"], env=compose_env)

        # ------------------------------------------------------------------
        # 1. Container isolation, security & software version checks
        # ------------------------------------------------------------------
        pg_cid = _compose(["ps", "-q", "postgres"], env=compose_env).stdout.strip()
        bk_cid = _compose(["ps", "-q", "backend"], env=compose_env).stdout.strip()
        nx_cid = _compose(["ps", "-q", "nginx"], env=compose_env).stdout.strip()
        assert pg_cid and bk_cid and nx_cid

        pg_info = json.loads(_run(["docker", "inspect", pg_cid]).stdout)[0]
        bk_info = json.loads(_run(["docker", "inspect", bk_cid]).stdout)[0]
        nx_info = json.loads(_run(["docker", "inspect", nx_cid]).stdout)[0]

        pg_ports: dict[str, Any] = pg_info["NetworkSettings"]["Ports"]
        assert not pg_ports.get("5432/tcp"), f"Postgres exposed to host: {pg_ports}"

        pg_networks: dict[str, Any] = pg_info["NetworkSettings"]["Networks"]
        assert len(pg_networks) == 1
        db_net_name = next(iter(pg_networks))
        db_net_info = json.loads(_run(["docker", "network", "inspect", db_net_name]).stdout)[0]
        assert db_net_info["Internal"] is True, "Postgres network must have Internal=true"

        nx_networks = set(nx_info["NetworkSettings"]["Networks"].keys())
        assert db_net_name not in nx_networks

        bk_ports = bk_info["NetworkSettings"]["Ports"]
        assert not bk_ports.get("8000/tcp"), f"Backend exposed to host: {bk_ports}"

        nx_bindings = nx_info["NetworkSettings"]["Ports"].get("80/tcp") or []
        assert len(nx_bindings) == 1
        assert nx_bindings[0]["HostIp"] == "127.0.0.1"
        assert nx_bindings[0]["HostPort"] == str(NGINX_PORT)

        nx_ver_proc = _compose(["exec", "-T", "nginx", "nginx", "-v"], env=compose_env)
        ver = _parse_nginx_version(nx_ver_proc.stderr + nx_ver_proc.stdout)
        assert ver >= (1, 31, 9) or ((1, 30, 7) <= ver < (1, 31, 0)), (
            f"Unsafe nginx version {ver}"
        )

        # ------------------------------------------------------------------
        # 2. PostgreSQL 19, custom password enforcement & real DDL/constraints
        # ------------------------------------------------------------------
        pg_ver = _psql("SHOW server_version;", env=compose_env).stdout.strip()
        assert pg_ver.startswith("19."), f"Expected PostgreSQL 19.x, got {pg_ver!r}"

        wrong_pw = _psql(
            "SELECT 1;", env=compose_env, password="wrong-password", check=False
        )
        assert wrong_pw.returncode != 0

        tables_raw = _psql(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;",
            env=compose_env,
        ).stdout.splitlines()
        assert set(tables_raw) == {
            "auth_tokens",
            "device_commands",
            "device_events",
            "device_settings",
            "devices",
            "downlink_transmissions",
            "light_telemetry",
            "users",
        }

        idx_exists = _psql(
            "SELECT COUNT(*) FROM pg_indexes WHERE indexname = 'uq_one_inflight_per_device';",
            env=compose_env,
        ).stdout.strip()
        assert idx_exists == "1"

        dev_uuid = _psql("SELECT id FROM devices LIMIT 1;", env=compose_env).stdout.strip()
        assert dev_uuid
        _psql(
            f"INSERT INTO device_commands "
            f"(device_id, command_kind, command_args, cmd_id, wire_fport, wire_payload_hex, status, attempts, sent_at) "
            f"VALUES ('{dev_uuid}', 'set_mode', '{{\"mode\":\"manual\"}}'::jsonb, 1, 1, '010200', 'IN_FLIGHT', 1, NOW());",
            env=compose_env,
        )
        dup_inflight = _psql(
            f"INSERT INTO device_commands "
            f"(device_id, command_kind, command_args, cmd_id, wire_fport, wire_payload_hex, status, attempts, sent_at) "
            f"VALUES ('{dev_uuid}', 'set_mode', '{{\"mode\":\"manual\"}}'::jsonb, 2, 1, '020200', 'IN_FLIGHT', 1, NOW());",
            env=compose_env,
            check=False,
        )
        assert dup_inflight.returncode != 0
        assert "uq_one_inflight_per_device" in dup_inflight.stderr
        _psql(
            f"DELETE FROM device_commands WHERE device_id = '{dev_uuid}' AND status = 'IN_FLIGHT';",
            env=compose_env,
        )

        # ------------------------------------------------------------------
        # 3. End-to-end React SPA & /api/* HTTP flows through Nginx
        # ------------------------------------------------------------------
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
            spa = client.get("/")
            assert spa.status_code == 200
            assert "text/html" in (spa.headers.get("content-type") or "")
            assert '<div id="root">' in spa.text
            # Client-side SPA route fallback (/debug, /settings) serves index.html
            assert '<div id="root">' in client.get("/debug").text

            health = client.get("/health")
            assert health.status_code == 200
            assert health.json() == {
                "ok": True,
                "transportConnected": True,
                "protocolVersion": "v4",
            }
            assert health.headers.get("x-content-type-options") == "nosniff"
            assert client.get("/api/health").json() == health.json()

            docs_resp = client.get("/api/docs")
            assert docs_resp.status_code == 200
            assert "/api/openapi.json" in docs_resp.text
            openapi_resp = client.get("/api/openapi.json")
            assert openapi_resp.status_code == 200
            openapi_json = openapi_resp.json()
            assert str(openapi_json.get("openapi", "")).startswith("3.")
            assert openapi_json.get("servers") == [{"url": "/api"}]

            unauth = client.get("/api/status")
            assert unauth.status_code == 401
            assert unauth.json() == {"error": "not logged in"}

            bad_login = client.post(
                "/api/login", json={"username": "demo@example.com", "password": "wrong"}
            )
            assert bad_login.status_code == 401

            login = client.post(
                "/api/login", json={"username": "demo@example.com", "password": DEMO_MD5}
            )
            assert login.status_code == 200
            token = login.json()["token"]
            headers = {"Authorization": f"Bearer {token}"}

            # Wait for MockTransport initial heartbeat + initial Type 3 automatic sync
            deadline = time.monotonic() + 5.0
            dbg_init: dict[str, Any] = {}
            while time.monotonic() < deadline:
                dbg_init = client.get("/api/debug?limit=20", headers=headers).json()
                if dbg_init["device"]["calibrated"] and dbg_init["device"]["settingsInSync"]:
                    break
                time.sleep(0.1)
            assert dbg_init["device"]["position"] == 50
            assert dbg_init["device"]["calibrated"] is True
            assert dbg_init["device"]["reportedMode"] == "automatic"
            assert dbg_init["device"]["settingsInSync"] is True
            assert dbg_init["budget"]["downlinksUsed24h"] == 1

            # Strict 400 validation checks
            for method, path, body in [
                ("POST", "/api/position", {"position": True}),
                (
                    "PUT",
                    "/api/auto",
                    {"autoOpen": 1, "autoOpenTime": "07:30", "autoClose": 0},
                ),
                (
                    "PUT",
                    "/api/settings",
                    {"outsideThresholdOpen": 40, "outsideThresholdClose": 40},
                ),
            ]:
                bad_req = client.request(method, path, json=body, headers=headers)
                assert bad_req.status_code == 400
                assert "error" in bad_req.json()

            # POST /api/position -> 35 -> confirmed in Postgres
            assert client.post("/api/position", json={"position": 35}, headers=headers).status_code == 200
            deadline = time.monotonic() + 5.0
            st: dict[str, Any] = {}
            while time.monotonic() < deadline:
                st = client.get("/api/status", headers=headers).json()
                if st["position"] == 35 and st["moving"] is False:
                    break
                time.sleep(0.1)
            assert st["position"] == 35

            # PUT /api/auto + PUT /api/settings while manual -> 0 DLs; PUT /api/mode automatic -> 1 consolidated v4 Type 3
            cmds_before_auto = int(
                _psql("SELECT COUNT(*) FROM device_commands;", env=compose_env).stdout.strip()
            )
            now_cph = datetime.now(ZoneInfo("Europe/Copenhagen"))
            open_hhmm = (now_cph - timedelta(hours=1)).strftime("%H:%M")
            close_hhmm = (now_cph + timedelta(hours=1)).strftime("%H:%M")
            assert (
                client.put(
                    "/api/auto",
                    json={
                        "autoOpen": 0,
                        "autoOpenTime": open_hhmm,
                        "autoClose": 0,
                        "autoCloseTime": close_hhmm,
                        "maintainLight": 1,
                        "targetLightLevel": 45,
                    },
                    headers=headers,
                ).status_code
                == 200
            )
            assert (
                client.put(
                    "/api/settings",
                    json={"outsideThresholdOpen": 85, "outsideThresholdClose": 20},
                    headers=headers,
                ).status_code
                == 200
            )
            time.sleep(0.2)
            cmds_after_manual_edits = int(
                _psql("SELECT COUNT(*) FROM device_commands;", env=compose_env).stdout.strip()
            )
            assert cmds_after_manual_edits == cmds_before_auto
            assert (
                client.put(
                    "/api/mode", json={"mode": "automatic"}, headers=headers
                ).status_code
                == 200
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                dbg_auto = client.get("/api/debug?limit=20", headers=headers).json()
                if (
                    dbg_auto["device"]["reportedMode"] == "automatic"
                    and dbg_auto["device"]["day"] is True
                    and dbg_auto["device"]["settingsInSync"] is True
                ):
                    break
                time.sleep(0.1)
            assert dbg_auto["device"]["reportedMode"] == "automatic"
            assert dbg_auto["device"]["day"] is True

            # Time-only PUT /api/auto that does NOT flip day/night -> 0 downlinks
            total_cmds_before = int(
                _psql("SELECT COUNT(*) FROM device_commands;", env=compose_env).stdout.strip()
            )
            new_close_hhmm = (now_cph + timedelta(hours=2)).strftime("%H:%M")
            assert (
                client.put(
                    "/api/auto",
                    json={
                        "autoOpen": 0,
                        "autoOpenTime": open_hhmm,
                        "autoClose": 0,
                        "autoCloseTime": new_close_hhmm,
                        "maintainLight": 1,
                        "targetLightLevel": 45,
                    },
                    headers=headers,
                ).status_code
                == 200
            )
            time.sleep(0.3)
            total_cmds_after = int(
                _psql("SELECT COUNT(*) FROM device_commands;", env=compose_env).stdout.strip()
            )
            assert total_cmds_after == total_cmds_before

            # v4 rule: POST /api/position while in automatic mode succeeds in 1 Type 1 downlink
            assert client.post("/api/position", json={"position": 10}, headers=headers).status_code == 200
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                st_p = client.get("/api/status", headers=headers).json()
                if st_p["position"] == 10 and st_p["mode"] == "manual" and st_p["moving"] is False:
                    break
                time.sleep(0.1)
            assert st_p["position"] == 10
            assert st_p["mode"] == "manual"

            # Verify /api/debug shows lastCommandSent + lastUplinkReceived
            dbg = client.get("/api/debug?limit=10", headers=headers).json()
            assert dbg["lastCommandSent"]["kind"] == "set_position"
            assert dbg["lastCommandSent"]["status"] == "CONFIRMED"
            assert dbg["lastCommandSent"]["wirePayloadHex"].endswith("010A")
            assert dbg["lastUplinkReceived"]["reason"] == "app_command"
            assert dbg["lastUplinkReceived"]["position"] == 10

            # POST /api/sync
            assert client.post("/api/sync", headers=headers).status_code == 200
            time.sleep(0.2)
            tx_count_24h = int(
                _psql(
                    "SELECT COUNT(*) FROM downlink_transmissions "
                    "WHERE sent_at > NOW() - INTERVAL '24 hours';",
                    env=compose_env,
                ).stdout.strip()
            )
            assert tx_count_24h >= 3

            # ------------------------------------------------------------------
            # 4. Persistence across backend container restart
            # ------------------------------------------------------------------
            _compose(["restart", "backend"], env=compose_env)
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                try:
                    hr = client.get("/health")
                    if hr.status_code == 200 and hr.json().get("transportConnected"):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)

            st_after = client.get("/api/status", headers=headers)
            assert st_after.status_code == 200
            assert st_after.json()["outsideThresholdOpen"] == 85
            assert st_after.json()["outsideThresholdClose"] == 20

        # ------------------------------------------------------------------
        # 5. GCP Production Compose & HTTPS Nginx template validation
        # ------------------------------------------------------------------
        prod_env = {
            **compose_env,
            "DOMAIN": "curtain.example.com",
            "CERTBOT_EMAIL": "admin@example.com",
        }
        prod_cfg_proc = _compose(
            ["config", "--format", "json"],
            env=prod_env,
            files=["docker-compose.yml", "deploy/gcp/docker-compose.prod.yml"],
            project=None,
        )
        prod_cfg = json.loads(prod_cfg_proc.stdout)
        assert "ports" not in prod_cfg["services"]["postgres"]
        assert "ports" not in prod_cfg["services"]["backend"]
        # USB link broker: only reachable through nginx /mqtt, never published on the host
        assert "ports" not in prod_cfg["services"]["mosquitto"]
        assert prod_cfg["services"]["backend"]["environment"]["USB_MQTT_HOST"] == "mosquitto"
        assert "certbot" in prod_cfg["services"]
        published_ports = {
            str(p["published"]) for p in prod_cfg["services"]["nginx"]["ports"]
        }
        assert published_ports == {"80", "443"}

        template_mount = (
            f"{(REPO_ROOT / 'deploy' / 'nginx' / 'default.https.conf.template').resolve()}"
            ":/etc/nginx/templates/default.conf.template:ro"
        )
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--add-host=backend:127.0.0.1",
                "-e",
                "DOMAIN=curtain.example.com",
                "-e",
                "NGINX_ENVSUBST_FILTER=DOMAIN",
                "-v",
                template_mount,
                "nginx:1.31.9-alpine",
                "sh",
                "-c",
                (
                    "(command -v openssl >/dev/null 2>&1 || apk add --no-cache openssl >/dev/null) && "
                    "mkdir -p /etc/letsencrypt/live/curtain.example.com /var/www/certbot /usr/share/nginx/html && "
                    "openssl req -x509 -nodes -newkey rsa:2048 -days 1 "
                    "-subj /CN=curtain.example.com "
                    "-keyout /etc/letsencrypt/live/curtain.example.com/privkey.pem "
                    "-out /etc/letsencrypt/live/curtain.example.com/fullchain.pem 2>/dev/null && "
                    "/docker-entrypoint.d/20-envsubst-on-templates.sh && "
                    "nginx -t"
                ),
            ]
        )
    finally:
        _compose(["down", "-v", "--remove-orphans"], env=compose_env, check=False)
