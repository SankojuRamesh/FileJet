"""Shared with me: folders others shared with you; send, download, edit, delete as permitted.

Uploads go into an outbox on this computer and are delivered directly to the admin's computer - right away
when the admin is online, otherwise automatically as soon as they come online."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QProgressBar, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..util import fmt_bytes, fmt_rate
from . import icons
from .common import (big_bar, button, confirm, fmt_time, hbox, label, perm_chips, run_bg, set_bar, show_error,
                     show_in_folder, sum_line, table, tool)
from .filetable import FileTable
from .sash import Sash
from .theme import C

ROLE = Qt.UserRole + 1


def _perm_tags(p: dict) -> str:
    return "".join(t for t, k in (("V", "read"), ("U", "upload"), ("E", "edit"), ("D", "delete")) if p.get(k))


class SendDialog(QDialog):
    """Confirm what is sent: files with previews, the admin's upload form, an optional thumbnail."""

    def __init__(self, parent, folder_name: str, owner: str, online: bool, paths: list[str], fields: list[dict]):
        super().__init__(parent)
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        from .. import thumbs
        self.setWindowTitle(f"Send to {folder_name}")
        self.setMinimumWidth(520)
        self.thumb_path = None
        lay = QVBoxLayout(self)
        lay.addWidget(label(f"Send to '{folder_name}' ({owner})", "PageTitle"))
        if not online:
            lay.addWidget(label(f"{owner} is offline - delivered when online", muted=True))
        self.list = QListWidget()
        self.list.setIconSize(QSize(40, 40))
        self.list.setMaximumHeight(200)
        total = 0
        for path in paths:
            pth = Path(path)
            is_dir = pth.is_dir()
            size = sum(f.stat().st_size for f in pth.rglob("*") if f.is_file()) if is_dir else pth.stat().st_size
            total += size
            thumb = None if is_dir or size > thumbs.MAX_SOURCE else thumbs.make_thumb(pth) \
                if thumbs.ext_of(pth.name) in thumbs.IMAGE_EXT else None
            it = QListWidgetItem(icons.thumb_icon(thumb, pth.name, is_dir), f"{pth.name}   ·   {fmt_bytes(size)}"
                                 + ("   ·   folder" if is_dir else ""))
            self.list.addItem(it)
        lay.addWidget(self.list)
        lay.addWidget(label(f"{len(paths)} item(s) · {fmt_bytes(total)}", muted=True))
        self.thumb_label = label("Thumbnail", muted=True)
        self.thumb_preview = QLabel()
        self.thumb_preview.setFixedSize(64, 64)
        lay.addLayout(hbox(self.thumb_preview, self.thumb_label, None,
                           button("Choose thumbnail...", "secondary", "add_file", self._pick_thumb, small=True),
                           button("Clear", "link", slot=self._clear_thumb)))
        self._paint_thumb(None)
        form = QFormLayout()
        self.edits = {}
        if fields:
            for f in fields:
                e = QLineEdit()
                self.edits[f["name"]] = (e, f.get("required"))
                form.addRow(f["name"] + (" *" if f.get("required") else ""), e)
        lay.addLayout(form)
        bb = QDialogButtonBox()
        bb.addButton("Send", QDialogButtonBox.AcceptRole)
        cancel = bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        cancel.setProperty("kind", "secondary")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _paint_thumb(self, data):
        self.thumb_preview.setPixmap(icons.thumb_icon(data, "x.file", False, 32).pixmap(64, 64))

    def _pick_thumb(self):
        from .. import thumbs
        f, _ = QFileDialog.getOpenFileName(self, "Thumbnail image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if f:
            data = thumbs.from_image(f)
            if data is None:
                return show_error("That file is not a readable image.", self)
            self.thumb_path = f
            self._paint_thumb(data)
            self.thumb_label.setText(Path(f).name)

    def _clear_thumb(self):
        self.thumb_path = None
        self._paint_thumb(None)
        self.thumb_label.setText("Thumbnail")

    def _ok(self):
        missing = [n for n, (e, req) in self.edits.items() if req and not e.text().strip()]
        if missing:
            return show_error(f"Please fill in: {', '.join(missing)}", self)
        self.accept()

    def values(self) -> dict:
        return {n: e.text().strip() for n, (e, _r) in self.edits.items() if e.text().strip()}


class ClientFoldersView(QWidget):
    def __init__(self, core, main):
        super().__init__()
        self.core, self.main = core, main
        self.current: dict | None = None     # {"uid", "folder_id", "name", "kind", "perms", "rules"}
        self.rel = ""
        self.dropbox = False
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sash = Sash("shared.side", Qt.Horizontal, collapse=0, sizes=[290, 900], minimums=[190, 480])
        root.addWidget(self.sash)
        side = QWidget()
        side.setObjectName("SideBar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 8)
        sl.setSpacing(0)
        sl.addLayout(hbox(label("SHARED WITH ME", "SideTitle"), None, tool("refresh", "Refresh", self.refresh_tree),
                          margins=(0, 0, 8, 0)))
        sl.addLayout(hbox(button("Open folder by ID", "primary", "plus", self.open_by_id), margins=(16, 0, 16, 8)))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.itemClicked.connect(self._clicked)
        sl.addWidget(self.tree, 1)
        pid = (core.me or {}).get("public_id", "")
        self.my_id = label(f"Your ID: {pid[:3]} {pid[3:6]} {pid[6:]}", muted=True)
        sl.addLayout(hbox(self.my_id, None, button("Copy", "link", slot=lambda: (
            QGuiApplication.clipboard().setText(pid), self.main.flash("Your ID copied - give it to whoever shares a folder with you"))),
            margins=(16, 6, 12, 0)))
        self.sash.addWidget(side)

        main_w = QWidget()
        ml = QVBoxLayout(main_w)
        ml.setContentsMargins(28, 18, 28, 14)
        ml.setSpacing(10)
        self.title = label("Open a folder", "PageTitle")
        self.perm_label = label("")
        ml.addLayout(hbox(self.title, None, self.perm_label))
        self.rules = label("", muted=True, wrap=True)
        ml.addWidget(self.rules)
        self.crumbs = label("", "Crumbs")
        self.btn_up = tool("up", "Up one level", self.go_up)
        self.perm_row = label("")
        ml.addWidget(self.perm_row)
        self.btn_upload = button("Send files", "primary", "upload", self.upload_files)
        self.btn_upload_dir = button("Send folder", "primary", "share", self.upload_folder)
        self.btn_download = button("Receive / Download", "primary", "download", self.download)
        self.btn_mkdir = button("New folder", "secondary", "new_folder", self.new_folder)
        self.btn_rename = button("Rename", "secondary", "rename", self.rename)
        self.btn_delete = button("Delete", "danger", "trash", self.delete)
        self.btn_refresh = tool("refresh", "Refresh", self.reload)
        from PySide6.QtWidgets import QSizePolicy
        for b in (self.btn_upload, self.btn_upload_dir, self.btn_download, self.btn_mkdir, self.btn_rename,
                  self.btn_delete):
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)      # never squeeze the labels
        ml.addLayout(hbox(self.btn_upload, self.btn_upload_dir, self.btn_download, None, self.btn_mkdir,
                          self.btn_rename, self.btn_delete, self.btn_refresh, spacing=6))
        ml.addLayout(hbox(self.btn_up, self.crumbs, None))
        split = Sash("shared.bottom", Qt.Vertical, collapse=1, sizes=[420, 240], minimums=[120, 0])
        self.bottom = split
        top_w = QWidget()
        tl = QVBoxLayout(top_w)
        tl.setContentsMargins(0, 0, 0, 0)
        self.files = FileTable()
        self.files.opened.connect(self._open_entry)
        self.files.selection_changed.connect(self._update_buttons)
        tl.addWidget(self.files, 1)
        self.drop = QWidget()
        dl = QVBoxLayout(self.drop)
        dl.addStretch(1)
        self.drop_title = label("", "PageTitle")
        dl.addWidget(self.drop_title, alignment=Qt.AlignCenter)
        self.drop_text = label("", muted=True, wrap=True)
        self.drop_text.setAlignment(Qt.AlignCenter)
        dl.addWidget(self.drop_text)
        dl.addLayout(hbox(None, button("Upload files", "primary", "upload", self.upload_files),
                          button("Upload a folder", "secondary", "share", self.upload_folder), None))
        dl.addStretch(2)
        tl.addWidget(self.drop, 1)
        self.drop.hide()
        split.addWidget(top_w)
        up_w = QWidget()
        ul = QVBoxLayout(up_w)
        ul.setContentsMargins(0, 8, 0, 0)
        self.uploads_title = label("MY UPLOADS", "SectionTitle")
        self.show_all = button("All folders", "link", slot=self._toggle_all)
        ul.addLayout(hbox(self.uploads_title, None, self.show_all))
        self.up_summary = label("", muted=True)
        self.up_bar = big_bar()
        ul.addWidget(self.up_summary)
        ul.addWidget(self.up_bar)
        self.uploads = table(["File", "Folder", "Size", "Status", "Progress", "Sent", ""], stretch=0,
                             fixed={4: 240, 6: 84}, row_height=46)
        from PySide6.QtCore import QSize
        self.uploads.setIconSize(QSize(40, 40))
        from PySide6.QtWidgets import QHeaderView
        uh = self.uploads.horizontalHeader()
        uh.setSectionResizeMode(0, QHeaderView.Interactive)      # file name: wide, drag to resize
        self.uploads.setColumnWidth(0, 280)
        uh.setSectionResizeMode(5, QHeaderView.Stretch)
        ul.addWidget(self.uploads)
        split.addWidget(up_w)
        ml.addWidget(split, 1)
        self._all_uploads = True
        self._up_timer = QTimer(self)
        self._up_timer.timeout.connect(self._reload_uploads)
        self._up_timer.start(1000)
        self.dest_label = label("", muted=True)
        ml.addLayout(hbox(self.dest_label, button("Change", "link", slot=self._change_dest),
                          button("Open", "link", slot=lambda: show_in_folder(Path(self.core.cfg.dest_dir))), None))
        self.message = label("", muted=True, wrap=True)
        ml.addWidget(self.message)
        self.sash.addWidget(main_w)
        self._update_dest()
        self._update_buttons()
        self.refresh_tree()
        self._reload_uploads()

    def toggle_sidebar(self):
        self.sash.toggle_collapse()

    def toggle_panel(self):
        self.bottom.toggle_collapse()

    # ------------------------------------------------------------ my uploads (outbox)
    def _toggle_all(self):
        self._all_uploads = not self._all_uploads
        self.show_all.setText("All folders" if self._all_uploads else "This folder only")
        self._reload_uploads()

    STATUS = {"queued": ("Waiting for admin", "warn"), "sending": ("Sending", "info"),
              "delivered": ("Delivered", "ok"), "failed": ("Failed", "bad"), "cancelled": ("Cancelled", "muted")}

    def _reload_uploads(self):
        if not self.isVisible() and self.uploads.rowCount():
            return
        fid = None if self._all_uploads or not self.current else self.current["folder_id"]
        rows = list(reversed(self.core.outbox_items(fid)))[:300]
        t = self.uploads
        if t.rowCount() != len(rows) or getattr(self, "_up_ids", None) != [r["item_id"] for r in rows]:
            t.clearContents()
            t.setRowCount(len(rows))
            self._up_ids = [r["item_id"] for r in rows]
            self._up_state = {}
        waiting = 0
        for i, r in enumerate(rows):
            state = r["state"]
            if state == "queued":
                waiting += 1
            text, color = self.STATUS.get(state, (state, "muted"))
            if state == "queued" and r["admin_online"]:
                text = "Starting..."
            elif state == "sending" and r["speed"]:
                text = f"Sending · {fmt_rate(r['speed'])}"
            key = (state, text, round(r["progress"], 1), bool(r.get("thumb")))
            if self._up_state.get(r["item_id"]) == key:
                continue
            first = r["item_id"] not in self._up_state or (
                bool(r.get("thumb")) != self._up_state[r["item_id"]][3])
            self._up_state[r["item_id"]] = key
            if first:
                t.setItem(i, 0, QTableWidgetItem(icons.thumb_icon(r.get("thumb"), r["rel"], False),
                                                 f"{r['remote_dir']}/{r['rel']}".strip("/")))
                t.setItem(i, 1, QTableWidgetItem(r["folder_name"]))
                t.setItem(i, 2, QTableWidgetItem(fmt_bytes(r["size"])))
                t.setItem(i, 5, QTableWidgetItem(fmt_time(r["created_at"])))
                t.setCellWidget(i, 4, _cell(big_bar()))
            item = QTableWidgetItem(text)
            item.setForeground(icons_qcolor(C[color]))
            if r.get("error") and state in ("queued", "failed"):
                item.setToolTip(r["error"])
            t.setItem(i, 3, item)
            box = t.cellWidget(i, 4)
            bar = box.findChild(QProgressBar) if box is not None else None
            if bar is not None:
                done = r["size"] if state == "delivered" else int(r["size"] * r["progress"] / 100)
                set_bar(bar, done, r["size"], "delivered" if state == "delivered" else
                        "failed" if state == "failed" else "",
                        text=None if state != "queued" else f"0 B / {fmt_bytes(r['size'])}  ·  waiting")
            if state in ("queued", "sending"):
                t.setCellWidget(i, 6, _cell(button("Cancel", "secondary", small=True,
                                                   slot=lambda _=False, x=r["item_id"]: self._cancel_upload(x))))
            elif state == "failed":
                t.setCellWidget(i, 6, _cell(button("Retry", "secondary", small=True,
                                                   slot=lambda _=False, x=r["item_id"]: self._retry_upload(x))))
            else:
                t.removeCellWidget(i, 6)
        self.uploads_title.setText(f"MY UPLOADS  ·  {waiting} waiting for the admin" if waiting else "MY UPLOADS")
        sending = [j.snapshot() for j in self.core.jobs.values() if j.kind == "upload" and j.state == "running"]
        q_bytes = sum(r["size"] for r in rows if r["state"] == "queued")
        d_rows = [r for r in rows if r["state"] == "delivered"]
        tail = (f"  ·  waiting: {waiting} file(s), {fmt_bytes(q_bytes)}" if waiting else "") + \
               (f"  ·  delivered: {len(d_rows)} file(s), {fmt_bytes(sum(r['size'] for r in d_rows))}" if d_rows else "")
        if sending:
            text, done, total = sum_line(sending, "↑ Sending")
            self.up_summary.setText(text + tail)
            set_bar(self.up_bar, done, total)
            self.up_bar.show()
        else:
            self.up_summary.setText(("Nothing sending right now" + tail) if rows else "")
            self.up_bar.setVisible(False)

    def _cancel_upload(self, item_id):
        if confirm(self, "Cancel upload", "Cancel sending this file?"):
            self.core.cancel_upload(item_id)
            self._reload_uploads()

    def _retry_upload(self, item_id):
        self.core.retry_upload(item_id)
        self._reload_uploads()

    def on_job(self):
        self._reload_uploads()

    # ------------------------------------------------------------ side tree
    def refresh_tree(self):
        self.tree.clear()
        folders = self.core.my_folders()
        invites = [f for f in folders if f["status"] != "active"]
        active = [f for f in folders if f["status"] == "active"]
        if invites:
            head = QTreeWidgetItem([f"NEW - SHARED WITH YOU ({len(invites)})"])
            head.setFlags(Qt.ItemIsEnabled)
            head.setForeground(0, icons_qcolor(C["warn"]))
            self.tree.addTopLevelItem(head)
            for f in invites:
                it = QTreeWidgetItem([f"{f['name']}  -  from {f['owner']['display_name'] or f['owner']['username']}"
                                      "  (click to open)"])
                it.setIcon(0, icons.icon("plus", C["warn"], size=16))
                it.setToolTip(0, f"Folder ID {f['folder_id']} · your role: {f['role'].title()}")
                it.setData(0, ROLE, {"kind": "invite", "folder": f})
                head.addChild(it)
            head.setExpanded(True)
        admins: dict[str, list] = {}
        for f in active:
            admins.setdefault(f["owner"]["public_id"], []).append(f)
        for uid, fl in admins.items():
            owner = fl[0]["owner"]
            online = self.core.is_online(uid)
            name = owner["display_name"] or owner["username"]
            org = f" ({owner['organization']})" if owner.get("organization") else ""
            top = QTreeWidgetItem([f"{name}{org}" + ("" if online else "  (offline)")])
            top.setIcon(0, icons.avatar(name, online, 24))
            top.setData(0, ROLE, {"kind": "admin", "uid": uid})
            if not online:
                top.setForeground(0, Qt.gray)
            self.tree.addTopLevelItem(top)
            for f in fl:
                ch = QTreeWidgetItem([f"{f['name']}   [{_perm_tags(f['perms'])}]"])
                ch.setIcon(0, icons.folder_icon())
                ch.setToolTip(0, f"{f['folder_id']} · {f['kind'].title()} · role {f['role']}")
                ch.setData(0, ROLE, {"kind": "folder", "uid": uid, "folder": f})
                top.addChild(ch)
            top.setExpanded(True)
        if not folders:
            it = QTreeWidgetItem(["No folders yet. Give your ID to an admin,"])
            it.setFlags(Qt.NoItemFlags)
            self.tree.addTopLevelItem(it)
            it2 = QTreeWidgetItem(["then open the folder ID from your e-mail."])
            it2.setFlags(Qt.NoItemFlags)
            self.tree.addTopLevelItem(it2)
        if self.current:                            # keep the view in sync (permissions may have changed)
            f = next((x for x in active if x["folder_id"] == self.current["folder_id"]), None)
            if f is None:
                self.current = None
                self.files.set_entries([])
                self.title.setText("Open a folder")
                self.message.setText("Your access to that folder was removed.")
            else:
                self.current.update(perms=f["perms"], kind=f["kind"])
        self._update_buttons()

    def _clicked(self, item, _col):
        d = item.data(0, ROLE)
        if not d:
            return
        if d["kind"] == "invite":
            self._join(d["folder"]["folder_id"])
        elif d["kind"] == "folder":
            f = d["folder"]
            self.current = {"uid": d["uid"], "folder_id": f["folder_id"], "name": f["name"], "kind": f["kind"],
                            "perms": f["perms"], "rules": {"allowed_extensions": f.get("allowed_extensions"),
                                                           "max_file_size": f.get("max_file_size"),
                                                           "form_fields": f.get("form_fields") or []},
                            "owner": f["owner"]}
            self.rel = ""
            self.reload()

    def open_by_id(self):
        fid, ok = QInputDialog.getText(self, "Open folder", "Folder ID from your e-mail (e.g. FD-7K3M-9QX2):")
        if ok and fid.strip():
            self._join(fid.strip())

    def _join(self, fid):
        def done(f):
            self.main.flash(f"Opened '{f['name']}'")
            self.refresh_tree()
            self._select_folder(f["folder_id"])
        run_bg(lambda: self.core.join_folder(fid), ok=done)

    def _select_folder(self, fid):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                d = top.child(j).data(0, ROLE)
                if d and d.get("kind") == "folder" and d["folder"]["folder_id"] == fid:
                    self.tree.setCurrentItem(top.child(j))
                    self._clicked(top.child(j), 0)
                    return

    # ------------------------------------------------------------ browsing
    def _update_dest(self):
        self.dest_label.setText(f"Downloads go to: {self.core.cfg.dest_dir}")

    def _change_dest(self):
        d = QFileDialog.getExistingDirectory(self, "Download folder", str(self.core.cfg.dest_dir))
        if d:
            self.core.cfg.dest_dir = Path(d)
            self.main.settings["download_dir"] = d
            self.main.save_settings()
            self._update_dest()

    def _perms(self) -> dict:
        return (self.current or {}).get("perms") or {"read": False, "upload": False, "edit": False, "delete": False}

    def _set(self, btn, allowed: bool, ready: bool, why_not: str, hint: str = ""):
        """Buttons are always shown; disabled with the reason when not possible."""
        btn.setEnabled(allowed and ready)
        btn.setToolTip(hint if allowed and ready else why_not if not allowed else hint)

    def _update_buttons(self):
        p = self._perms()
        have = bool(self.current)
        on = have and self.core.is_online(self.current["uid"])
        sel = self.files.selected_entries()
        owner = ((self.current or {}).get("owner") or {})
        owner = owner.get("display_name") or owner.get("username") or "the owner"
        offline = f"{owner} is offline - available when their app is running"
        no = "Your permissions for this folder do not include "
        for b in (self.btn_upload, self.btn_upload_dir):
            self._set(b, p["upload"], have, no + "Upload.",
                      "" if on else f"{owner} is offline: your files wait on this PC and are delivered automatically")
        self._set(self.btn_download, p["read"] and not self.dropbox, on and bool(sel),
                  "This is a Submit folder: its contents are hidden." if self.dropbox else no + "View & download.",
                  offline if not on else "Select files or folders to receive")
        self._set(self.btn_mkdir, p["upload"] or p["edit"], on, no + "Edit.", offline if not on else "")
        self._set(self.btn_rename, p["edit"], on and len(sel) == 1, no + "Edit.",
                  offline if not on else "Select one item to rename")
        self._set(self.btn_delete, p["delete"], on and bool(sel), no + "Delete.",
                  offline if not on else "Select items to delete")
        self.btn_up.setEnabled(bool(self.rel))
        self.btn_refresh.setEnabled(on)
        self.perm_label.setText("")
        self.perm_row.setText(("Your permissions:&nbsp;&nbsp; " + perm_chips(p)) if have else "")

    def _rules_text(self) -> str:
        r = (self.current or {}).get("rules") or {}
        parts = []
        if r.get("allowed_extensions"):
            parts.append("Accepted: " + ", ".join("." + e for e in r["allowed_extensions"].split(",")))
        if r.get("max_file_size"):
            parts.append(f"max {fmt_bytes(r['max_file_size'])} per file")
        if r.get("form_fields"):
            parts.append("form: " + ", ".join(f["name"] + ("*" if f.get("required") else "") for f in r["form_fields"]))
        return "  ·  ".join(parts)

    def reload(self):
        cur = self.current
        if not cur:
            return
        owner = cur["owner"]["display_name"] or cur["owner"]["username"]
        self.title.setText(f"{cur['name']}")
        self.crumbs.setText("  ›  ".join([owner, cur["name"]] + [p for p in self.rel.split("/") if p]))
        if not self.core.is_online(cur["uid"]):
            self.rules.setText(self._rules_text())
            if self._perms()["upload"]:
                self._show_dropbox(True, offline=True)
                self.message.setText("")
            else:
                self._show_dropbox(False)
                self.files.set_entries([])
                self.message.setText(f"{owner} is offline")
            self._update_buttons()
            return
        self.message.setText("Loading...")
        rel = self.rel

        def done(data):
            if self.current is not cur or self.rel != rel:
                return
            if data.get("perms"):
                cur["perms"] = data["perms"]
            if data.get("rules"):
                cur["rules"] = data["rules"]
            self.rules.setText(self._rules_text())
            self._show_dropbox(bool(data.get("dropbox")))
            self.files.set_entries(data["entries"])
            self.message.setText("" if data.get("dropbox") else f"{len(data['entries'])} item(s)")
            self._update_buttons()

        def fail(exc):
            self.files.set_entries([])
            self.message.setText(str(exc))
            self.core._refresh_quietly()
            self.refresh_tree()
        run_bg(lambda: self.core.remote_list(cur["uid"], cur["folder_id"], rel), ok=done, err=fail)

    def _show_dropbox(self, on: bool, offline: bool = False):
        self.dropbox = on
        self.files.setVisible(not on)
        self.drop.setVisible(on)
        if on:
            owner = self.current["owner"]["display_name"] or self.current["owner"]["username"]
            self.drop_title.setText(f"Send files to {owner}")
            if offline:
                self.drop_text.setText(f"{owner} is offline - delivered when online\n" + self._rules_text())
            else:
                self.drop_text.setText(self._rules_text())

    def _open_entry(self, e):
        if e["dir"]:
            self.rel = f"{self.rel}/{e['name']}".strip("/")
            self.reload()
        elif self._perms()["read"]:
            self.download()

    def go_up(self):
        self.rel = "/".join(self.rel.split("/")[:-1])
        self.reload()

    def _item_path(self, e) -> str:
        return f"{self.rel}/{e['name']}".strip("/")

    # ------------------------------------------------------------ actions
    def download(self):
        sel = self.files.selected_entries()
        if not sel:
            return show_error("Select the files or folders to download first (Ctrl+click for several).",
                              self, "Download")
        cur = self.current
        try:
            job = self.core.download(cur["uid"], cur["folder_id"], cur["name"],
                                     [(self._item_path(e), e["dir"]) for e in sel], Path(self.core.cfg.dest_dir))
        except Exception as exc:
            return show_error(exc, self)
        self.main.flash(f"Downloading {job.title} - see Transfers")

    def _upload(self, paths, ask: bool = True):
        cur = self.current
        form = (cur.get("rules") or {}).get("form_fields") or []
        owner = cur["owner"]["display_name"] or cur["owner"]["username"]
        online = self.core.is_online(cur["uid"])
        fields, thumb = {}, None
        if ask:
            d = SendDialog(self, cur["name"], owner, online, [str(x) for x in paths], form)
            if d.exec() != QDialog.Accepted:
                return None
            fields, thumb = d.values(), d.thumb_path
        try:
            job = self.core.upload(cur["uid"], cur["folder_id"], cur["name"], self.rel, paths, fields=fields,
                                   rules=cur.get("rules"), thumbnail=thumb)
        except Exception as exc:
            return show_error(exc, self, "Send")
        size = f"{len(job.items)} file(s), {fmt_bytes(sum(i.size for i in job.items))}"
        if online:
            self.main.flash(f"Sending {job.title} ({size}) - see My uploads")
        else:
            self.main.flash(f"{job.title} ({size}) queued - delivered automatically when {owner} is online")
        self._reload_uploads()
        return job

    def upload_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Upload files")
        if files:
            self._upload(files)

    def upload_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Upload a folder")
        if d:
            self._upload([d])

    def new_folder(self):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name.strip():
            cur, rel = self.current, self.rel
            run_bg(lambda: self.core.remote_mkdir(cur["uid"], cur["folder_id"], rel, name.strip()),
                   ok=lambda _r: self.reload())

    def rename(self):
        sel = self.files.selected_entries()
        if len(sel) != 1:
            return
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=sel[0]["name"])
        if ok and name.strip() and name != sel[0]["name"]:
            cur, path = self.current, self._item_path(sel[0])
            run_bg(lambda: self.core.remote_rename(cur["uid"], cur["folder_id"], path, name.strip()),
                   ok=lambda _r: self.reload())

    def delete(self):
        sel = self.files.selected_entries()
        owner = self.current["owner"]["display_name"] or self.current["owner"]["username"]
        if not sel or not confirm(self, "Delete", f"Delete {len(sel)} item(s) from {owner}'s computer? "
                                  "This cannot be undone.", True):
            return
        cur, paths = self.current, [self._item_path(e) for e in sel]

        def work():
            for p in paths:
                self.core.remote_delete(cur["uid"], cur["folder_id"], p)
        run_bg(work, ok=lambda _r: self.reload(), err=lambda e: (show_error(e, self), self.reload()))

    def on_presence(self):
        self.refresh_tree()
        if self.current:
            self._update_buttons()
            online = self.core.is_online(self.current["uid"])
            if online != getattr(self, "_was_online", None):
                self._was_online = online
                self.reload()


def _cell(widget):
    box = QWidget()
    box.setLayout(hbox(widget, margins=(4, 3, 4, 3)))
    return box


def icons_qcolor(hexcolor):
    from PySide6.QtGui import QColor
    return QColor(hexcolor)
