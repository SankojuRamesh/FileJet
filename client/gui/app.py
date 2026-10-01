"""Main window of the FileJet desktop app (Qt, modern dark UI).

One app does everything: share your own folders with users (by ID, with permissions), and work in the
folders other people shared with you - send files into them, download, edit, delete as permitted."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..config import ClientConfig
from ..core import AppCore
from ..util import fmt_rate
from . import icons
from .common import (apply_settings, button, default_data_dir, init_bridge, label, load_settings,
                     save_settings)
from .login import LoginDialog
from .theme import C, QSS

log = logging.getLogger("p2p.gui")

APPS = {
    "app": {"title": "FileJet", "pages": [("folders", "share", "My Folders"),
                                                ("myfolders", "remote", "Shared with me"),
                                                ("users", "contacts", "Users"),
                                                ("chat", "chat", "Chat"),
                                                ("transfers", "transfers", "Transfers")]},
}


class MainWindow(QMainWindow):
    def __init__(self, core: AppCore, app_kind: str, settings: dict, data_dir: Path, bridge):
        super().__init__()
        self.core, self.kind, self.settings, self.data_dir, self.bridge = core, app_kind, settings, data_dir, bridge
        self.signed_out = False
        meta = APPS[app_kind]
        self.setWindowTitle(meta["title"])
        self.setMinimumSize(900, 560)
        from .sash import LayoutStore
        self.layout_store = LayoutStore()          # remembered pane sizes + window geometry (per profile)
        self.layout_store.load(data_dir)
        geo = self.layout_store.get("window")
        restored = False
        if isinstance(geo, str):
            from PySide6.QtCore import QByteArray
            restored = self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        if not restored:
            self.resize(1360, 860)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        root.addLayout(body, 1)

        # ---- activity bar
        self.activity = QWidget()
        self.activity.setObjectName("ActivityBar")
        self.activity.setFixedWidth(58)
        al = QVBoxLayout(self.activity)
        al.setContentsMargins(0, 6, 0, 8)
        al.setSpacing(2)
        brand = QLabel()
        brand.setObjectName("Brand")
        brand.setPixmap(icons.brand_mark(30).pixmap(30, 30))
        brand.setAlignment(Qt.AlignHCenter)
        brand.setToolTip("FileJet")
        al.addWidget(brand)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.buttons: dict[str, QToolButton] = {}
        entries = meta["pages"] + [None, ("account", "account", "Account"), ("settings", "settings", "Settings")]
        for entry in entries:
            if entry is None:
                al.addStretch(1)
                continue
            key, ic, tip = entry
            b = QToolButton()
            b.setIcon(icons.icon(ic, C["activity_icon"], C["activity_active"]))
            b.setIconSize(QSize(22, 22))
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key, btn=b: self._activity_clicked(k))
            self.group.addButton(b)
            self.buttons[key] = b
            al.addWidget(b)
        body.addWidget(self.activity)
        right = QVBoxLayout()
        right.setSpacing(0)
        right.setContentsMargins(0, 0, 0, 0)
        # banner explaining WHY the app is offline (server not running, secret mismatch, same account twice...)
        self.banner = QWidget()
        self.banner.setObjectName("Banner")
        self.banner_text = label("", wrap=True)
        self.banner_btn = button("Reconnect here", "secondary", "refresh", self._reconnect, small=True)
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(16, 8, 16, 8)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_btn)
        self.banner.setStyleSheet(f"#Banner {{ background: #2a1719; border-bottom: 1px solid #4a2427; }} "
                                  f"#Banner QLabel {{ color: #ffb4ae; }}")
        self.banner.hide()
        right.addWidget(self.banner)
        right.addWidget(self.stack, 1)
        body.addLayout(right, 1)

        # ---- status bar
        sb = QWidget()
        sb.setObjectName("StatusBar")
        sb.setFixedHeight(28)
        sl = QHBoxLayout(sb)
        sl.setContentsMargins(4, 0, 4, 0)
        sl.setSpacing(0)
        self.st_conn = QLabel()
        self.st_user = QLabel()
        self.st_msg = QLabel()
        self.st_speed = QLabel()
        self.st_meta = QLabel("")
        for w in (self.st_conn, self.st_user, self.st_msg):
            sl.addWidget(w)
        sl.addStretch(1)
        sl.addWidget(self.st_speed)
        sl.addWidget(self.st_meta)
        root.addWidget(sb)
        self.setCentralWidget(central)

        self._build_pages()
        self.go(meta["pages"][0][0])
        self.bridge.core_event.connect(self._on_core_event)
        from PySide6.QtGui import QKeySequence, QShortcut
        for seq, fn in (("Ctrl+B", "toggle_sidebar"), ("Ctrl+J", "toggle_panel")):
            QShortcut(QKeySequence(seq), self, activated=lambda f=fn: self._current_page_call(f))
        for i, (key, _ic, _t) in enumerate(p for p in meta["pages"] if p):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda k=key: self.go(k))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(700)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(lambda: self.st_msg.setText(""))
        self._update_status()

    def _build_pages(self):
        from .views import AccountView, SettingsView, TransfersView
        makers = {"transfers": TransfersView, "account": AccountView, "settings": SettingsView}
        from .admin_view import AdminFoldersView, UsersView
        from .client_view import ClientFoldersView
        from .chat_view import ChatView
        makers.update(folders=AdminFoldersView, users=UsersView, myfolders=ClientFoldersView, chat=ChatView)
        for key in self.buttons:
            page = makers[key](self.core, self)
            self.pages[key] = page
            self.stack.addWidget(page)

    # ------------------------------------------------------------- helpers
    def go(self, key: str):
        self.buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])
        if key == "account":
            self.pages[key].reload()
        if key == "transfers":
            self.pages[key].refresh()

    def _activity_clicked(self, key: str):
        page = self.pages.get(key)
        if self.stack.currentWidget() is page and hasattr(page, "toggle_sidebar"):
            page.toggle_sidebar()                 # like VS Code: click the active icon to hide/show the side bar
            self.buttons[key].setChecked(True)
            return
        self.go(key)

    def _current_page_call(self, name: str):
        fn = getattr(self.stack.currentWidget(), name, None)
        if fn:
            fn()

    def flash(self, text: str, ms: int = 6000):
        self.st_msg.setText(f"  {text}")
        self._flash_timer.start(ms)

    def save_settings(self, apply: bool = False):
        save_settings(self.data_dir, self.settings)
        if apply:
            apply_settings(self.core.cfg, self.settings)

    def _update_status(self):
        p = self.core.presence
        online = bool(p and p.connected)
        self.st_conn.setText("  ●  Online" if online else "  ○  Offline")
        problem = self.core.connection_problem()
        # a short "connecting" moment is normal; only show the banner for real problems
        show = bool(problem) and not problem.startswith("Connecting")
        if show:
            self.banner_text.setText(problem)
            self.banner_btn.setVisible(bool(p and p.replaced))
        if self.banner.isVisible() != show:
            self.banner.setVisible(show)
        self.st_conn.setToolTip(problem or "Connected to the signaling server")
        me = self.core.me or {}
        n_online = sum(1 for uid in self.core.contacts if self.core.is_online(uid))
        self.st_user.setText(f"   {me.get('display_name') or me.get('username', '')}  ·  "
                             f"{n_online}/{len(self.core.contacts)} "
                             "users online")

    def _tick(self):
        active = [j.snapshot() for j in self.core.jobs.values() if j.state in ("running", "awaiting_accept")]
        speed = sum(s["speed"] for s in active)
        self.st_speed.setText(f"{len(active)} active  ·  {fmt_rate(speed)}   " if active else "")
        if self.stack.currentWidget() is self.pages.get("transfers"):
            self.pages["transfers"].refresh()
        self._update_status()

    def _on_core_event(self, kind, data):
        if kind in ("contacts", "presence"):
            for key in ("folders", "users", "myfolders", "chat"):
                if key in self.pages:
                    self.pages[key].on_presence()
            self._update_status()
        elif kind == "server":
            self._update_status()
            if data and data.get("state") == "replaced":
                QMessageBox.information(self, "Signed in elsewhere",
                                        "This account was just signed in in another app or on another computer, "
                                        "so this app went offline.\n\nTo test on one PC, start the app twice with "
                                        "different profiles (p2p_app.py --profile NAME) and two accounts.")
        elif kind == "job":
            if "transfers" in self.pages:
                self.pages["transfers"].refresh()
            if "myfolders" in self.pages:
                self.pages["myfolders"].on_job()
        elif kind == "chat":
            chat = self.pages.get("chat")
            if chat is not None:
                chat.on_chat(data or {})
            if data and data.get("incoming"):
                viewing = self.stack.currentWidget() is chat and chat.peer == data["uid"] and self.isActiveWindow()
                if not viewing:
                    self.flash(f"New message from {data.get('from')}: {data.get('text', '')[:60]}")
                    QApplication.alert(self)
            self._update_chat_badge()
        elif kind == "received":
            self.flash(f"Received {data.get('name')} from {data.get('from')}")
            if "folders" in self.pages:
                self.pages["folders"].on_received(data)

    def open_chat(self, uid: str):
        self.go("chat")
        self.pages["chat"].open_chat(uid)

    def _update_chat_badge(self):
        unread = sum(v["unread"] for v in self.core.chat_summary().values())
        b = self.buttons.get("chat")
        if b is not None:
            b.setToolTip(f"Chat ({unread} new)" if unread else "Chat")
            b.setIcon(icons.icon("chat", C["warn"] if unread else C["activity_icon"], C["activity_active"]))

    def _reconnect(self):
        if self.core.presence is not None:
            self.core.presence.restart()
            self.flash("Reconnecting...")

    def sign_out(self):
        running = [j for j in self.core.jobs.values() if j.state in ("running", "awaiting_accept")]
        if running and QMessageBox.question(self, "Sign out", f"{len(running)} transfer(s) running. They will be "
                                            "paused. Sign out?") != QMessageBox.Yes:
            return
        self.signed_out = True
        self.core.logout()
        self.close()

    def closeEvent(self, e):
        running = [j for j in self.core.jobs.values() if j.state in ("running", "awaiting_accept")]
        if running and not self.signed_out and QMessageBox.question(
                self, "Quit", f"{len(running)} transfer(s) running. Quit anyway?") != QMessageBox.Yes:
            e.ignore()
            return
        self.timer.stop()
        from .sash import Sash
        for sash in self.findChildren(Sash):       # exact sizes at exit (window resizes move them too)
            if sash.count() and sum(sash.sizes()) > 0:
                self.layout_store.data[f"sash:{sash.name}"] = sash.sizes()
        self.layout_store.data["window"] = bytes(self.saveGeometry().toBase64()).decode()
        self.layout_store.save()
        if not self.signed_out:
            self.core.shutdown()
        e.accept()


def stylesheet() -> str:
    """QSS with the check-mark image written to a cache file (QSS images must be files)."""
    import tempfile
    path = Path(tempfile.gettempdir()) / "mediarush-check.svg"
    if not path.exists():
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14"><path d="M3 7.2l2.6 2.6L11 4.4" '
                        'fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" '
                        'stroke-linejoin="round"/></svg>')
    return QSS.replace("CHECK_SVG", path.as_posix())


def run(app_kind: str = "app", profile: str | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APPS[app_kind]["title"])
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    app.setFont(QFont("Segoe UI", 9))
    app.setWindowIcon(icons.brand_mark(64))
    bridge = init_bridge()
    data_dir = default_data_dir(profile or "default")
    while True:
        settings = load_settings(data_dir)
        cfg = ClientConfig(data_dir=data_dir)
        apply_settings(cfg, settings)
        core = AppCore(cfg, settings["cloud_url"], app=app_kind,
                       on_event=lambda kind, data=None: bridge.core_event.emit(kind, data))
        core.server_override = bool(settings.get("server_override"))

        def cloud_changed(url, core=core, settings=settings):
            save_settings(data_dir, settings)
            from ..cloud_api import CloudClient
            core.cloud = CloudClient(url, data_dir, app=app_kind)
        login = LoginDialog(core, settings, APPS[app_kind]["title"], cloud_changed)
        if core.cloud.logged_in:
            login._busy(True)
            login.subtitle.setText("Signing in...")
            from .common import run_bg
            run_bg(core.restore_session, ok=lambda ok: login.accept() if ok else login._failed(
                "Your session expired - please sign in again."), err=login._failed)
        if login.exec() != QDialog.Accepted:
            core.shutdown()
            return 0
        win = MainWindow(core, app_kind, settings, data_dir, bridge)
        if profile:
            win.setWindowTitle(f"{APPS[app_kind]['title']} - {profile}")
        win.show()
        app.exec()
        if not win.signed_out:
            return 0
        try:
            bridge.core_event.disconnect()
        except (RuntimeError, TypeError):
            pass


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="FileJet desktop app")
    ap.add_argument("--profile", help="separate local data (e.g. to run two accounts on one PC)")
    args, _qt = ap.parse_known_args()
    sys.exit(run("app", args.profile))
