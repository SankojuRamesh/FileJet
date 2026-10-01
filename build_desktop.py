"""Build the standalone desktop app with PyInstaller (run on each target OS).

    pip install -r requirements.txt pyinstaller
    python build_desktop.py

Produces dist/MediaRush(.exe).
The cloud (Django) and the signaling server are server-side and are NOT bundled.
"""
import subprocess
import sys

EXCLUDE = ["server", "cloud", "django", "rest_framework", "fastapi", "starlette", "uvicorn", "pydantic",
           "httpx", "pytest", "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
           "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtPdf"]


def build(name: str = "MediaRush", entry: str = "mediarush.py") -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--name", name,
           "--collect-submodules", "client"]
    for mod in EXCLUDE:
        cmd += ["--exclude-module", mod]
    cmd.append(entry)
    print(" ".join(cmd), flush=True)
    subprocess.check_call(cmd)


if __name__ == "__main__":
    build()
    print("\nDone: see dist/")
