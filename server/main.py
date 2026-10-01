"""Signaling server: rendezvous + presence + TCP reflector. Never carries file data.

    P2P_CLOUD_JWT_SECRET=<same as the cloud> python -m server.main     (from the project folder)
    P2P_CLOUD_JWT_SECRET=<same as the cloud> python main.py            (from inside server/ also works)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

if __name__ == "__main__" and not __package__:     # started as "python main.py" inside server/
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "server"                          # noqa: A001 - lets the relative imports below work

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


ENV_FILE = __import__("pathlib").Path(__file__).resolve().parent.parent / "server.env"


def load_env_file(path) -> None:
    """KEY=VALUE lines (# comments). Values already in the environment win."""
    import os
    from pathlib import Path
    f = Path(path)
    if not f.is_file():
        return
    for raw in f.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    logging.getLogger("p2p.server").info("settings loaded from %s", f)


def main(argv=None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="MediaRush signaling server (no file data)")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--reflector-port", type=int)
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--env", default=str(ENV_FILE),
                        help="settings file with P2P_CLOUD_JWT_SECRET=... (default: server.env in the project folder)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env_file(args.env)
    settings = load_settings()
    if not settings.cloud_jwt_secret:
        sys.exit("P2P_CLOUD_JWT_SECRET is not set - it must be the same value as the cloud app (Django).\n"
                 f"  Put this line into {args.env}:\n"
                 "      P2P_CLOUD_JWT_SECRET=<same value as the cloud app>\n"
                 "  or set it in this window first:  $env:P2P_CLOUD_JWT_SECRET = \"...\"\n"
                 "  or start cloud + signaling together:  python run_dev.py")
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
