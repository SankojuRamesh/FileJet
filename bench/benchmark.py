"""Throughput benchmark through the real stack: cloud sign-in, signaling, presence, E2E RPC,
TLS 1.3 parallel streams, chunk hashing, fsync'ed writes and verification.

It starts a private cloud + signaling server, creates two accounts ("owner" shares a folder,
"sender" uploads into it) and measures each upload:

    python bench/benchmark.py --size 2G
    python bench/benchmark.py --size 1G --streams 1,4,8 --chunk-sizes 4M,8M,16M

On one machine this measures the application's own ceiling (loopback), not your network.
For WAN-like conditions run bench/netem.sh on Linux first.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.core import AppCore  # noqa: E402
from client.util import fmt_bytes, parse_size  # noqa: E402
from tests.conftest import free_port, make_cfg, write_random_file  # noqa: E402


def wait_until(pred, timeout, msg):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise SystemExit(f"timed out: {msg}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", default="1G")
    ap.add_argument("--streams", default="4")
    ap.add_argument("--chunk-sizes", default="8M")
    ap.add_argument("--out", default=str(ROOT / "bench" / "results.json"))
    ap.add_argument("--dest-dir", help="receive onto another disk (closer to two real computers)")
    ap.add_argument("--fsync-mb", type=int, default=0, help="receiver flush size (default: app default)")
    ap.add_argument("--trace", type=float, default=0, help="print progress every N seconds")
    args = ap.parse_args()

    import socket

    import uvicorn

    from server.config import Settings
    from server.main import create_app

    tmp = Path(tempfile.mkdtemp(prefix="p2pbench-"))
    secret = secrets.token_urlsafe(32)
    sig_port, cloud_port = free_port(), free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(Settings(host="127.0.0.1", port=sig_port, reflector_port=0,
                                                               stun_servers=[], cloud_jwt_secret=secret)),
                                           host="127.0.0.1", port=sig_port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    env = dict(os.environ, DJANGO_SQLITE_PATH=str(tmp / "cloud.sqlite3"), P2P_CLOUD_JWT_SECRET=secret,
               P2P_SIGNALING_URL=f"ws://127.0.0.1:{sig_port}/ws", DJANGO_DEBUG="1",
               THROTTLE_REGISTER="1000/min", THROTTLE_LOGIN="1000/min")
    subprocess.run([sys.executable, "manage.py", "migrate", "-v", "0"], cwd=ROOT / "cloud", env=env, check=True)
    cloud = subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{cloud_port}", "--noreload"],
                             cwd=ROOT / "cloud", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def up(port):
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            return True
        except OSError:
            return False
    results = []
    try:
        wait_until(lambda: up(cloud_port) and up(sig_port), 30, "servers")
        url = f"http://127.0.0.1:{cloud_port}/"
        owner = AppCore(make_cfg("ws://unused/ws", tmp / "owner"), url)
        owner.register("owner", "owner@example.com", "Bench-pass-123")
        owner.cloud.request("POST", "api/billing/subscription/", {"plan": "business"})
        sender = AppCore(make_cfg("ws://unused/ws", tmp / "sender"), url)
        sender.register("sender", "sender@example.com", "Bench-pass-123")
        dest = Path(args.dest_dir) / f"p2pbench-shared-{secrets.token_hex(3)}" if args.dest_dir else tmp / "Shared"
        dest.mkdir(parents=True)
        fid = owner.create_folder("Shared", dest)
        owner.add_user(sender.me["public_id"])
        owner.set_member(fid, sender.me["public_id"], role="uploader")
        sender.join_folder(fid)
        o_uid = owner.me["public_id"]
        wait_until(lambda: sender.is_online(o_uid) and sender.remote_shares(o_uid), 15, "folder visible")
        size = parse_size(args.size)
        src = tmp / "payload.bin"
        print(f"writing {fmt_bytes(size)} test file ...", flush=True)
        write_random_file(src, size)
        with open(src, "rb+") as fh:              # flush the test file first: measure the transfer, not the
            os.fsync(fh.fileno())                 # OS still writing our freshly created test data to disk
        time.sleep(5)
        for streams in map(int, args.streams.split(",")):
            for chunk in (parse_size(c) for c in args.chunk_sizes.split(",")):
                for core in (owner, sender):
                    core.cfg.streams, core.cfg.max_streams, core.cfg.chunk_size = streams, max(8, streams), chunk
                    core.cfg.fsync_interval = 2.0                      # the app's defaults, not the tests'
                    if args.fsync_mb:
                        core.cfg.fsync_bytes = args.fsync_mb << 20
                name = f"payload-{streams}x{chunk >> 20}M.bin"
                run_src = tmp / name
                os.replace(src, run_src)
                t0 = time.time()
                job = sender.upload(o_uid, fid, "Shared", "", [run_src])
                while not job.done.wait(args.trace or 3600):
                    snap = job.snapshot()
                    print(f"  t={time.time() - t0:6.1f}s  {snap['transferred'] / 1e9:6.2f} GB  "
                          f"{snap['speed'] / 1e6:7.1f} MB/s", flush=True)
                dt = time.time() - t0
                snap = job.items[0].engine.snapshot() if job.items[0].engine else {}
                os.replace(run_src, src)
                (dest / name).unlink(missing_ok=True)
                r = {"size": size, "streams": streams, "chunk": chunk, "state": job.state, "seconds": round(dt, 2),
                     "avg_MBps": round(size / dt / 1e6, 1), "peak_MBps": round((snap.get("peak") or 0) / 1e6, 1),
                     "connection": snap.get("connection")}
                results.append(r)
                print(json.dumps(r), flush=True)
        owner.shutdown()
        sender.shutdown()
    finally:
        cloud.terminate()
        server.should_exit = True
    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
