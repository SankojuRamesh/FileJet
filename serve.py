"""FileJet server without Docker - the cloud and the signaling server in ONE process (Windows and Linux).

    python serve.py --env server.env            # start (uses the settings file written by the installer)
    python serve.py --env server.env --check    # validate settings + database, then exit
    python serve.py --env server.env manage createsuperuser    # any Django command with the settings loaded

* Cloud (Django, web dashboard + API) is served by waitress on CLOUD_PORT (default 8000).
* Signaling server (+ TCP reflector for hole punching) on SIGNAL_PORT / REFLECTOR_PORT (8765 / 8766).
* On every start the database is migrated and static files are collected (both are quick and idempotent).
* If either part stops, the process exits with an error so the service manager restarts it.

File data never passes through this server.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("mediarush.serve")


def load_env_file(path: Path) -> None:
    """KEY=VALUE lines (comments with #). Values from the real environment win."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def check_settings() -> None:
    problems = []
    for key in ("DJANGO_SECRET_KEY", "P2P_CLOUD_JWT_SECRET"):
        v = os.environ.get(key, "")
        if len(v) < 32 or v.startswith("change-me"):
            problems.append(f"{key} must be a random value of 32+ characters")
    if problems:
        sys.exit("Settings problem:\n  - " + "\n  - ".join(problems))


def setup_django():
    sys.path.insert(0, str(ROOT / "cloud"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.core.management import call_command
    call_command("migrate", interactive=False, verbosity=0)
    call_command("collectstatic", interactive=False, verbosity=0)
    from config.wsgi import application
    return application


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FileJet server (cloud + signaling), no Docker needed")
    ap.add_argument("--env", default=str(ROOT / "server.env"), help="settings file (default: server.env)")
    ap.add_argument("--host", default=None, help="listen address (default HOST from the env file, else 0.0.0.0)")
    ap.add_argument("--check", action="store_true", help="validate settings and database, then exit")
    ap.add_argument("--log-level", default="info")
    ap.add_argument("manage", nargs=argparse.REMAINDER, help="manage <django command ...>")
    args = ap.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    env = Path(args.env)
    if not env.is_file():
        sys.exit(f"Settings file not found: {env}\nRun the installer in deploy/ or copy deploy/server.env.example.")
    load_env_file(env)
    os.environ.setdefault("DJANGO_DEBUG", "0")
    check_settings()

    host = args.host or os.environ.get("HOST", "0.0.0.0")
    cloud_port = int(os.environ.get("CLOUD_PORT", 8000))
    signal_port = int(os.environ.get("SIGNAL_PORT", 8765))
    reflector_port = int(os.environ.get("REFLECTOR_PORT", 8766))
    cloud_host = os.environ.get("CLOUD_HOST", host)

    if args.manage:
        if args.manage[0] != "manage" or len(args.manage) < 2:
            sys.exit("usage: serve.py --env server.env manage <command> [args]")
        sys.path.insert(0, str(ROOT / "cloud"))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        from django.core.management import execute_from_command_line
        execute_from_command_line(["manage.py", *args.manage[1:]])
        return 0

    application = setup_django()
    log.info("database and static files ready")
    if args.check:
        print("OK - settings and database are fine")
        return 0

    # ---- cloud (waitress, own thread)
    from waitress import create_server
    cloud = create_server(application, host=cloud_host, port=cloud_port, threads=int(os.environ.get("THREADS", 8)),
                          ident="FileJet", trusted_proxy=os.environ.get("TRUSTED_PROXY", "127.0.0.1"),
                          trusted_proxy_headers={"x-forwarded-proto", "x-forwarded-for"}, clear_untrusted_proxy_headers=True)

    def run_cloud():
        try:
            cloud.run()
        except Exception:                     # noqa: BLE001
            log.exception("cloud stopped")
        finally:
            if not stopping.is_set():
                log.error("cloud stopped unexpectedly - exiting so the service restarts")
                os._exit(1)

    stopping = threading.Event()
    threading.Thread(target=run_cloud, name="cloud", daemon=True).start()
    log.info("cloud on http://%s:%d/", cloud_host, cloud_port)

    # ---- signaling (uvicorn, main thread)
    import uvicorn

    from server.config import load_settings
    from server.main import create_app
    settings = load_settings()
    settings.host, settings.port, settings.reflector_port = host, signal_port, reflector_port
    log.info("signaling on ws://%s:%d/ws, reflector on tcp %d", host, signal_port, reflector_port)
    try:
        uvicorn.run(create_app(settings), host=host, port=signal_port, ws_max_size=128 * 1024,
                    log_level=args.log_level, proxy_headers=settings.trust_proxy_headers,
                    forwarded_allow_ips=os.environ.get("TRUSTED_PROXY", "127.0.0.1"))
    finally:
        stopping.set()
        cloud.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
