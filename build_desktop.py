"""Build the standalone desktop app with PyInstaller (run on each target OS).

    pip install -r requirements.txt pyinstaller
    python build_desktop.py                                        # cloud: http://iotgateway.live/
    python build_desktop.py --cloud-url http://192.168.1.20:8000/  # a build for another server
    python build_desktop.py --signal-url ""                        # signaling address: ask the cloud (automatic)

Produces dist/FileJet(.exe) with the cloud URL built in (users can still change it on the sign-in screen).
The cloud (Django) and the signaling server are server-side and are NOT bundled.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_CLOUD_URL = "http://iotgateway.live/"
# Signaling server the app connects to. Empty = use what the cloud announces (GET /api/config/).
DEFAULT_SIGNAL_URL = "ws://13.204.80.52:8765/ws"
_QT_APP = None
EXCLUDE = ["server", "cloud", "django", "rest_framework", "fastapi", "starlette", "uvicorn", "pydantic",
           "httpx", "pytest", "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
           "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtPdf"]


def write_build_info(cloud_url: str, signal_url: str = "") -> None:
    """client/build_info.py is read by the app for its default cloud and signaling URLs."""
    url = cloud_url.strip().rstrip("/") + "/"
    if not url.startswith(("http://", "https://")):
        sys.exit(f"--cloud-url must start with http:// or https:// (got {cloud_url!r})")
    sig = signal_url.strip()
    if sig and not sig.startswith(("ws://", "wss://")):
        sys.exit(f"--signal-url must start with ws:// or wss:// (got {signal_url!r})")
    (ROOT / "client" / "build_info.py").write_text(
        '"""Written by build_desktop.py - the servers this build connects to by default."""\n'
        f"CLOUD_URL = {url!r}\n"
        f"SIGNAL_URL = {sig!r}      # empty = use what the cloud announces\n", encoding="utf-8")
    print(f"cloud URL built in:     {url}")
    print(f"signaling URL built in: {sig or '(automatic, from the cloud)'}")


def make_icon() -> Path | None:
    """FileJet logo as build/mediarush.ico (Windows exe) and build/mediarush.png (Linux app menu)."""
    try:
        sys.path.insert(0, str(ROOT))
        from PySide6.QtGui import QGuiApplication
        global _QT_APP                              # keep the Qt app alive while rendering the icon
        _QT_APP = QGuiApplication.instance() or QGuiApplication(["build"])
        from client.gui import icons
        out = ROOT / "build" / "mediarush.ico"
        out.parent.mkdir(exist_ok=True)
        pixmap = icons.brand_mark(256).pixmap(256, 256)
        pixmap.save(str(out.with_suffix(".png")), "PNG")
        if pixmap.save(str(out), "ICO"):
            return out
    except Exception as exc:                       # noqa: BLE001 - an icon must never break the build
        print(f"(no icon: {exc})")
    return None


def build(name: str = "FileJet", entry: str = "filejet.py") -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--name", name,
           "--collect-submodules", "client"]
    icon = make_icon()
    if icon:
        cmd += ["--icon", str(icon)]
    for mod in EXCLUDE:
        cmd += ["--exclude-module", mod]
    cmd.append(entry)
    print(" ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=ROOT)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the FileJet desktop app")
    ap.add_argument("--cloud-url", default=DEFAULT_CLOUD_URL, help=f"cloud the app uses by default ({DEFAULT_CLOUD_URL})")
    ap.add_argument("--signal-url", default=DEFAULT_SIGNAL_URL,
                    help=f'signaling server, "" = from the cloud (default {DEFAULT_SIGNAL_URL})')
    args = ap.parse_args()
    write_build_info(args.cloud_url, args.signal_url)
    build()
    print("\nDone: see dist/")
