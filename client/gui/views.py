"""Transfers (sending / receiving), Account and Settings."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHeaderView, QLineEdit, QProgressBar,
                               QSpinBox,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..util import fmt_bytes, fmt_duration, fmt_rate
from . import icons
from .common import (STATE_TEXT, big_bar, button, card, confirm, hbox, label, page, run_bg, set_bar, show_error,
                     show_in_folder, status_color, sum_line, table)
from .theme import C


def _item(text, color=None, icon=None, align_right=False):
    it = QTableWidgetItem(icon, text) if icon is not None else QTableWidgetItem(text)
    if color:
        it.setForeground(Qt.GlobalColor.white if color == "white" else _qcolor(color))
    if align_right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return it


def _qcolor(hexcolor):
    from PySide6.QtGui import QColor
    return QColor(hexcolor)


def _padded(widget):
    box = QWidget()
    box.setLayout(hbox(widget, margins=(4, 3, 4, 3)))
    return box


def _w(layout):
    w = QWidget()
    w.setLayout(layout)
    return w


# ====================================================================== Transfers
# "upload"/"download" = I started it; "receive"/"send" = someone else started it in one of my folders
KIND_TEXT = {"upload": ("Sending", "upload"), "send": ("Sending", "upload"),
             "download": ("Receiving", "download"), "receive": ("Receiving", "download")}
OUTGOING = ("upload", "send")


def _local(iso):
    return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone() if iso else None


def _took(s: dict):
    """Time taken = only while data was moving; waiting (other side offline, connecting) is in the tooltip."""
    it = _item(fmt_duration(s["elapsed"]) if s["elapsed"] >= 0.05 or s["state"] == "completed" else "-",
               C["ok"] if s["state"] == "completed" else None, align_right=True)
    if s.get("waited", 0) >= 1:
        it.setToolTip(f"Transferring: {fmt_duration(s['elapsed'])}\n"
                      f"Waiting (offline / connecting), not counted: {fmt_duration(s['waited'])}")
    return it


def _times(n: int) -> str:
    return f"{n} time{'s' if n != 1 else ''}"


def _status_with_tries(t: dict) -> str:
    """'Failed 6 times' instead of six rows; 'Completed (after 6 failed tries)'."""
    f = int(t.get("failures") or 0)
    if t["status"] == "failed":
        return f"Failed {_times(max(f, 1))}"
    text = STATE_TEXT.get(t["status"], t["status"])
    if not f:
        return text
    return f"{text} (after {f} failed tr{'y' if f == 1 else 'ies'})" if t["status"] == "completed"         else f"{text} (try {t.get('attempts', f + 1)}, failed {_times(f)})"


class AttemptsDialog(QDialog):
    """Every try of one file: started, ended, result, bytes, time, reason."""

    def __init__(self, parent, name: str, data: dict):
        super().__init__(parent)
        self.setWindowTitle(f"Tries - {name}")
        self.resize(980, 420)
        lay = QVBoxLayout(self)
        attempts = data["attempts"]
        last = attempts[-1] if attempts else {}
        head = f"{name}: tried {_times(data['total'])}, failed {_times(data['failures'])}"
        if last.get("status") == "completed":
            head += f", completed {_local(last['ended_at']):%Y-%m-%d %H:%M:%S}"
        if data.get("shown_from", 1) > 1:
            head += f"  (showing tries {data['shown_from']}-{data['total']})"
        lay.addWidget(label(head, "SectionTitle", wrap=True))
        tbl = table(["Try", "Started", "Ended", "Result", "Transferred", "Time", "Speed", "Connection", "Reason"],
                    stretch=8)
        tbl.setRowCount(len(attempts))
        for r, a in enumerate(attempts):
            st, en = _local(a["started_at"]), _local(a.get("ended_at"))
            cells = [_item(f"#{a['n']}", align_right=True), _item(f"{st:%Y-%m-%d %H:%M:%S}"),
                     _item(f"{en:%Y-%m-%d %H:%M:%S}" if en else "-"),
                     _item(STATE_TEXT.get(a["status"], a["status"]), status_color(a["status"])),
                     _item(f"{fmt_bytes(a['bytes_transferred'])} / {fmt_bytes(a['file_size'])}", align_right=True),
                     _item(fmt_duration(a["duration"]) if a["duration"] is not None else "-", align_right=True),
                     _item(fmt_rate(a["avg_speed"]) if a["avg_speed"] else "-", align_right=True),
                     _item(a["connection_type"] or "-"), _item(a["error"] or "")]
            for c, it in enumerate(cells):
                tbl.setItem(r, c, it)
        lay.addWidget(tbl, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class TransfersView(QWidget):
    def __init__(self, core, main):
        super().__init__()
        self.core, self.main = core, main
        from PySide6.QtWidgets import QVBoxLayout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget()
        outer.addWidget(tabs)

        live, lay = page("")
        from PySide6.QtWidgets import QButtonGroup, QPushButton
        self.filter = "all"
        self.filter_buttons = QButtonGroup(self)
        chips = []
        for key, text in (("all", "All"), ("out", "Sending"), ("in", "Receiving")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("kind", "secondary")
            b.setProperty("btn", "small")
            b.setChecked(key == "all")
            b.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self.filter_buttons.addButton(b)
            chips.append(b)
        self.counts = label("", muted=True)
        lay.addLayout(hbox(label("Transfers", "PageTitle"), *chips, self.counts, None,
                           button("Cancel selected", "secondary", "close", self.cancel_selected),
                           button("Show in folder", "secondary", "open", self.show_selected)))
        # totals: everything being sent / received right now
        self.out_label, self.in_label = label("", muted=True), label("", muted=True)
        self.out_bar, self.in_bar = big_bar(), big_bar()
        lay.addWidget(self.out_label)
        lay.addWidget(self.out_bar)
        lay.addWidget(self.in_label)
        lay.addWidget(self.in_bar)
        self.jobs = table(["Direction", "Name", "From / to", "Folder", "Files", "Size", "Progress", "Left", "Speed",
                           "Time taken", "Time left", "Status", "Connection", "Started", "Finished"], stretch=1, row_height=36,
                          fixed={6: 250})
        jh = self.jobs.horizontalHeader()
        jh.setSectionResizeMode(1, QHeaderView.Interactive)     # name: wide, user-resizable
        self.jobs.setColumnWidth(1, 240)
        self.jobs.setColumnWidth(0, 120)
        jh.setStretchLastSection(True)
        from PySide6.QtCore import QSize
        self.jobs.setIconSize(QSize(28, 28))
        lay.addWidget(self.jobs, 1)
        tabs.addTab(live, icons.icon("transfers", C["text"], size=16), "Active && recent")

        hist, hl = page("")
        hl.addLayout(hbox(label("History (cloud)", "PageTitle"), None,
                          button("Refresh", "secondary", "refresh", self.load_history),
                          button("Open web dashboard", "primary", "web", self.open_web)))
        self.history = table(["File name", "Size", "From", "To", "Type", "Status", "Done", "Speed",
                              "Connection", "Date & time", "Time taken"], stretch=0)
        self.history.setToolTip("Click a row to see every try")
        self.history.cellClicked.connect(self._show_attempts)
        hl.addWidget(self.history, 1)
        self.hist_msg = label("", muted=True)
        hl.addWidget(self.hist_msg)
        tabs.addTab(hist, icons.icon("web", C["text"], size=16), "History")
        tabs.currentChanged.connect(lambda i: self.load_history() if i == 1 else None)
        self._rows: dict[str, int] = {}

    def _set_filter(self, key):
        self.filter = key
        self.jobs.clearContents()
        self.jobs.setRowCount(0)
        self.refresh()

    def refresh(self):
        every = list(self.core.jobs.values())
        n_out = sum(1 for j in every if j.kind in OUTGOING)
        self.counts.setText(f"{n_out} sending · {len(every) - n_out} receiving")
        jobs = sorted((j for j in every if self.filter == "all" or (j.kind in OUTGOING) == (self.filter == "out")),
                      key=lambda j: -j.created)
        if self.jobs.rowCount() != len(jobs):
            self.jobs.setRowCount(len(jobs))
        snaps = {j.job_id: j.snapshot() for j in jobs}
        running = [s for s in (j.snapshot() for j in every) if s["state"] == "running"]
        for verb, lab, bar, want in (("Sending", self.out_label, self.out_bar, True),
                                     ("Receiving", self.in_label, self.in_bar, False)):
            act = [s for s in running if (s["kind"] in OUTGOING) == want]
            if act:
                text, done, total = sum_line(act, ("↑ " if want else "↓ ") + verb)
                lab.setText(text)
                set_bar(bar, done, total)
            else:
                lab.setText(f"{'↑' if want else '↓'} Nothing {verb.lower()} right now")
                set_bar(bar, 0, 0, text="")
            bar.setVisible(bool(act))
        for r, job in enumerate(jobs):
            s = snaps[job.job_id]
            text, ic = KIND_TEXT.get(s["kind"], (s["kind"], "transfers"))
            cells = [
                _item(text, icon=icons.icon(ic, C["info"] if ic == "download" else C["ok"], size=16)),
                _item(s["title"], icon=icons.thumb_icon(s.get("thumb"), s["title"], s.get("is_folder", False), 28)),
                _item(s["peer_name"]), _item(s["share_name"] or "-"),
                _item(f"{s['files_done']}/{s['files']}" if s["files"] else "-", align_right=True),
                _item(fmt_bytes(s["total"]), align_right=True), None,
                _item(fmt_bytes(s["remaining"]) if s["state"] not in ("completed",) else "-", align_right=True),
                _item(fmt_rate(s["speed"]) if s["speed"] else
                      (f"avg {fmt_rate(s['avg_speed'])}" if s.get("avg_speed") else "-"), align_right=True),
                _took(s),
                _item(fmt_duration(s["eta"]) if s["eta"] else "-", align_right=True),
                _item(STATE_TEXT.get(s["state"], s["state"]) + (f": {s['error']}" if s["error"] else ""),
                      status_color(s["state"])),
                _item(s["connection"] or "-", C["warn"] if (s["connection"] or "").startswith("RELAYED") else
                      C["ok"] if s["connection"] else None),
                _item(dt.datetime.fromtimestamp(s["created"]).strftime("%Y-%m-%d %H:%M:%S")),
                _item(dt.datetime.fromtimestamp(s["finished_at"]).strftime("%Y-%m-%d %H:%M:%S")
                      if s.get("finished_at") else "-"),
            ]
            for c, it in enumerate(cells):
                if it is not None:
                    it.setData(Qt.UserRole, job.job_id)
                    self.jobs.setItem(r, c, it)
            box = self.jobs.cellWidget(r, 6)
            bar = box.findChild(QProgressBar) if box is not None else None
            if bar is None:
                bar = big_bar()
                self.jobs.setCellWidget(r, 6, _padded(bar))
            set_bar(bar, s["transferred"], s["total"], s["state"])

    def _selected_job(self):
        r = self.jobs.currentRow()
        it = self.jobs.item(r, 1) if r >= 0 else None
        return self.core.jobs.get(it.data(Qt.UserRole)) if it else None

    def cancel_selected(self):
        job = self._selected_job()
        if job and job.state not in ("completed", "failed", "cancelled") and confirm(
                self, "Cancel transfer", f"Cancel '{job.title}'?"):
            self.core.cancel_job(job.job_id)

    def show_selected(self):
        job = self._selected_job()
        if not job:
            return
        for item in job.items:
            eng = item.engine
            path = getattr(eng, "final_path", None) or (item.local if job.kind in ("send", "upload") else None)
            if path:
                return show_in_folder(Path(path))
        show_in_folder(Path(self.core.cfg.dest_dir))

    def load_history(self):
        self.hist_msg.setText("Loading...")

        def done(data):
            rows = data["results"]
            self.history.setRowCount(len(rows))
            for r, t in enumerate(rows):
                sender = (t["sender"] or {}).get("display_name") or (t["sender"] or {}).get("username") or "-"
                receiver = (t["receiver"] or {}).get("display_name") or (t["receiver"] or {}).get("username") or "-"
                started = _local(t.get("first_started_at") or t["started_at"])
                cells = [
                    _item(t["file_name"], icon=icons.icon_for_name(t["file_name"], False)),
                    _item(fmt_bytes(t["file_size"]), align_right=True), _item(sender), _item(receiver),
                    _item(("Sent by me" if t.get("my_role") == "sender" else "Received") + (" (download)" if t["direction"] == "download" else "")
                          + (f" · {t['share_name']}" if t["share_name"] else "")),
                    _item(_status_with_tries(t), status_color(t["status"])),
                    _item(f"{t['progress']:.0f}%", align_right=True),
                    _item(fmt_rate(t["avg_speed"]) if t["avg_speed"] else "-", align_right=True),
                    _item(t["connection_type"] or "-"),
                    _item(started.strftime("%Y-%m-%d %H:%M:%S")),
                    _item(fmt_duration(t["duration"]) if t["duration"] is not None else "-", align_right=True),
                ]
                cells[0].setToolTip(t.get("relative_path") or t["file_name"])   # full path on mouse-over
                for c, it in enumerate(cells):
                    it.setData(Qt.UserRole, t["transfer_id"])
                    self.history.setItem(r, c, it)
            self.hist_msg.setText(f"{data['count']} transfer(s) recorded in your account (metadata only).")
        run_bg(lambda: self.core.cloud.history(page_size=200), ok=done,
               err=lambda e: self.hist_msg.setText(f"Cloud unavailable: {e}"))

    def _show_attempts(self, row, _col):
        it = self.history.item(row, 0)
        if it is None or not it.data(Qt.UserRole):
            return
        name = it.text()
        run_bg(lambda: self.core.cloud.transfer_attempts(it.data(Qt.UserRole)),
               ok=lambda d: AttemptsDialog(self, name, d).exec(),
               err=lambda e: show_error(e, self, "Transfer"))

    def open_web(self):
        QDesktopServices.openUrl(QUrl(self.core.cloud.base + "transfers/"))


# ======================================================================== Account
class AccountView(QWidget):
    def __init__(self, core, main):
        super().__init__()
        self.core, self.main = core, main
        from PySide6.QtWidgets import QVBoxLayout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        w, self.lay = page("Account")
        outer.addWidget(w)
        self.name = label("", "PageTitle")
        self.details = label("", muted=True, wrap=True)
        self.plan = label("")
        self.usage_bar = QProgressBar()
        self.usage_bar.setRange(0, 1000)
        self.usage = label("", muted=True)
        self.phone = QLineEdit(placeholderText="+91 98765 43210")
        self.phone.setMaximumWidth(260)
        self.phone_msg = label("", muted=True)
        self.lay.addWidget(card(label("PROFILE", "SectionTitle"), self.name, self.details,
                                _w(hbox(label("Mobile number"), self.phone,
                                        button("Save", "primary", "check", self._save_phone, small=True),
                                        self.phone_msg, None)),
                                label("Used only for one-time codes (SMS / WhatsApp) when a folder admin asks for "
                                      "one. Include the country code.", muted=True, wrap=True),
                                _w(hbox(button("Edit profile on the web", "secondary", "web",
                                               lambda: self._web("account/")), None))))
        self.lay.addWidget(card(label("SUBSCRIPTION", "SectionTitle"), self.plan, self.usage_bar, self.usage,
                                _w(hbox(button("Manage subscription", "primary", "web",
                                               lambda: self._web("subscription/")),
                                        button("Devices", "secondary", "remote", lambda: self._web("devices/")),
                                        None))))
        self.lay.addWidget(_w(hbox(None, button("Sign out", "danger", "logout", self.main.sign_out))))
        self.lay.addStretch(1)
        self.reload()

    def _web(self, path):
        QDesktopServices.openUrl(QUrl(self.core.cloud.base + path))

    def _save_phone(self):
        def done(me):
            self.phone.setText(me.get("phone") or "")
            self.phone_msg.setText("Saved" if me.get("phone") else "Removed")

        def fail(e):
            self.phone_msg.setText(str(e).removeprefix("phone: "))
        run_bg(lambda: self.core.cloud.update_me({"phone": self.phone.text().strip()}), ok=done, err=fail)

    def reload(self):
        def done(me):
            pid = me["public_id"]
            self.phone.setText(me.get("phone") or "")
            self.name.setText(me.get("display_name") or me["username"])
            self.details.setText(f"@{me['username']} · {me['email']} · ID {pid[:3]} {pid[3:6]} {pid[6:]} · "
                                 f"device {self.core.identity.fingerprint[:12]}...")
            sub, use = me["subscription"], me["usage"]
            plan = sub["plan"]
            self.plan.setText(f"<b>{plan['name']}</b> plan · renews "
                              f"{sub['current_period_end'][:10]}" + (" (cancels at period end)"
                                                                     if sub["cancel_at_period_end"] else ""))
            q = use["quota_bytes"]
            self.usage_bar.setValue(int(min(1.0, use["used_bytes"] / q) * 1000) if q else 0)
            self.usage.setText(f"{fmt_bytes(use['used_bytes'])} used of "
                               f"{fmt_bytes(q) if q else 'unlimited'} this period · max file "
                               f"{fmt_bytes(plan['max_file_size']) if plan['max_file_size'] else 'unlimited'} · "
                               f"users {plan.get('max_users') or 'unlimited'} · folders "
                               f"{plan.get('max_folders') or 'unlimited'}")
        run_bg(self.core.cloud.me, ok=done, err=lambda e: self.details.setText(f"Cloud unavailable: {e}"))


# ======================================================================= Settings
class SettingsView(QWidget):
    def __init__(self, core, main):
        super().__init__()
        self.core, self.main = core, main
        from PySide6.QtWidgets import QVBoxLayout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        w, lay = page("Settings")
        outer.addWidget(w)
        s = main.settings
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.cloud = QLineEdit(s["cloud_url"])
        self.server = QLineEdit(s.get("server_override") or "", placeholderText="(automatic - from the cloud)")
        self.dest = QLineEdit(s["download_dir"])
        pick = button("Browse...", "secondary", slot=self._pick)
        self.streams = QSpinBox(minimum=1, maximum=32, value=int(s["streams"]))
        self.max_streams = QSpinBox(minimum=1, maximum=32, value=int(s["max_streams"]))
        self.chunk = QComboBox()
        self.chunk.addItems(["Auto", "1M", "4M", "8M", "16M", "32M"])
        self.chunk.setCurrentText(s["chunk_size"] or "Auto")
        self.limit = QLineEdit(s.get("rate_limit") or "", placeholderText="e.g. 50M  (empty = no limit)")
        form.addRow("Cloud app URL", self.cloud)
        form.addRow("Signaling server", self.server)
        form.addRow("Download folder", _w(hbox(self.dest, pick)))
        form.addRow("Parallel streams", self.streams)
        form.addRow("Maximum streams", self.max_streams)
        form.addRow("Chunk size", self.chunk)
        form.addRow("Bandwidth limit (bytes/s)", self.limit)
        self.checks = {}
        for key, text in (("use_upnp", "UPnP port mapping on the router"),
                          ("use_stun", "STUN public address discovery"),
                          ("allow_punch", "TCP hole punching"),
                          ("verify_full", "Also verify a plain SHA-256 of every file (extra read)")):
            cb = QCheckBox(text)
            cb.setChecked(bool(s.get(key)))
            self.checks[key] = cb
            form.addRow("", cb)
        lay.addWidget(card(_w(form)))
        lay.addWidget(_w(hbox(button("Reset layout", "secondary", "refresh", self._reset_layout), None,
                              button("Save", "primary", "check", self.save))))
        lay.addStretch(1)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Download folder", self.dest.text())
        if d:
            self.dest.setText(d)

    def _reset_layout(self):
        from .sash import reset_all
        reset_all(self.main)
        self.main.flash("Layout reset to the defaults")

    def save(self):
        s = self.main.settings
        from ..util import parse_size
        try:
            if self.limit.text().strip():
                parse_size(self.limit.text().strip())
        except ValueError as exc:
            return show_error(exc, self)
        s.update(cloud_url=self.cloud.text().strip().rstrip("/") + "/", server_override=self.server.text().strip(),
                 download_dir=self.dest.text().strip(), streams=self.streams.value(),
                 max_streams=max(self.streams.value(), self.max_streams.value()), chunk_size=self.chunk.currentText(),
                 rate_limit=self.limit.text().strip(), **{k: cb.isChecked() for k, cb in self.checks.items()})
        self.main.save_settings(apply=True)
        self.main.flash("Settings saved.")
