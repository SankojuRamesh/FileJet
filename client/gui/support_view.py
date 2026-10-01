"""Support: open a request and chat with the FileJet support team (tickets live in the cloud)."""
from __future__ import annotations

import datetime as dt
import html

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QTextBrowser, QVBoxLayout, QWidget)

from . import icons
from .common import button, hbox, label, run_bg, show_error
from .sash import Sash
from .theme import C

CATEGORIES = [("general", "General question"), ("transfer", "Transfers & connection"),
              ("account", "Account & sign-in"), ("billing", "Billing & subscription"), ("bug", "Bug report")]
STATUS_COLOR = {"open": "info", "waiting": "warn", "closed": "muted"}


def _when(value) -> str:
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    return t.strftime("%H:%M") if t.date() == dt.date.today() else t.strftime("%d %b %H:%M")


class NewRequestDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("New support request")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.addWidget(label("New support request", "PageTitle"))
        form = QFormLayout()
        self.category = QComboBox()
        for code, text in CATEGORIES:
            self.category.addItem(text, code)
        self.subject = QLineEdit(placeholderText="e.g. Transfer stops at 80%")
        self.subject.setMaxLength(150)
        self.message = QPlainTextEdit(placeholderText="What happened, what did you expect, any error message...")
        self.message.setMinimumHeight(140)
        form.addRow("Topic", self.category)
        form.addRow("Subject", self.subject)
        form.addRow("Message", self.message)
        lay.addLayout(form)
        bb = QDialogButtonBox()
        bb.addButton("Send to support", QDialogButtonBox.AcceptRole)
        cancel = bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        cancel.setProperty("kind", "secondary")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self):
        if not self.subject.text().strip() or not self.message.toPlainText().strip():
            return show_error("Please enter a subject and a message.", self, "Support")
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return self.subject.text().strip(), self.message.toPlainText().strip(), self.category.currentData()


