"""FastAPI application entrypoint, lifespan management, and REST routes."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import Settings, load_settings
from db import Database
from device_manager import (
    BadRequestError,
    ConflictError,
    DeviceManager,
    UnauthorizedError,
)
from models import (
    AutoRequest,
    DebugResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    ModeRequest,
    OkResponse,
    PositionRequest,
    SettingsRequest,
    StatusResponse,
    TelemetryResponse,
)
from protocol import ProtocolCodec
from transport import LoRaTransport, MockTransport, TTNMqttTransport

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def _format_validation_error(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = [str(item) for item in err.get("loc", ()) if item != "body"]
        msg = str(err.get("msg", "invalid request"))
        if loc:
            parts.append(f"{'.'.join(loc)}: {msg}")
        else:
            parts.append(msg)
    return "; ".join(parts) if parts else "invalid request"


def create_app(
    settings: Settings | None = None,
    db: Any = None,
    transport: LoRaTransport | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active_settings = settings if settings is not None else load_settings()
        codec = ProtocolCodec.from_file(active_settings.protocol_config_path)
        if active_settings.handshake_timeout_sec is not None:
            codec = codec.with_handshake_timeout(active_settings.handshake_timeout_sec)

        owns_db = db is None
        active_db = db if db is not None else Database(active_settings.database_url)
        if owns_db:
            await active_db.connect()
        await active_db.init_schema()
        await active_db.seed_defaults(active_settings, codec)

        if transport is not None:
            active_transport = transport
        elif active_settings.transport_mode == "mock":
            active_transport = MockTransport(
                codec=codec, dev_id=active_settings.ttn_devid
            )
        else:
            active_transport = TTNMqttTransport(
                ttn_appid=active_settings.ttn_appid,
                mqtt_api_key=active_settings.mqtt_api_key,
                ttn_region=active_settings.ttn_region,
                ttn_tenant=active_settings.ttn_tenant,
                mqtt_host=active_settings.ttn_mqtt_host,
                mqtt_port=active_settings.ttn_mqtt_port,
                mqtt_use_tls=active_settings.ttn_mqtt_use_tls,
                mqtt_ca_certs=active_settings.ttn_mqtt_ca_certs,
            )

        manager = DeviceManager(
            settings=active_settings,
            codec=codec,
            db=active_db,
            transport=active_transport,
        )
        app.state.settings = active_settings
        app.state.codec = codec
        app.state.db = active_db
        app.state.manager = manager

        await manager.start()
        try:
            yield
        finally:
            await manager.stop()
            if owns_db:
                await active_db.close()

    raw_root = (
        settings.app_root_path
        if settings is not None
        else os.environ.get("APP_ROOT_PATH", "").strip().rstrip("/")
    )
    root_path = raw_root if not raw_root or raw_root.startswith("/") else f"/{raw_root}"

    application = FastAPI(
        title="IoT Smart Curtain Backend",
        root_path=root_path,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(RequestValidationError)
    async def _validation_exc_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"error": _format_validation_error(exc)}
        )

    @application.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"error": str(exc.detail)}
        )

    @application.exception_handler(ConflictError)
    async def _conflict_exc_handler(
        request: Request, exc: ConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": str(exc)})

    @application.exception_handler(UnauthorizedError)
    async def _unauthorized_exc_handler(
        request: Request, exc: UnauthorizedError
    ) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": str(exc)})

    @application.exception_handler(BadRequestError)
    async def _bad_request_exc_handler(
        request: Request, exc: BadRequestError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    def get_manager(request: Request) -> DeviceManager:
        return request.app.state.manager

    async def require_user(
        request: Request,
        manager: DeviceManager = Depends(get_manager),
    ) -> uuid.UUID:
        auth = request.headers.get("authorization")
        if not auth:
            raise UnauthorizedError("not logged in")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise UnauthorizedError("not logged in")
        return await manager.authenticate_token(token.strip())

    @application.get("/health", response_model=HealthResponse)
    async def health(manager: DeviceManager = Depends(get_manager)) -> HealthResponse:
        return HealthResponse(
            ok=True,
            transportConnected=manager.transport.is_connected,
            protocolVersion=manager.codec.spec.version,
        )

    @application.post("/login", response_model=LoginResponse)
    async def login(
        body: LoginRequest, manager: DeviceManager = Depends(get_manager)
    ) -> LoginResponse:
        token = await manager.login(body.username, body.password)
        return LoginResponse(token=token)

    @application.get("/status", response_model=StatusResponse)
    async def get_status(
        user_id: uuid.UUID = Depends(require_user),
        manager: DeviceManager = Depends(get_manager),
    ) -> StatusResponse:
        return await manager.get_status(user_id)

    @application.post("/position", response_model=OkResponse)
    async def post_position(
        body: PositionRequest,
        user_id: uuid.UUID = Depends(require_user),
        manager: DeviceManager = Depends(get_manager),
    ) -> OkResponse:
        await manager.set_position(user_id, body.position)
        return OkResponse()

    @application.put("/mode", response_model=OkResponse)
    async def put_mode(
        body: ModeRequest,
        user_id: uuid.UUID = Depends(require_user),
        manager: DeviceManager = Depends(get_manager),
    ) -> OkResponse:
        await manager.set_mode(user_id, body.mode)
        return OkResponse()

    @application.post("/sync", response_model=OkResponse)
    async def post_sync(
        user_id: uuid.UUID = Depends(require_user),
        manager: DeviceManager = Depends(get_manager),
    ) -> OkResponse:
        await manager.sync_device(user_id)
        return OkResponse()

    @application.put("/auto", response_model=OkResponse)
    async def put_auto(
        body: AutoRequest,
        user_id: uuid.UUID = Depends(require_user),
        manager: DeviceManager = Depends(get_manager),
    ) -> OkResponse:
        await manager.set_auto(user_id, body)
        return OkResponse()

    @application.put("/settings", response_model=OkResponse)
    async def put_settings(
        body: SettingsRequest,
        user_id: uuid.UUID = Depends(require_user),
        manager: DeviceManager = Depends(get_manager),
    ) -> OkResponse:
        await manager.set_settings(user_id, body)
        return OkResponse()

    @application.get("/debug", response_model=DebugResponse)
    async def get_debug(
        limit: int = Query(default=20),
        user_id: uuid.UUID = Depends(require_user),
        manager: DeviceManager = Depends(get_manager),
    ) -> DebugResponse:
        return await manager.get_debug(user_id, limit=limit)

    @application.get("/telemetry", response_model=TelemetryResponse)
    async def get_telemetry(
        hours: int = Query(default=24),
        user_id: uuid.UUID = Depends(require_user),
        manager: DeviceManager = Depends(get_manager),
    ) -> TelemetryResponse:
        return await manager.get_telemetry(user_id, hours)

    return application


app = create_app()
