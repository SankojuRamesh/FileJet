"""Shared GUI helpers: background work, settings persistence, styled widgets."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QTableWidget, QToolButton, QVBoxLayout, QWidget)

from ..config import ClientConfig
from ..util import fmt_bytes, fmt_duration, fmt_rate, parse_size
from . import icons
from .theme import C

log = logging.getLogger("p2p.gui")


# ------------------------------------------------------------ threading
class Bridge(QObject):
    """Queued signals: callables emitted from worker threads run on the GUI thread."""
    invoke = Signal(object)
    core_event = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.invoke.connect(lambda fn: fn(), Qt.QueuedConnection)


bridge: Bridge | None = None
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="gui-bg")


def init_bridge() -> Bridge:
    global bridge
    bridge = Bridge()
    return bridge


def run_bg(fn, ok=None, err=None) -> None:
    """Run ``fn`` in a worker thread; ``ok(result)`` / ``err(exc)`` run on the GUI thread."""
    def task():
        try:
            result = fn()
        except Exception as exc:          # noqa: BLE001 - shown to the user
            log.info("background task failed: %s", exc)
            bridge.invoke.emit(lambda exc=exc: (err or show_error)(exc))
        else:
            if ok is not None:
                bridge.invoke.emit(lambda result=result: ok(result))
    _pool.submit(task)


def show_error(exc, parent=None, title="Error") -> None:
    msg = str(exc)
    for prefix in ("permission denied: ",):
        if msg.startswith(prefix):
            title, msg = "Permission denied", msg[len(prefix):]
    QMessageBox.warning(parent, title, msg[:1500])


def confirm(parent, title: str, text: str, danger: bool = False) -> bool:
    box = QMessageBox(QMessageBox.Warning if danger else QMessageBox.Question, title, text,
                      QMessageBox.Yes | QMessageBox.No, parent)
    box.setDefaultButton(QMessageBox.No if danger else QMessageBox.Yes)
    return box.exec() == QMessageBox.Yes


def show_in_folder(path: Path) -> None:
    path = Path(path)
    try:
        if sys.platform == "win32":
            if path.is_dir():
                os.startfile(str(path))                       # noqa: S606
            else:
                subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
    except OSError as exc:
        show_error(exc)


def big_bar(width: int | None = None):
    """Progress bar that shows the sizes and percentage inside it."""
    from PySide6.QtWidgets import QProgressBar
    bar = QProgressBar()
    bar.setRange(0, 1000)
    bar.setProperty("bar", "big")
    bar.setTextVisible(True)
    if width:
        bar.setMinimumWidth(width)
    return bar


def set_bar(bar, done: int, total: int, state: str = "", text: str | None = None) -> None:
    """e.g.  4.2 GB / 10.7 GB  ·  39%   (green when completed, red when failed)."""
    pct = (done / total * 100) if total else (100.0 if state == "completed" else 0.0)
    bar.setValue(int(min(pct, 100) * 10))
    bar.setFormat(text if text is not None else f"{fmt_bytes(done)} / {fmt_bytes(total)}  ·  {pct:.0f}%")
    st = "completed" if state in ("completed", "delivered") else "failed" if state == "failed" else ""
    if bar.property("state") != st:
        bar.setProperty("state", st)
        bar.style().unpolish(bar)
        bar.style().polish(bar)


def sum_line(snaps: list[dict], verb: str) -> tuple[str, int, int]:
    """Totals of several running transfers: text, done bytes, total bytes."""
    total = sum(s["total"] for s in snaps)
    done = sum(s["transferred"] for s in snaps)
    speed = sum(s["speed"] for s in snaps)
    files = sum(s["files"] for s in snaps)
    eta = (total - done) / speed if speed > 0 and total > done else None
    took = max((s.get("elapsed") or 0) for s in snaps)
    text = (f"{verb} {len(snaps)} transfer{'s' if len(snaps) != 1 else ''} · {files} file(s) · "
            f"{fmt_bytes(done)} of {fmt_bytes(total)} · {fmt_bytes(total - done)} left · {fmt_rate(speed)}"
            + f" · {fmt_duration(took)} so far" + (f" · {fmt_duration(eta)} left" if eta else ""))
    return text, done, total


def perm_chips(perms: dict) -> str:
    """Rich text: View & download / Upload / Edit / Delete with a check or cross each."""
    out = []
    for text, key in (("View & download", "read"), ("Upload", "upload"), ("Edit", "edit"), ("Delete", "delete")):
        on = bool(perms.get(key))
        out.append(f"<span style='color:{C['ok'] if on else C['muted']}'>{'&#10004;' if on else '&#10008;'} {text}</span>")
    return "&nbsp;&nbsp;&nbsp;".join(out)


def open_file(path: Path) -> None:
    """Open a file with its default application."""
    path = Path(path)
    try:
        if not path.exists():
            raise FileNotFoundError(f"{path} no longer exists (moved or deleted)")
        if sys.platform == "win32":
            os.startfile(str(path))                           # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        show_error(exc)


def fmt_time(ts) -> str:
    import datetime as dt
    if not ts:
        return "-"
    if isinstance(ts, str):
        try:
            ts = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return ts[:16]
    return dt.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------ settings
SETTINGS_KEYS = ("cloud_url", "server_override", "download_dir", "streams", "max_streams", "chunk_size",
                 "rate_limit", "use_upnp", "use_stun", "allow_punch", "verify_full")


def default_data_dir(app: str) -> Path:
    env = os.environ.get("P2P_DATA_DIR")
    if env:
        return Path(env)
    old = Path.home() / ".p2p-transfer" / app
    return old if old.exists() else Path.home() / ".mediarush" / app


try:                                   # written by build_desktop.py --cloud-url ... --signal-url ...
    from ..build_info import CLOUD_URL as DEFAULT_CLOUD_URL
except ImportError:
    DEFAULT_CLOUD_URL = "http://filejet.live/"      # FileJet cloud (change with "Change" on the sign-in screen)
try:
    from ..build_info import SIGNAL_URL as DEFAULT_SIGNAL_URL
except ImportError:
    DEFAULT_SIGNAL_URL = ""                         # "" = the address the cloud announces
# addresses older builds saved as their default - an installed app moves to DEFAULT_CLOUD_URL
_OLD_DEFAULTS = ("http://127.0.0.1:8000/", "http://localhost:8000/", "https://iotgateway.live/",
                 "http://iotgateway.live/")                          # old domain -> filejet.live
_OLD_SIGNAL = ("ws://iotgateway.live:8765/ws", "ws://iotgateway.live/ws")


def load_settings(data_dir: Path) -> dict:
    frozen = getattr(sys, "frozen", False)               # the packaged FileJet.exe (not a source/dev run)
    s = {"cloud_url": os.environ.get("P2P_CLOUD_URL", DEFAULT_CLOUD_URL),
         "server_override": DEFAULT_SIGNAL_URL if frozen else "",
         "download_dir": str(Path.home() / "Downloads" / "FileJet"), "streams": 4, "max_streams": 8,
         "chunk_size": "Auto", "rate_limit": "", "use_upnp": True, "use_stun": True, "allow_punch": True,
         "verify_full": False}
    try:
        s.update({k: v for k, v in json.loads((data_dir / "settings.json").read_text()).items()
                  if k in SETTINGS_KEYS})
    except (OSError, ValueError):
        pass
    if getattr(sys, "frozen", False) and s.get("cloud_url") in _OLD_DEFAULTS and "P2P_CLOUD_URL" not in os.environ:
        s["cloud_url"] = DEFAULT_CLOUD_URL
    if s.get("server_override") in _OLD_SIGNAL:
        s["server_override"] = DEFAULT_SIGNAL_URL if frozen else ""   # old domain's signaling server
    if getattr(sys, "frozen", False) and DEFAULT_SIGNAL_URL and not s.get("server_override"):
        s["server_override"] = DEFAULT_SIGNAL_URL      # settings saved by an older build: use this build's server
    return s


def save_settings(data_dir: Path, s: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "settings.json").write_text(json.dumps({k: s.get(k) for k in SETTINGS_KEYS}, indent=2))


def apply_settings(cfg: ClientConfig, s: dict) -> None:
    cfg.streams = int(s.get("streams") or 4)
    cfg.max_streams = max(cfg.streams, int(s.get("max_streams") or 8))
    cs = s.get("chunk_size") or "Auto"
    cfg.chunk_size = None if cs == "Auto" else parse_size(cs)
    cfg.rate_limit = parse_size(s["rate_limit"]) if s.get("rate_limit") else None
    for k in ("use_upnp", "use_stun", "allow_punch", "verify_full"):
        setattr(cfg, k, bool(s.get(k)))
    cfg.dest_dir = Path(s.get("download_dir") or Path.home() / "Downloads")
    if s.get("server_override"):
        cfg.server_url = s["server_override"]


# ------------------------------------------------------------ widgets
def button(text: str, kind: str = "primary", icon_name: str | None = None, slot=None,
           small: bool = False) -> QPushButton:
    b = QPushButton(text)
    if kind != "primary":
        b.setProperty("kind", kind)
    if small:
        b.setProperty("btn", "small")
    if icon_name:
        b.setIcon(icons.icon(icon_name, "#ffffff" if kind == "primary" else C["text"], size=16))
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


def tool(icon_name: str, tip: str, slot=None) -> QToolButton:
    t = QToolButton()
    t.setProperty("kind", "tool")
    t.setIcon(icons.icon(icon_name, C["text"], "#ffffff", size=18))
    t.setToolTip(tip)
    t.setAutoRaise(True)
    t.setCursor(Qt.PointingHandCursor)
    if slot:
        t.clicked.connect(slot)
    return t


def label(text: str = "", name: str | None = None, muted: bool = False, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    if muted:
        lb.setProperty("muted", True)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def card(*widgets, layout=None) -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    lay = layout or QVBoxLayout()
    lay.setContentsMargins(16, 14, 16, 14)
    f.setLayout(lay)
    for w in widgets:
        lay.addWidget(w)
    return f


def table(headers: list[str], stretch: int = 0, fixed: dict | None = None, row_height: int = 36) -> QTableWidget:
    """``fixed``: {column: width} for columns holding buttons (cell widgets are not measured by Qt)."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(row_height)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setShowGrid(False)
    t.setAlternatingRowColors(False)
    t.setWordWrap(False)
    t.setFocusPolicy(Qt.StrongFocus)
    h = t.horizontalHeader()
    h.setHighlightSections(False)
    h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    h.setMinimumSectionSize(48)
    h.setSectionsMovable(False)
    skip = set(fixed or {}) | {stretch}
    for i in range(len(headers)):
        if fixed and i in fixed:
            h.setSectionResizeMode(i, QHeaderView.Fixed)
            t.setColumnWidth(i, fixed[i])
        elif i == stretch:
            h.setSectionResizeMode(i, QHeaderView.Stretch)      # takes the remaining width
        else:
            # like VS Code: drag the divider in the header to resize, double-click it to fit the content
            h.setSectionResizeMode(i, QHeaderView.Interactive)
            t.resizeColumnToContents(i)
            t.setColumnWidth(i, max(t.columnWidth(i) + 16, 64))

    def autosize_once():
        if getattr(t, "_autosized", False) or t.rowCount() == 0:
            return
        t._autosized = True
        for i in range(t.columnCount()):
            if i not in skip:
                t.resizeColumnToContents(i)
                t.setColumnWidth(i, min(max(t.columnWidth(i) + 12, 64), 300))
    from PySide6.QtCore import QTimer
    t.model().rowsInserted.connect(lambda *_a: QTimer.singleShot(0, autosize_once))
    return t


def status_color(state: str) -> str:
    return {"completed": C["ok"], "failed": C["bad"], "cancelled": C["muted"], "active": C["info"],
            "running": C["info"], "verifying": C["info"], "reconnecting": C["warn"], "paused": C["warn"],
            "awaiting_accept": C["warn"]}.get(state, "#c5c5c5")


STATE_TEXT = {"queued": "Queued", "running": "Transferring", "awaiting_accept": "Waiting for acceptance",
              "completed": "Completed", "failed": "Failed", "cancelled": "Cancelled", "active": "Transferring",
              "pending": "Connecting", "verifying": "Verifying", "reconnecting": "Reconnecting", "paused": "Paused"}


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


def page(title: str) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(32, 24, 32, 20)
    lay.setSpacing(12)
    if title:
        lay.addWidget(label(title, "PageTitle"))
    return w, lay


__all__ = ["fmt_bytes", "fmt_duration", "fmt_rate"]