class SupportView(QWidget):
    def __init__(self, core, main):
        super().__init__()
        self.core, self.main = core, main
        self.tickets: list[dict] = []
        self.current: dict | None = None
        self.unread = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.sash = Sash("support.side", Qt.Horizontal, collapse=0, sizes=[320, 900], minimums=[220, 380])
        root.addWidget(self.sash)

        side = QWidget()
        side.setObjectName("SideBar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 8)
        sl.setSpacing(0)
        sl.addWidget(label("SUPPORT", "SideTitle"))
        sl.addLayout(hbox(button("New request", "primary", "plus", self.new_request), margins=(16, 0, 16, 8)))
        self.list = QListWidget()
        self.list.setIconSize(QSize(26, 26))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.currentItemChanged.connect(lambda cur, _p: self._open(cur))
        sl.addWidget(self.list, 1)
        self.sash.addWidget(side)

        main_w = QWidget()
        ml = QVBoxLayout(main_w)
        ml.setContentsMargins(28, 18, 28, 14)
        ml.setSpacing(10)
        self.title = label("Support", "PageTitle")
        self.status = label("", muted=True)
        ml.addLayout(hbox(self.title, None, self.status))
        self.view = QTextBrowser()
        self.vsplit = Sash("support.input", Qt.Vertical, collapse=None, sizes=[520, 90], minimums=[120, 48])
        self.vsplit.addWidget(self.view)
        self.input = QPlainTextEdit(placeholderText="Message")
        self.input.setMinimumHeight(40)
        self.input.installEventFilter(self)
        self.send_btn = button("Send", "primary", "send", self.send)
        self.solved_btn = button("Mark as solved", "secondary", "check", self.close_ticket, small=True)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 6, 0, 0)
        bl.addLayout(hbox(self.input, self.send_btn))
        bl.addLayout(hbox(None, self.solved_btn))
        self.vsplit.addWidget(box)
        ml.addWidget(self.vsplit, 1)
        self.sash.addWidget(main_w)
        self._set_enabled(False)
        self._show_welcome()

        self.poll = QTimer(self)                 # new replies: list every 30 s, open conversation every 8 s
        self.poll.timeout.connect(self._poll)
        self.poll.start(8000)
        self._ticks = 0
        QTimer.singleShot(1500, self.refresh)

    def toggle_sidebar(self):
        self.sash.toggle_collapse()

    # ------------------------------------------------------------ data
    def refresh(self):
        def done(data):
            self.tickets = data.get("tickets", [])
            self._fill_list()
            self._set_unread(int(data.get("unread", 0)))
        run_bg(self.core.cloud.support_tickets, ok=done, err=lambda _e: None)

    def _poll(self):
        if not self.core.me:
            return
        self._ticks += 1
        if self.current and self.isVisible():
            self._load(self.current["id"], quiet=True)
        if self._ticks % 4 == 0 or (self.isVisible() and not self.current):
            self.refresh()

    def _fill_list(self):
        keep = self.current["id"] if self.current else None
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for t in self.tickets:
            badge = f"   ({t['unread']} new)" if t.get("unread") else ""
            it = QListWidgetItem(icons.icon("support", C[STATUS_COLOR.get(t["status"], "muted")], size=26),
                                 f"{t['subject']}{badge}\n{t['number']} · {t['status_label']} · {_when(t['last_message_at'])}")
            it.setData(Qt.UserRole, t["id"])
            if t.get("unread"):
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.list.addItem(it)
            if t["id"] == keep:
                chosen = it
        if not self.tickets:
            it = QListWidgetItem("No requests yet")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        if chosen is not None:
            self.list.setCurrentItem(chosen)
        self.list.blockSignals(False)

    def _set_unread(self, n: int):
        if n > self.unread and not (self.isVisible() and self.window().isActiveWindow()):
            self.main.flash("FileJet Support replied to your request")
        self.unread = n
        b = self.main.buttons.get("support")
        if b is not None:
            b.setToolTip(f"Support ({n} new)" if n else "Support")
            b.setIcon(icons.icon("support", C["warn"] if n else C["activity_icon"], C["activity_active"]))

    # ------------------------------------------------------------ conversation
    def _open(self, item):
        if item is None or not item.data(Qt.UserRole):
            return
        self._load(item.data(Qt.UserRole))

    def _load(self, ticket_id: int, quiet: bool = False):
        def done(t):
            if self.current and self.current["id"] != t["id"] and quiet:
                return
            changed = not self.current or self.current["id"] != t["id"] or \
                len(self.current.get("messages", [])) != len(t.get("messages", []))
            self.current = t
            self.title.setText(t["subject"])
            col = C[STATUS_COLOR.get(t["status"], "muted")]
            self.status.setText(f"{t['number']} · {t['category_label']} · <span style='color:{col}'>{t['status_label']}</span>")
            self._set_enabled(True)
            self.solved_btn.setVisible(t["status"] != "closed")
            if changed:
                self._render(t)
                if quiet:
                    self.refresh()
        run_bg(lambda: self.core.cloud.support_ticket(ticket_id), ok=done,
               err=(lambda _e: None) if quiet else (lambda e: show_error(e, self, "Support")))

    def _render(self, t):
        rows = []
        for m in t.get("messages", []):
            text = html.escape(m["body"]).replace("\n", "<br>")
            if m["from_staff"]:
                rows.append(f"<tr><td align='left'><table cellpadding='9' style='background:{C['raised']}'><tr><td>"
                            f"<span style='color:{C['info']};font-size:10px'>FileJet Support</span><br>{text}<br>"
                            f"<span style='color:{C['muted']};font-size:10px'>{_when(m['created_at'])}</span>"
                            "</td></tr></table></td><td width='25%'></td></tr>")
            else:
                rows.append(f"<tr><td width='25%'></td><td align='right'><table cellpadding='9' "
                            f"style='background:{C['accent_soft']}'><tr><td>{text}<br>"
                            f"<span style='color:{C['muted']};font-size:10px'>{_when(m['created_at'])}</span>"
                            "</td></tr></table></td></tr>")
        if t["status"] == "closed":
            rows.append(f"<tr><td colspan='2' align='center' style='color:{C['muted']};padding:12px'>"
                        "This request is closed. Send a message to reopen it.</td></tr>")
        self.view.setHtml(f"<table width='100%' cellspacing='6'>{''.join(rows)}</table>")
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _show_welcome(self):
        self.view.setHtml(f"<div style='color:{C['muted']};padding:40px;text-align:center'>"
                          "Questions or problems? Open a request with <b>New request</b> – the FileJet support team "
                          "answers right here.</div>")

    def _set_enabled(self, on: bool):
        self.input.setEnabled(on)
        self.send_btn.setEnabled(on)
        self.solved_btn.setVisible(on)

    # ------------------------------------------------------------ actions
    def new_request(self):
        d = NewRequestDialog(self)
        if d.exec() != QDialog.Accepted:
            return
        subject, message, category = d.values()

        def done(t):
            self.main.flash(f"Request {t['number']} sent to support")
            self.current = None
            self.refresh()
            self._load(t["id"])
        run_bg(lambda: self.core.cloud.support_open(subject, message, category), ok=done,
               err=lambda e: show_error(e, self, "Support"))

    def send(self):
        text = self.input.toPlainText().strip()
        if not text or not self.current:
            return
        tid = self.current["id"]
        self.input.clear()

        def done(t):
            self.current = None
            self._load(t["id"])
            self.refresh()
        run_bg(lambda: self.core.cloud.support_reply(tid, text), ok=done,
               err=lambda e: (self.input.setPlainText(text), show_error(e, self, "Support")))

    def close_ticket(self):
        if not self.current:
            return
        tid = self.current["id"]
        run_bg(lambda: self.core.cloud.support_close(tid),
               ok=lambda _t: (self._load(tid), self.refresh(), self.main.flash("Request marked as solved")),
               err=lambda e: show_error(e, self, "Support"))

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not ev.modifiers() & Qt.ShiftModifier:
            self.send()
            return True
        return super().eventFilter(obj, ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.refresh()
