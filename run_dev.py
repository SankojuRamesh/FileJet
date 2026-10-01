"""Start everything needed on ONE computer for development/testing:

    python run_dev.py            # cloud (http://127.0.0.1:8000) + signaling server (ws://127.0.0.1:8765)
    python run_dev.py --apps     # ... and also open the desktop app twice (profiles 'one' and 'two')

Both servers get the same P2P_CLOUD_JWT_SECRET, so sign-ins work. Press Ctrl+C to stop.
To test on one PC, sign in to the two app windows with two DIFFERENT accounts
(one account can be online in only one app at a time).
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEV_SECRET = "dev-signal-secret-change-me-0123456789abcdef"      # the cloud's DEBUG default


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if port_in_use(port):
            return True
        time.sleep(0.2)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apps", action="store_true", help="also open the desktop app twice")
    ap.add_argument("--cloud-port", type=int, default=8000)
    ap.add_argument("--signal-port", type=int, default=8765)
    args = ap.parse_args()

    for port, what in ((args.cloud_port, "cloud"), (args.signal_port, "signaling server")):
        if port_in_use(port):
            print(f"Port {port} is already in use - is the {what} already running? Stop it or pick another port.")
            return 1
    secret = os.environ.get("P2P_CLOUD_JWT_SECRET", DEV_SECRET)
    env = dict(os.environ, P2P_CLOUD_JWT_SECRET=secret,
               P2P_SIGNALING_URL=f"ws://127.0.0.1:{args.signal_port}/ws", PYTHONUNBUFFERED="1")
    procs = []
    try:
        print("Preparing the cloud database ...", flush=True)
        subprocess.run([sys.executable, "manage.py", "migrate", "-v", "0"], cwd=ROOT / "cloud", env=env, check=True)
        procs.append(subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{args.cloud_port}",
                                       "--noreload"], cwd=ROOT / "cloud", env=env))
        procs.append(subprocess.Popen([sys.executable, "-m", "server.main", "--host", "127.0.0.1", "--port",
                                       str(args.signal_port), "--log-level", "warning"], cwd=ROOT, env=env))
        ok = wait_port(args.cloud_port) and wait_port(args.signal_port)
        if not ok:
            print("A server did not start - see the messages above.")
            return 1
        print(f"\n  Cloud (web dashboard, accounts):  http://127.0.0.1:{args.cloud_port}/")
        print(f"  Signaling server:                  ws://127.0.0.1:{args.signal_port}/ws")
        print("  Sign in to the two app windows with two different accounts.\n"
              "  E-mails (folder IDs) are shown on the web under Inbox.\n  Ctrl+C stops everything.\n",
              flush=True)
        if args.apps:
            for profile in ("one", "two"):
                procs.append(subprocess.Popen([sys.executable, "filejet.py", "--profile", profile], cwd=ROOT,
                                              env=dict(env, P2P_CLOUD_URL=f"http://127.0.0.1:{args.cloud_port}/")))
        while all(p.poll() is None for p in procs[:2]):
            time.sleep(0.5)
        print("A server stopped unexpectedly.")
        return 1
    except KeyboardInterrupt:
        print("\nStopping ...")
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    sys.exit(main())
