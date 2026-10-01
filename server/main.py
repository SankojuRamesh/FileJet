"""Signaling server: rendezvous + presence + TCP reflector. Never carries file data.

    P2P_CLOUD_JWT_SECRET=<same as the cloud> python -m server.main
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

from fastapi import FastAPI, WebSocket

from .config import Settings, load_settings
from .presence import PresenceHub
from .reflector import start_reflector
from .signaling import Hub

VERSION = "2.0.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    hub = Hub(settings)
    presence = PresenceHub(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        reflector = None
        if settings.reflector_port:
            try:
                reflector = await start_reflector(settings.host, settings.reflector_port)
            except OSError as exc:
                logging.getLogger("p2p.server").error("reflector disabled: %s", exc)
                settings.reflector_port = 0
        sweeper = asyncio.create_task(hub.sweep_forever())
        try:
            yield
        finally:
            sweeper.cancel()
            if reflector is not None:
                reflector.close()

    app = FastAPI(title="P2P signaling", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.hub, app.state.presence, app.state.settings = hub, presence, settings

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "version": VERSION, **hub.stats(), **presence.stats()}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await hub.handle(ws)

    @app.websocket("/ws/presence")
    async def presence_endpoint(ws: WebSocket):
        await presence.handle(ws)

    return app


def main(argv=None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="MediaRush signaling server (no file data)")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--reflector-port", type=int)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    if not settings.cloud_jwt_secret:
        sys.exit("P2P_CLOUD_JWT_SECRET is not set. Use the same value as the cloud app "
                 "(or start everything with: python run_dev.py).")
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    if args.reflector_port is not None:
        settings.reflector_port = args.reflector_port
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, ws_max_size=128 * 1024,
                log_level=args.log_level, proxy_headers=settings.trust_proxy_headers,
                ssl_certfile=settings.ssl_certfile or None, ssl_keyfile=settings.ssl_keyfile or None)


if __name__ == "__main__":
    main()
