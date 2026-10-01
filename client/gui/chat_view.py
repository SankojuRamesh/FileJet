"""Chat: everyone you are linked with (users you added and people who added you), online / offline, and
conversations. Messages are end-to-end encrypted and go directly between the two apps; a message to someone
who is offline waits on this computer and is delivered when they are online. Nothing is stored in the cloud."""
from __future__ import annotations

import datetime as dt
import html

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QTextBrowser,
                               QVBoxLayout, QWidget)

from . import icons
from .common import button, hbox, label, run_bg, show_error
from .sash import Sash
from .theme import C


def _when(ts: float) -> str:
    d = dt.datetime.fromtimestamp(ts)
    return d.strftime("%H:%M") if d.date() == dt.date.today() else d.strftime("%d %b %H:%M")


TICKS = {"queued": ("&#9719;", "muted", "Waiting - delivered when they are online"),
         "delivered": ("&#10003;", "muted", "Delivered"), "read": ("&#10003;&#10003;", "info", "Read")}


class ChatView(QWidget):
    def __init__(self, core, main):
        super().__init__()
        self.core, self.main = core, main
        self.peer: str | None = None
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sash = Sash("chat.side", Qt.Horizontal, collapse=0, sizes=[300, 900], minimums=[200, 380])
        root.addWidget(self.sash)
        side = QWidget()
        side.setObjectName("SideBar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 8)
        sl.setSpacing(0)
        self.side_title = label("USERS", "SideTitle")
        sl.addWidget(self.side_title)
        self.search = QLineEdit(placeholderText="Search users")
        self.search.textChanged.connect(lambda _t: self.refresh_list())
        sl.addLayout(hbox(self.search, margins=(12, 0, 12, 8)))
        self.list = QListWidget()
        from PySide6.QtCore import QSize
        self.list.setIconSize(QSize(34, 34))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.currentItemChanged.connect(lambda cur, _p: self._open(cur))
        sl.addWidget(self.list, 1)
        self.sash.addWidget(side)

        main_w = QWidget()
        ml = QVBoxLayout(main_w)
        ml.setContentsMargins(28, 18, 28, 14)
        ml.setSpacing(10)
        self.title = label("Select a user", "PageTitle")
        self.status = label("", muted=True)
        ml.addLayout(hbox(self.title, None, self.status))
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.input = QPlainTextEdit(placeholderText="Message")
        self.input.setMinimumHeight(40)
        self.input.installEventFilter(self)
        self.send_btn = button("Send", "primary", "send", self.send)
        self.vsplit = Sash("chat.input", Qt.Vertical, collapse=None, sizes=[520, 80], minimums=[120, 48])
        self.vsplit.addWidget(self.view)
        box = QWidget()
        bl = hbox(self.input, self.send_btn, margins=(0, 6, 0, 0))
        bl.setAlignment(self.send_btn, Qt.AlignTop)
        box.setLayout(bl)
        self.vsplit.addWidget(box)
        ml.addWidget(self.vsplit, 1)
        self.sash.addWidget(main_w)
        self._set_enabled(False)
        self.refresh_list()

    def toggle_sidebar(self):
        self.sash.toggle_collapse()

    # ------------------------------------------------------------ list
    def _people(self) -> list[dict]:
        summary = self.core.chat_summary()
        people = []
        for uid, c in self.core.contacts.items():
            s = summary.get(uid, {})
            people.append(dict(c, uid=uid, online=self.core.is_online(uid), unread=s.get("unread", 0),
                               last_text=s.get("last_text"), last_ts=s.get("last_ts") or 0,
                               last_outgoing=s.get("last_outgoing", False)))
        q = self.search.text().strip().lower()
        if q:
            people = [p for p in people if q in p["name"].lower() or q in p.get("username", "").lower()
                      or q in p["uid"]]
        people.sort(key=lambda p: (-(p["unread"] > 0), -p["online"], -p["last_ts"], p["name"].lower()))
        return people

    def refresh_list(self):
        people = self._people()
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for p in people:
            org = f" · {p['organization']}" if p.get("organization") else ""
            badge = f"   ({p['unread']} new)" if p["unread"] else ""
            preview = ""
            if p["last_text"]:
                t = p["last_text"].replace("\n", " ")
                preview = ("You: " if p["last_outgoing"] else "") + (t[:38] + "..." if len(t) > 40 else t)
            else:
                preview = "Online" if p["online"] else "Offline"
            it = QListWidgetItem(icons.avatar(p["name"], p["online"], 34),
                                 f"{p['name']}{org}{badge}\n{preview}")
            it.setData(Qt.UserRole, p["uid"])
            it.setToolTip(f"@{p.get('username', '')} · ID {p['uid']} · {'online' if p['online'] else 'offline'}")
            if p["unread"]:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.list.addItem(it)
            if p["uid"] == self.peer:
                chosen = it
        if chosen is not None:
            self.list.setCurrentItem(chosen)
        self.list.blockSignals(False)
        online = sum(1 for p in people if p["online"])
        self.side_title.setText(f"USERS  ·  {online} online / {len(people)}")
        if not people:
            it = QListWidgetItem("No users yet")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        self._update_header()

    def open_chat(self, uid: str):
        self.peer = uid
        self.refresh_list()
        self._load()

    def _open(self, item):
        if item is None or not item.data(Qt.UserRole):
            return
        self.peer = item.data(Qt.UserRole)
        self._load()

    # ------------------------------------------------------------ conversation
    def _set_enabled(self, on: bool):
        self.input.setEnabled(on)
        self.send_btn.setEnabled(on)

    def _update_header(self):
        if not self.peer or self.peer not in self.core.contacts:
            self.title.setText("Select a user")
            self.status.setText("")
            self._set_enabled(False)
            return
        c = self.core.contacts[self.peer]
        self.title.setText(c["name"])
        on = self.core.is_online(self.peer)
        self.status.setText(f"<span style='color:{C['ok']}'>&#9679; Online</span>" if on else
                            f"<span style='color:{C['muted']}'>&#9679; Offline</span>")
        self._set_enabled(True)

    def _load(self):
        self._update_header()
        if not self.peer:
            return
        msgs = self.core.chat_history(self.peer)
        name = self.core.contacts.get(self.peer, {}).get("name", "")
        rows = []
        day = None
        for m in msgs:
            d = dt.datetime.fromtimestamp(m["ts"]).date()
            if d != day:
                day = d
                rows.append(f"<tr><td colspan='2' align='center' style='color:{C['muted']}; padding:8px'>"
                            f"{d.strftime('%A, %d %B %Y')}</td></tr>")
            text = html.escape(m["text"]).replace("\n", "<br>")
            if m["outgoing"]:
                tick, col, tip = TICKS.get(m["state"], ("", "muted", ""))
                rows.append(f"<tr><td width='25%'></td><td align='right'><table cellpadding='8' "
                            f"style='background:{C['accent_soft']}; border-radius:10px'><tr><td>{text}"
                            f"<br><span style='color:{C['muted']}; font-size:10px'>{_when(m['ts'])} "
                            f"<span style='color:{C[col]}' title='{tip}'>{tick}</span></span></td></tr></table>"
                            "</td></tr>")
            else:
                rows.append(f"<tr><td align='left'><table cellpadding='8' style='background:{C['raised']}; "
                            f"border-radius:6px'><tr><td><span style='color:{C['info']}; font-size:10px'>"
                            f"{html.escape(name)}</span><br>{text}<br><span style='color:{C['muted']}; "
                            f"font-size:10px'>{_when(m['ts'])}</span></td></tr></table></td>"
                            "<td width='25%'></td></tr>")
        if not msgs:
            rows.append(f"<tr><td align='center' style='color:{C['muted']}; padding:30px'>No messages yet. "
                        "Say hello!</td></tr>")
        self.view.setHtml(f"<table width='100%' cellspacing='4'>{''.join(rows)}</table>")
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())
        if self.isVisible() and self.window().isActiveWindow():
            self.core.mark_chat_read(self.peer)

    def send(self):
        text = self.input.toPlainText().strip()
        if not text or not self.peer:
            return
        try:
            self.core.send_chat(self.peer, text)
        except Exception as exc:
            return show_error(exc, self, "Chat")
        self.input.clear()
        self._load()

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not ev.modifiers() & Qt.ShiftModifier:
            self.send()
            return True
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------ events
    def on_chat(self, data: dict):
        self.refresh_list()
        if data.get("uid") == self.peer:
            self._load()

    def on_presence(self):
        self.refresh_list()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self.peer:
            run_bg(lambda: None, ok=lambda _r: self._load())
