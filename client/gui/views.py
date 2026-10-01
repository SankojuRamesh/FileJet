"""Transfers (sending / receiving), Account and Settings."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHeaderView, QLineEdit, QProgressBar,
                               QSpinBox,
                               QTableWidgetItem, QTabWidget, QWidget)

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
                           "Time left", "Status", "Connection", "Started"], stretch=1, row_height=36,
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
                              "Connection", "Date & time", "Duration"], stretch=0)
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
                _item(fmt_duration(s["eta"]) if s["eta"] else
                      (f"took {fmt_duration(s['elapsed'])}" if s["state"] == "completed" else "-"), align_right=True),
                _item(STATE_TEXT.get(s["state"], s["state"]) + (f": {s['error']}" if s["error"] else ""),
                      status_color(s["state"])),
                _item(s["connection"] or "-", C["warn"] if (s["connection"] or "").startswith("RELAYED") else
                      C["ok"] if s["connection"] else None),
                _item(dt.datetime.fromtimestamp(s["created"]).strftime("%Y-%m-%d %H:%M:%S")),
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
                started = dt.datetime.fromisoformat(t["started_at"].replace("Z", "+00:00")).astimezone()
                cells = [
                    _item(t["file_name"], icon=icons.icon_for_name(t["file_name"], False)),
                    _item(fmt_bytes(t["file_size"]), align_right=True), _item(sender), _item(receiver),
                    _item(("Sent by me" if t.get("my_role") == "sender" else "Received") + (" (download)" if t["direction"] == "download" else "")
                          + (f" · {t['share_name']}" if t["share_name"] else "")),
                    _item(STATE_TEXT.get(t["status"], t["status"]), status_color(t["status"])),
                    _item(f"{t['progress']:.0f}%", align_right=True),
                    _item(fmt_rate(t["avg_speed"]) if t["avg_speed"] else "-", align_right=True),
                    _item(t["connection_type"] or "-"),
                    _item(started.strftime("%Y-%m-%d %H:%M:%S")),
                    _item(fmt_duration(t["duration"]) if t["duration"] is not None else "-", align_right=True),
                ]
                for c, it in enumerate(cells):
                    self.history.setItem(r, c, it)
            self.hist_msg.setText(f"{data['count']} transfer(s) recorded in your account (metadata only).")
        run_bg(lambda: self.core.cloud.history(page_size=200), ok=done,
               err=lambda e: self.hist_msg.setText(f"Cloud unavailable: {e}"))

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
        self.lay.addWidget(card(label("PROFILE", "SectionTitle"), self.name, self.details,
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

    def reload(self):
        def done(me):
            pid = me["public_id"]
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
