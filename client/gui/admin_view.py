"""My Folders (on this computer, shared with users with per-user permissions) and Users (by ID, groups)."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPlainTextEdit, QRadioButton, QSpinBox,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..shares import ROLE_TEXT, ROLES, SIZER, list_dir, resolve_in_share, size_text
from ..util import fmt_bytes, sanitize_filename
from . import icons
from .common import (big_bar, button, card, confirm, fmt_time, hbox, label, open_file, page, run_bg, set_bar,
                     show_error, show_in_folder, sum_line, table, tool)
from .filetable import FileTable
from .sash import Sash
from .theme import C

MB = 10 ** 6


def _name_item(path: str, icon=None) -> QTableWidgetItem:
    """Show only the file name; the full path is in the tooltip."""
    path = (path or "").strip("/")
    it = QTableWidgetItem(icon, path.split("/")[-1]) if icon is not None else QTableWidgetItem(path.split("/")[-1])
    it.setToolTip(path)
    return it


def _w(layout):
    w = QWidget()
    w.setLayout(layout)
    return w


def _cell(widget):
    box = QWidget()
    box.setLayout(hbox(widget, margins=(4, 3, 4, 3)))
    return box


# ================================================================== dialogs
class FolderSettingsForm(QWidget):
    """Type, description, file rules, upload form fields, notifications."""

    def __init__(self, f: dict | None = None):
        super().__init__()
        f = f or {}
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.share = QRadioButton("Share")
        self.submit = QRadioButton("Submit (drop box)")
        g = QButtonGroup(self)
        g.addButton(self.share)
        g.addButton(self.submit)
        (self.submit if f.get("kind") == "submit" else self.share).setChecked(True)
        form.addRow("Type", _w(_vbox(self.share, self.submit)))
        self.description = QLineEdit(f.get("description", ""), placeholderText="Optional")
        form.addRow("Description", self.description)
        self.types = QLineEdit(f.get("allowed_extensions", ""), placeholderText="Any")
        form.addRow("Allowed file types", self.types)
        self.max_mb = QSpinBox(minimum=0, maximum=10_000_000, suffix=" MB")
        self.max_mb.setSpecialValueText("No limit")
        self.max_mb.setValue(int((f.get("max_file_size") or 0) / MB))
        form.addRow("Max file size", self.max_mb)
        self.fields = QPlainTextEdit("\n".join(x["name"] + ("*" if x.get("required") else "")
                                              for x in f.get("form_fields") or []))
        self.fields.setPlaceholderText("Project*\nNotes")
        self.fields.setFixedHeight(80)
        form.addRow("Upload form", self.fields)
        self.notify = QCheckBox("E-mail on upload")
        self.notify.setChecked(bool(f.get("notify_owner", True)))
        form.addRow("", self.notify)

    def values(self) -> dict:
        fields = []
        for line in self.fields.toPlainText().splitlines():
            name = line.strip()
            if name:
                fields.append({"name": name.rstrip("*").strip(), "required": name.endswith("*")})
        return {"kind": "submit" if self.submit.isChecked() else "share", "description": self.description.text(),
                "allowed_extensions": self.types.text(), "max_file_size": self.max_mb.value() * MB or None,
                "form_fields": fields, "notify_owner": self.notify.isChecked()}


def _vbox(*ws):
    lay = QVBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    for w in ws:
        lay.addWidget(w)
    return lay


class NewFolderDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("New folder")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(placeholderText="Folder name")
        form.addRow("Folder name", self.name)
        lay.addLayout(form)
        self.use_existing = QRadioButton("Use an existing folder on this computer / NAS")
        self.create_new = QRadioButton("Create a new folder")
        self.create_new.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.use_existing)
        g.addButton(self.create_new)
        self.existing = QLineEdit(placeholderText="Folder path")
        self.parent_dir = QLineEdit(str(Path.home() / "P2P Folders"), placeholderText="Create it inside")
        lay.addWidget(self.create_new)
        lay.addLayout(hbox(self.parent_dir, button("Browse...", "secondary", slot=self._browse_parent),
                           margins=(22, 0, 0, 0)))
        lay.addWidget(self.use_existing)
        lay.addLayout(hbox(self.existing, button("Browse...", "secondary", slot=self._browse_existing),
                           margins=(22, 0, 0, 0)))
        lay.addWidget(label("RULES", "SectionTitle"))
        self.settings = FolderSettingsForm()
        lay.addWidget(self.settings)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse_existing(self):
        d = QFileDialog.getExistingDirectory(self, "Folder")
        if d:
            self.existing.setText(d)
            self.use_existing.setChecked(True)
            if not self.name.text():
                self.name.setText(Path(d).name)

    def _browse_parent(self):
        d = QFileDialog.getExistingDirectory(self, "Create inside", self.parent_dir.text())
        if d:
            self.parent_dir.setText(d)

    def _ok(self):
        name = self.name.text().strip()
        if self.use_existing.isChecked():
            p = Path(self.existing.text().strip())
            if not p.is_dir():
                return show_error("Choose an existing folder.", self)
            self.result_value = (name or p.name, p, False, self.settings.values())
        else:
            if not name:
                return show_error("Enter a folder name.", self)
            self.result_value = (name, Path(self.parent_dir.text().strip()) / sanitize_filename(name), True,
                                 self.settings.values())
        self.accept()


class ShareDialog(QDialog):
    """Share a folder: type a user ID (or username / e-mail), or tick any of your users and groups,
    then choose the permissions once for everyone ticked."""

    def __init__(self, parent, core, folder: dict, preselect: str | None = None):
        super().__init__(parent)
        self.core, self.folder = core, folder
        self.setWindowTitle(f"Share '{folder['name']}'")
        self.setMinimumSize(600, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(label(f"Share '{folder['name']}'", "PageTitle"))
        lay.addWidget(label(f"Folder ID {folder['folder_id']} · the people you tick get an e-mail and see it "
                            "under 'Shared with me' in their app.", muted=True, wrap=True))

        lay.addWidget(label("ADD BY ID", "SectionTitle"))
        self.query = QLineEdit(placeholderText="User ID (e.g. 364 866 519), username or e-mail")
        self.query.returnPressed.connect(self._add_by_id)
        self.add_btn = button("Add", "secondary", "plus", self._add_by_id)
        lay.addLayout(hbox(self.query, self.add_btn))

        lay.addWidget(label("YOUR USERS", "SectionTitle"))
        self.filter = QLineEdit(placeholderText="Search users...")
        self.filter.textChanged.connect(self._filter)
        lay.addWidget(self.filter)
        self.people = QListWidget()
        self.people.setIconSize(QSize(24, 24))
        self.people.itemChanged.connect(lambda _it: self._update_ok())
        lay.addWidget(self.people, 1)
        self.have = {m["uid"] for m in core.store.members(folder["folder_id"])}

        form = QFormLayout()
        self.role = QComboBox()
        for key in ("uploader", "viewer", "editor", "manager", "custom"):
            self.role.addItem(ROLE_TEXT[key], key)
        form.addRow("Role", self.role)
        self.boxes = {k: QCheckBox(t) for k, t in (("read", "View && download"), ("upload", "Upload"),
                                                   ("edit", "Edit"), ("delete", "Delete"))}
        form.addRow("Permissions", _w(hbox(*self.boxes.values(), None)))
        self.expire_on = QCheckBox("Access ends on")
        self.expire = QDateEdit(QDate.currentDate().addDays(30))
        self.expire.setCalendarPopup(True)
        self.expire.setEnabled(False)
        self.expire_on.toggled.connect(self.expire.setEnabled)
        form.addRow("Expiry", _w(hbox(self.expire_on, self.expire, None)))
        lay.addLayout(form)
        self.role.currentIndexChanged.connect(self._role_changed)
        for cb in self.boxes.values():
            cb.toggled.connect(self._box_changed)
        self._role_changed()

        bb = QDialogButtonBox()
        self.ok = bb.addButton("Share", QDialogButtonBox.AcceptRole)
        cancel = bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        cancel.setProperty("kind", "secondary")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill(check={("user", preselect)} if preselect else set())

    # ---------------------------------------------------------------- people
    def _fill(self, check: set | None = None):
        keep = check if check is not None else self.chosen_ids()
        self.people.blockSignals(True)
        self.people.clear()
        clients = {u["public_id"]: u for u in self.core.overview.get("clients", [])}
        users = sorted((c for uid, c in self.core.contacts.items() if uid in clients),
                       key=lambda c: c["name"].lower())
        for g in self.core.overview.get("groups", []):
            it = QListWidgetItem(icons.icon("contacts", C["info"], size=22),
                                 f"Group: {g['name']}   ({len(g['members'])} users)")
            it.setData(Qt.UserRole, ("group", g["id"]))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if ("group", g["id"]) in keep else Qt.Unchecked)
            self.people.addItem(it)
        for c in users:
            uid = c["uid"]
            pid = str(uid)
            shown_id = f"{pid[:3]} {pid[3:6]} {pid[6:]}" if pid.isdigit() and len(pid) == 9 else pid
            online = self.core.is_online(uid)
            text = f"{c['name']}  @{c['username']}   ·   ID {shown_id}   ·   {'online' if online else 'offline'}"
            it = QListWidgetItem(icons.avatar(c["name"], online, 24), text)
            it.setData(Qt.UserRole, ("user", uid))
            if uid in self.have:
                it.setText(text + "   ·   already has access")
                it.setFlags(Qt.ItemIsEnabled)
                it.setForeground(Qt.gray)
            else:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if ("user", uid) in keep else Qt.Unchecked)
            self.people.addItem(it)
        if self.people.count() == 0:
            it = QListWidgetItem("No users yet - type a user ID above and press Add")
            it.setFlags(Qt.NoItemFlags)
            self.people.addItem(it)
        self.people.blockSignals(False)
        self._filter(self.filter.text())
        self._update_ok()

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.people.count()):
            it = self.people.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def chosen_ids(self) -> set:
        out = set()
        for i in range(self.people.count()):
            it = self.people.item(i)
            if it.data(Qt.UserRole) and it.flags() & Qt.ItemIsUserCheckable and it.checkState() == Qt.Checked:
                out.add(tuple(it.data(Qt.UserRole)))
        return out

    def _add_by_id(self):
        q = self.query.text().strip()
        if not q:
            return
        self.add_btn.setEnabled(False)
        keep = self.chosen_ids()

        def done(u):
            self.add_btn.setEnabled(True)
            self.query.clear()
            uid = u["public_id"]
            if uid in self.have:
                show_error(f"{u.get('display_name') or u['username']} already has access to this folder.", self,
                           "Share")
            self._fill(check=keep | {("user", uid)})

        def fail(e):
            self.add_btn.setEnabled(True)
            show_error(e, self, "Share")
        run_bg(lambda: self.core.add_user(q, ""), ok=done, err=fail)

    # ---------------------------------------------------------------- permissions
    def _role_changed(self):
        key = self.role.currentData()
        if key in ROLES:
            for cb, v in zip(self.boxes.values(), ROLES[key]):
                cb.blockSignals(True)
                cb.setChecked(v)
                cb.blockSignals(False)

    def _box_changed(self):
        perms = tuple(cb.isChecked() for cb in self.boxes.values())
        key = next((k for k, v in ROLES.items() if v == perms), "custom")
        self.role.blockSignals(True)
        self.role.setCurrentIndex(self.role.findData(key))
        self.role.blockSignals(False)

    def _update_ok(self):
        n = len(self.chosen_ids())
        self.ok.setText(f"Share with {n}" if n else "Share")
        self.ok.setEnabled(n > 0)

    def _ok(self):
        if not any(cb.isChecked() for cb in self.boxes.values()):
            return show_error("Give at least one permission.", self, "Share")
        if not self.chosen_ids():
            return show_error("Tick at least one user, or add one by ID.", self, "Share")
        self.accept()

    def values(self):
        perms = {k: cb.isChecked() for k, cb in self.boxes.items()}
        expires = self.expire.date().toString("yyyy-MM-dd") if self.expire_on.isChecked() else None
        return sorted(self.chosen_ids(), key=str), self.role.currentData(), perms, expires


# ================================================================== Folders (admin)
class AdminFoldersView(QWidget):
    def __init__(self, core, main):
        super().__init__()
        self.core, self.main = core, main
        self.folder: dict | None = None
        self.rel = ""
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sash = Sash("folders.side", Qt.Horizontal, collapse=0, sizes=[270, 900], minimums=[170, 420])
        root.addWidget(self.sash)
        side = QWidget()
        side.setObjectName("SideBar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        sl.addLayout(hbox(label("FOLDERS", "SideTitle"), None, tool("plus", "New folder", self.new_folder),
                          margins=(0, 0, 8, 0)))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda cur, _p: self._select(cur))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._folder_menu)
        sl.addWidget(self.list)
        self.sash.addWidget(side)

        main_w = QWidget()
        ml = QVBoxLayout(main_w)
        ml.setContentsMargins(28, 18, 28, 14)
        ml.setSpacing(10)
        self.title = label("", "PageTitle")
        self.kind = label("")
        self.fid = label("", "BigId")
        self.fid.setStyleSheet("font-size: 16px;")
        copy = button("Copy ID", "secondary", "copy", self._copy_id, small=True)
        ml.addLayout(hbox(self.title, self.kind, None, self.fid, copy,
                          button("Share", "primary", "plus", lambda: self.give_access(), small=True),
                          button("Settings", "secondary", "settings", self.edit_settings, small=True),
                          button("Delete", "danger", "trash", self.delete_folder, small=True)))
        self.info = label("", muted=True, wrap=True)
        ml.addWidget(self.info)
        self.stats = label("", wrap=True)
        ml.addWidget(self.stats)
        self.arriving = label("", wrap=True)
        self.arriving_bar = big_bar()
        ml.addWidget(self.arriving)
        ml.addWidget(self.arriving_bar)
        self.arriving.hide()
        self.arriving_bar.hide()
        from PySide6.QtCore import QTimer
        self._size_timer = QTimer(self)
        self._size_timer.timeout.connect(self._update_sizes)
        self._size_timer.start(2000)
        self._arr_timer = QTimer(self)
        self._arr_timer.timeout.connect(self._update_arriving)
        self._arr_timer.start(700)
        self.crumbs = label("", "Crumbs")
        self.btn_up = tool("up", "Up one level", self.go_up)
        from PySide6.QtWidgets import QSizePolicy
        bar = [button("Add files", "primary", "add_file", self.add_files),
               button("Add folder", "primary", "share", self.add_dir),
               button("New folder", "secondary", "new_folder", self.new_subfolder),
               button("Rename", "secondary", "rename", self.rename_selected),
               button("Delete", "danger", "trash", self.delete_selected)]
        for b in bar:
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)      # never squeeze the labels
        ml.addLayout(hbox(*bar[:2], None, *bar[2:],
                          tool("open", "Open", self.open_selected),
                          tool("share", "Show in Explorer", self.open_local),
                          tool("refresh", "Refresh", self.reload), spacing=6))
        ml.addLayout(hbox(self.btn_up, self.crumbs, None))
        split = Sash("folders.bottom", Qt.Vertical, collapse=1, sizes=[340, 320], minimums=[110, 0])
        self.bottom = split
        self.files = FileTable()
        self.files.opened.connect(self._open_entry)
        self.files.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._files_menu)
        split.addWidget(self.files)
        self.tabs = QTabWidget()
        mem = QWidget()
        mm = QVBoxLayout(mem)
        mm.setContentsMargins(0, 8, 0, 0)
        mm.addLayout(hbox(None, button("Give access...", "primary", "plus", self.give_access)))
        self.members = table(["User", "E-mail", "Status", "Role", "View & download", "Upload", "Edit", "Delete",
                              "Access until", "", ""], stretch=0,
                             fixed={4: 118, 5: 70, 6: 56, 7: 62, 9: 86, 10: 92}, row_height=42)
        mm.addWidget(self.members)
        self.tabs.addTab(mem, "Users with access")
        rec = QWidget()
        rl = QVBoxLayout(rec)
        rl.setContentsMargins(0, 8, 0, 0)
        self.received = table(["File", "From", "Size", "Received", "Form", "", ""], stretch=0,
                              fixed={5: 84, 6: 130}, row_height=46)
        from PySide6.QtCore import QSize
        self.received.setIconSize(QSize(40, 40))
        from PySide6.QtWidgets import QHeaderView
        rh = self.received.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.Interactive)
        self.received.setColumnWidth(0, 320)
        rh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.received.cellDoubleClicked.connect(lambda r, _c: self._open_received(r))
        rl.addWidget(self.received)
        self.tabs.addTab(rec, "Received files")
        wait = QWidget()
        wl = QVBoxLayout(wait)
        wl.setContentsMargins(0, 8, 0, 0)
        wl.addLayout(hbox(None, button("Refresh", "secondary", "refresh", self._reload_waiting, small=True)))
        self.waiting = table(["File", "From", "Size", "Status", "Sent"], stretch=0)
        wl.addWidget(self.waiting)
        self.tabs.addTab(wait, "Waiting to arrive")
        self.tabs.currentChanged.connect(lambda i: i == 2 and self._reload_waiting())
        split.addWidget(self.tabs)
        self._received_rows: list[dict] = []
        ml.addWidget(split, 1)
        self.main_w = main_w

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch(1)
        el.addWidget(label("Create your first folder", "PageTitle"), alignment=Qt.AlignCenter)
        el.addWidget(button("New folder", "primary", "plus", self.new_folder), alignment=Qt.AlignCenter)
        el.addStretch(2)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(main_w)
        hl.addWidget(self.empty)
        self.sash.addWidget(holder)
        self.refresh_folders()

    def toggle_sidebar(self):
        self.sash.toggle_collapse()

    def toggle_panel(self):
        self.bottom.toggle_collapse()

    # ------------------------------------------------------------ folder list
    def refresh_folders(self, select: str | None = None):
        current = select or (self.folder["folder_id"] if self.folder else None)
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for f in self.core.store.list():
            it = QListWidgetItem(icons.folder_icon(), self._folder_label(f))
            it.setData(Qt.UserRole, f["folder_id"])
            it.setToolTip(f["path"])
            self.list.addItem(it)
            if f["folder_id"] == current:
                chosen = it
        self.list.blockSignals(False)
        has = self.list.count() > 0
        self.main_w.setVisible(has)
        self.empty.setVisible(not has)
        if has:
            self.list.setCurrentItem(chosen or self.list.item(0))
            self._select(self.list.currentItem())

    @staticmethod
    def _folder_label(f: dict) -> str:
        n = len(f["members"])
        return (f"{f['name']}   ·   {size_text(SIZER.peek(f['path']))}\n"
                f"{f['folder_id']} · {f['kind'].title()} · {n} user{'s' if n != 1 else ''}")

    def _update_sizes(self):
        """Every few seconds: new totals (files being received make the folder grow)."""
        if not self.isVisible():
            return
        folders = {f["folder_id"]: f for f in self.core.store.list()}      # list() includes the members
        for i in range(self.list.count()):
            it = self.list.item(i)
            f = folders.get(it.data(Qt.UserRole))
            if f is not None:
                text = self._folder_label(f)
                if it.text() != text:
                    it.setText(text)
        if self.folder:
            self.title.setText(f"{self.folder['name']}   <span style='color:{C['muted']};font-size:14px'>"
                               f"{size_text(SIZER.peek(self.folder['path']))}</span>")

    def _folder_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        self.list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(icons.icon("plus", C["accent"], size=16), "Share...", lambda: self.give_access())
        menu.addAction(icons.icon("copy", C["text"], size=16), "Copy folder ID", self._copy_id)
        menu.addAction(icons.icon("open", C["text"], size=16), "Open in file explorer",
                       lambda: show_in_folder(Path(self.folder["path"])) if self.folder else None)
        menu.addAction(icons.icon("settings", C["text"], size=16), "Settings...", self.edit_settings)
        menu.addSeparator()
        menu.addAction(icons.icon("trash", C["bad"], size=16), "Delete folder...", self.delete_folder)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _select(self, item):
        if item is None:
            return
        new = self.core.store.get(item.data(Qt.UserRole))
        if not self.folder or new["folder_id"] != self.folder["folder_id"]:
            self.rel = ""
        self.folder = new
        self.reload()

    def new_folder(self):
        d = NewFolderDialog(self)
        if d.exec() != QDialog.Accepted:
            return
        name, path, create, settings = d.result_value

        def done(fid):
            self.refresh_folders(fid)
            self.main.flash(f"Folder created: {fid}")
            if self.core.contacts:
                self.give_access()
        run_bg(lambda: self.core.create_folder(name, path, create=create, settings=settings), ok=done)

    def edit_settings(self):
        if not self.folder:
            return
        d = QDialog(self)
        d.setWindowTitle(f"Settings - {self.folder['name']}")
        d.setMinimumWidth(560)
        lay = QVBoxLayout(d)
        name = QLineEdit(self.folder["name"])
        lay.addLayout(hbox(label("Name"), name))
        form = FolderSettingsForm(self.folder)
        lay.addWidget(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        if d.exec() == QDialog.Accepted:
            data = dict(form.values(), name=name.text().strip() or self.folder["name"])
            fid = self.folder["folder_id"]
            run_bg(lambda: self.core.update_folder(fid, data), ok=lambda _f: self.refresh_folders(fid))

    def delete_folder(self):
        if self.folder and confirm(self, "Delete folder", f"Remove '{self.folder['name']}' ({self.folder['folder_id']})?"
                                   "\n\nThe files stay on your computer; all users lose access immediately.", True):
            fid = self.folder["folder_id"]
            self.folder = None
            run_bg(lambda: self.core.delete_folder(fid), ok=lambda _r: self.refresh_folders())

    def _copy_id(self):
        if self.folder:
            QGuiApplication.clipboard().setText(self.folder["folder_id"])
            self.main.flash("Folder ID copied")

    # ------------------------------------------------------------ browsing
    def _path(self) -> Path:
        return resolve_in_share(self.folder["path"], self.rel)

    def reload(self):
        f = self.folder
        if not f:
            return
        self.title.setText(f"{f['name']}   <span style='color:{C['muted']};font-size:14px'>"
                           f"{size_text(SIZER.peek(f['path']))}</span>")
        color = C["warn"] if f["kind"] == "submit" else C["info"]
        self.kind.setText(f"<span style='color:{color}'>{f['kind'].title()}</span>")
        self.fid.setText(f["folder_id"])
        free = self.core.folder_free_space(f["folder_id"])
        rules = [f"Free: {fmt_bytes(free) if free is not None else '-'}"]
        if f["allowed_extensions"]:
            rules.append(f"Types: {f['allowed_extensions']}")
        if f["max_file_size"]:
            rules.append(f"Max: {fmt_bytes(f['max_file_size'])}")
        if f["form_fields"]:
            rules.append("Form: " + ", ".join(x["name"] + ("*" if x["required"] else "") for x in f["form_fields"]))
        self.info.setText("  ·  ".join(rules))
        self.info.setToolTip(f["path"])
        parts = [f["name"]] + [p for p in self.rel.split("/") if p]
        self.crumbs.setText("  ›  ".join(parts))
        self.btn_up.setEnabled(bool(self.rel))
        try:
            self.files.set_entries(list_dir(self._path(), f["path"], 0, 100000)["entries"])
        except OSError as exc:
            self.files.set_entries([])
            self.crumbs.setText(f"Folder not available: {exc}")
        self._reload_members()
        self._reload_received()
        self._reload_stats()

    # ------------------------------------------------------------ received / waiting
    def _reload_received(self):
        rows = self.core.received_files(self.folder["folder_id"])
        self._received_rows = rows
        t = self.received
        t.clearContents()
        t.setRowCount(0)
        t.setRowCount(len(rows))
        for r, x in enumerate(rows):
            t.setItem(r, 0, _name_item(x["rel_path"], icons.thumb_icon(x.get("thumb"), x["name"], False)))
            t.setItem(r, 1, QTableWidgetItem(x["sender_name"] or x["sender_uid"]))
            size = QTableWidgetItem(fmt_bytes(x["size"]))
            size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(r, 2, size)
            t.setItem(r, 3, QTableWidgetItem(fmt_time(x["completed_at"])))
            t.setItem(r, 4, QTableWidgetItem(", ".join(f"{k}: {v}" for k, v in x["fields"].items()) or "-"))
            t.setCellWidget(r, 5, _cell(button("Open", "secondary", small=True,
                                               slot=lambda _=False, i=r: self._open_received(i))))
            t.setCellWidget(r, 6, _cell(button("Show in folder", "secondary", small=True,
                                               slot=lambda _=False, p=x["local_path"]: show_in_folder(Path(p)))))
        self.tabs.setTabText(1, f"Received files ({len(rows)} · {fmt_bytes(sum(x['size'] for x in rows))})")

    def _open_received(self, row):
        if 0 <= row < len(self._received_rows):
            open_file(Path(self._received_rows[row]["local_path"]))

    def _reload_stats(self):
        fid = self.folder["folder_id"]
        st = next((f.get("stats") for f in self.core.overview.get("owned", []) if f["folder_id"] == fid), None) or {}
        local = self.core.received_files(fid)
        senders = {x["sender_uid"] for x in local}
        text = (f"<b>{len(local)}</b> file(s) received · <b>{fmt_bytes(sum(x['size'] for x in local))}</b> · "
                f"{len(senders)} sender(s) · last: {fmt_time(local[0]['completed_at']) if local else '-'}")
        if st.get("waiting"):
            text += f" · <span style='color:{C['warn']}'><b>{st['waiting']}</b> waiting to arrive</span>"
        if st.get("in_progress"):
            text += f" · <span style='color:{C['info']}'>{st['in_progress']} arriving now</span>"
        self.stats.setText(text)
        self.tabs.setTabText(2, f"Waiting to arrive ({st.get('waiting', 0)})" if st.get("waiting")
                             else "Waiting to arrive")

    def _reload_waiting(self):
        if not self.folder:
            return
        fid = self.folder["folder_id"]

        def done(res):
            if not self.folder or self.folder["folder_id"] != fid:
                return
            rows = [x for x in res.get("files", []) if x["status"] not in ("completed", "cancelled")]
            t = self.waiting
            t.clearContents()
            t.setRowCount(len(rows))
            for r, x in enumerate(rows):
                s = x.get("sender") or {}
                t.setItem(r, 0, _name_item(x.get("relative_path") or x["file_name"],
                                           icons.icon_for_name(x["file_name"], False)))
                t.setItem(r, 1, QTableWidgetItem(s.get("display_name") or s.get("username") or "-"))
                t.setItem(r, 2, QTableWidgetItem(fmt_bytes(x["file_size"])))
                status = {"queued": "Waiting (sender's PC)", "failed": "Failed - will retry"}.get(
                    x["status"], f"{x['status'].title()} {x.get('progress', 0):.0f}%")
                t.setItem(r, 3, QTableWidgetItem(status))
                t.setItem(r, 4, QTableWidgetItem(fmt_time(x.get("started_at"))))
            self.tabs.setTabText(2, f"Waiting to arrive ({len(rows)} · {fmt_bytes(sum(x['file_size'] for x in rows))})"
                                 if rows else "Waiting to arrive")
        run_bg(lambda: self.core.folder_files_cloud(fid), ok=done, err=lambda e: None)

    def _update_arriving(self):
        """Files other users are sending into this folder right now: sizes, speed, time left."""
        if not self.folder or not self.isVisible():
            return
        name = self.folder["name"]
        snaps = [j.snapshot() for j in self.core.jobs.values()
                 if j.kind == "receive" and j.share_name == name and j.state == "running"]
        if not snaps:
            self.arriving.hide()
            self.arriving_bar.hide()
            return
        text, done, total = sum_line(snaps, "↓ Receiving")
        who = ", ".join(sorted({f"{s['title']} from {s['peer_name']} ({s['files_done']}/{s['files']} files)"
                                for s in snaps}))
        self.arriving.setText(f"<span style='color:{C['info']}'>{text}</span><br>{who}")
        set_bar(self.arriving_bar, done, total)
        self.arriving.show()
        self.arriving_bar.show()

    def on_received(self, data):
        if self.folder and data.get("folder_id") == self.folder["folder_id"]:
            self.reload()

    def _open_entry(self, e):
        if e["dir"]:
            self.rel = f"{self.rel}/{e['name']}".strip("/")
            self.reload()

    def go_up(self):
        self.rel = "/".join(self.rel.split("/")[:-1])
        self.reload()

    def open_local(self):
        if self.folder:
            show_in_folder(self._path())

    def new_subfolder(self):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name.strip():
            try:
                (self._path() / sanitize_filename(name.strip())).mkdir()
            except OSError as exc:
                show_error(exc, self)
            self.reload()

    def _copy_in(self, sources):
        dest = self._path()

        def work():
            for s in sources:
                target = dest / s.name
                if target.exists():
                    raise FileExistsError(f"{s.name} already exists here")
                shutil.copytree(s, target) if s.is_dir() else shutil.copy2(s, target)
            return len(sources)
        self.main.flash(f"Copying {len(sources)} item(s)...")
        run_bg(work, ok=lambda n: (self.main.flash(f"Added {n} item(s)."), self.reload()),
               err=lambda e: (show_error(e, self), self.reload()))

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add files")
        if files:
            self._copy_in([Path(x) for x in files])

    def add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Add a folder (copied)")
        if d:
            self._copy_in([Path(d)])

    def _files_menu(self, pos):
        """Right-click on a file or folder inside the shared folder."""
        if self.files.itemAt(pos) is None:
            return
        row = self.files.itemAt(pos).row()
        if not self.files.item(row, 0).isSelected():
            self.files.selectRow(row)
        sel = self.files.selected_entries()
        if not sel:
            return
        menu = QMenu(self)
        if len(sel) == 1 and sel[0]["dir"]:
            menu.addAction(icons.folder_icon(True), "Open", lambda: self._open_entry(sel[0]))
            menu.addAction(icons.icon("plus", C["accent"], size=16), "Share this folder...",
                           lambda: self.share_subfolder(sel[0]["name"]))
        else:
            menu.addAction(icons.icon("open", C["text"], size=16), "Open", self.open_selected)
        menu.addAction(icons.icon("open", C["text"], size=16), "Show in file explorer",
                       lambda: show_in_folder(self._path() / sel[0]["name"]))
        menu.addSeparator()
        if len(sel) == 1:
            menu.addAction(icons.icon("rename", C["text"], size=16), "Rename...", self.rename_selected)
        menu.addAction(icons.icon("trash", C["bad"], size=16), "Delete...", self.delete_selected)
        menu.exec(self.files.viewport().mapToGlobal(pos))

    def share_subfolder(self, name: str):
        """Share a folder inside this one with other people: it becomes its own shared folder (own ID and
        permissions); the outer folder's members are not affected."""
        path = (self._path() / name).resolve()
        existing = next((f for f in self.core.store.list() if Path(f["path"]).resolve() == path), None)
        if existing is not None:                      # already shared on its own: just open Share for it
            self.refresh_folders(existing["folder_id"])
            return self.give_access()
        if not confirm(self, "Share folder", f"Share '{name}' with other people?\n\nIt gets its own folder ID "
                                             "and its own list of people and permissions."):
            return

        def done(fid):
            self.refresh_folders(fid)
            self.main.flash(f"'{name}' is now a shared folder: {fid}")
            self.give_access()
        run_bg(lambda: self.core.create_folder(name, path), ok=done)

    def open_selected(self):
        sel = self.files.selected_entries()
        if len(sel) == 1 and sel[0]["dir"]:
            return self._open_entry(sel[0])
        for e in sel:
            open_file(self._path() / e["name"])

    def rename_selected(self):
        sel = self.files.selected_entries()
        if len(sel) != 1:
            return show_error("Select one item to rename.", self, "Rename")
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=sel[0]["name"])
        if ok and name.strip() and name.strip() != sel[0]["name"]:
            try:
                target = self._path() / sanitize_filename(name.strip())
                if target.exists():
                    raise FileExistsError(f"{target.name} already exists here")
                (self._path() / sel[0]["name"]).rename(target)
            except OSError as exc:
                show_error(exc, self)
            self.reload()

    def delete_selected(self):
        sel = self.files.selected_entries()
        if not sel or not confirm(self, "Delete", f"Permanently delete {len(sel)} item(s) from this computer?", True):
            return
        for e in sel:
            p = self._path() / e["name"]
            try:
                shutil.rmtree(p) if e["dir"] else p.unlink()
            except OSError as exc:
                show_error(exc, self)
        self.reload()

    # ------------------------------------------------------------ members
    def _reload_members(self):
        fid = self.folder["folder_id"]
        rows = self.core.store.members(fid)
        self.members.clearContents()              # also drops the old buttons / checkboxes
        self.members.setRowCount(0)
        self.members.setRowCount(len(rows))
        import datetime as dt
        for r, m in enumerate(rows):
            online = self.core.is_online(m["uid"])
            self.members.setItem(r, 0, QTableWidgetItem(icons.avatar(m["name"], online, 26),
                                                        f"{m['name']}  (@{m['username']})"))
            self.members.setItem(r, 1, QTableWidgetItem(m["email"]))
            st = QTableWidgetItem({"active": "Joined", "declined": "Declined"}.get(m["status"], "Invited"))
            st.setForeground(icons_color({"active": C["ok"], "declined": C["bad"]}.get(m["status"], C["warn"])))
            self.members.setItem(r, 2, st)
            role = m["role"].title() + (f" ({m['via_group']})" if m["via_group"] else "")
            self.members.setItem(r, 3, QTableWidgetItem(role))
            boxes = []
            for col, key in ((4, "can_read"), (5, "can_upload"), (6, "can_edit"), (7, "can_delete")):
                cb = QCheckBox()
                cb.setChecked(bool(m[key]))
                boxes.append(cb)
                self.members.setCellWidget(r, col, _cell(cb))
            for cb in boxes:
                cb.toggled.connect(lambda _v, uid=m["uid"], boxes=boxes: self._set_perms(uid, boxes))
            exp = m["expires_at"]
            txt = dt.datetime.fromtimestamp(exp).strftime("%Y-%m-%d") if exp else "-"
            if exp and exp < dt.datetime.now().timestamp():
                txt += " (expired)"
            self.members.setItem(r, 8, QTableWidgetItem(txt))
            self.members.setCellWidget(r, 9, _cell(button("Resend", "secondary", small=True,
                                                          slot=lambda _=False, uid=m["uid"]: self._resend(uid))))
            self.members.setCellWidget(r, 10, _cell(button("Remove", "danger", small=True,
                                                           slot=lambda _=False, uid=m["uid"]: self._remove(uid))))

    def _set_perms(self, uid, boxes):
        perms = dict(zip(("read", "upload", "edit", "delete"), (b.isChecked() for b in boxes)))
        fid = self.folder["folder_id"]
        if not any(perms.values()):
            return self._remove(uid)
        run_bg(lambda: self.core.update_member(fid, uid, perms), ok=lambda _m: self.reload(),
               err=lambda e: (show_error(e, self), self.reload()))

    def _resend(self, uid):
        fid = self.folder["folder_id"]
        run_bg(lambda: self.core.resend_invite(fid, uid),
               ok=lambda r: self.main.flash("E-mail sent." if r.get("sent") else f"E-mail failed: {r.get('error')}"))

    def _remove(self, uid):
        fid = self.folder["folder_id"]
        name = self.core.contacts.get(uid, {}).get("name", uid)
        if confirm(self, "Remove access", f"Remove {name}'s access to '{self.folder['name']}'?", True):
            run_bg(lambda: self.core.remove_member(fid, uid), ok=lambda _r: self.refresh_folders(fid))
        else:
            self.reload()

    def give_access(self, preselect: str | None = None):
        if not self.folder:
            return
        d = ShareDialog(self, self.core, self.folder, preselect)
        if d.exec() != QDialog.Accepted:
            return
        chosen, role, perms, expires = d.values()
        fid = self.folder["folder_id"]

        def job():
            failed = []
            for kind, ident in chosen:
                try:
                    if kind == "group":
                        self.core.add_group_to_folder(fid, ident, "manager" if role == "custom" else role, expires)
                    else:
                        self.core.set_member(fid, ident, role=None if role == "custom" else role, perms=perms,
                                             expires_at=expires)
                except Exception as exc:                       # keep going: share with the others
                    failed.append(f"{ident}: {exc}")
            return len(chosen) - len(failed), failed

        def done(res):
            ok, failed = res
            self.refresh_folders(fid)
            self.main.flash(f"Shared with {ok} - they get an e-mail and see it in their app.")
            if failed:
                show_error("Could not share with:\n" + "\n".join(failed), self, "Share")
        run_bg(job, ok=done)

    def on_presence(self):
        if self.folder:
            self._reload_members()
            self._reload_stats()


def icons_color(hexcolor):
    from PySide6.QtGui import QColor
    return QColor(hexcolor)


# ================================================================== Users (admin)
class UsersView(QWidget):
    def __init__(self, core, main):
        super().__init__()
        self.core, self.main = core, main
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        w, lay = page("Users")
        outer.addWidget(w)
        me = core.me or {}
        pid = me.get("public_id", "")
        self.query = QLineEdit(placeholderText="User ID, username or e-mail")
        self.note = QLineEdit(placeholderText="Note, e.g. Editor - Marketing")
        self.query.returnPressed.connect(self.add)
        lay.addWidget(card(label("ADD A USER", "SectionTitle"),
                           _w(hbox(self.query, self.note, button("Add user", "primary", "plus", self.add))),
                           label(f"Your own ID: {pid[:3]} {pid[3:6]} {pid[6:]}", muted=True)))
        self.split = Sash("users.groups", Qt.Vertical, collapse=1, sizes=[380, 220], minimums=[120, 0])
        top, tl = QWidget(), QVBoxLayout()
        tl.setContentsMargins(0, 0, 0, 0)
        top.setLayout(tl)
        tl.addWidget(label("MY USERS", "SectionTitle"))
        self.table = table(["Status", "Name", "Username", "E-mail", "ID", "Note", "Folders", "", ""], stretch=1,
                           fixed={7: 84, 8: 92}, row_height=42)
        tl.addWidget(self.table, 1)
        bottom, bl = QWidget(), QVBoxLayout()
        bl.setContentsMargins(0, 6, 0, 0)
        bottom.setLayout(bl)
        bl.addLayout(hbox(label("GROUPS", "SectionTitle"), None,
                          button("New group", "secondary", "plus", self.new_group, small=True)))
        self.groups = table(["Group", "Members", "", "", ""], stretch=1, fixed={2: 110, 3: 130, 4: 92}, row_height=42)
        bl.addWidget(self.groups, 1)
        self.split.addWidget(top)
        self.split.addWidget(bottom)
        lay.addWidget(self.split, 1)
        self.reload()

    def toggle_panel(self):
        self.split.toggle_collapse()

    def add(self):
        q = self.query.text().strip()
        if not q:
            return
        note = self.note.text().strip()

        def done(u):
            self.query.clear()
            self.note.clear()
            self.main.flash(f"{u.get('display_name') or u['username']} added. Give them access in Folders.")
            self.reload()
        run_bg(lambda: self.core.add_user(q, note), ok=done)

    def reload(self):
        clients = self.core.overview.get("clients", [])
        counts = {}
        for f in self.core.store.list():
            for m in f["members"]:
                counts[m["uid"]] = counts.get(m["uid"], 0) + 1
        self.table.clearContents()
        self.table.setRowCount(0)
        self.table.setRowCount(len(clients))
        for r, u in enumerate(sorted(clients, key=lambda x: (not self.core.is_online(x["public_id"]),
                                                             (x["display_name"] or x["username"]).lower()))):
            uid = u["public_id"]
            online = self.core.is_online(uid)
            self.table.setItem(r, 0, QTableWidgetItem(icons.dot(C["ok"] if online else C["offline_dot"]),
                                                      "Online" if online else "Offline"))
            self.table.setItem(r, 1, QTableWidgetItem(icons.avatar(u["display_name"] or u["username"], online, 26),
                                                      u["display_name"] or u["username"]))
            self.table.setItem(r, 2, QTableWidgetItem("@" + u["username"]))
            self.table.setItem(r, 3, QTableWidgetItem(u.get("email", "")))
            self.table.setItem(r, 4, QTableWidgetItem(f"{uid[:3]} {uid[3:6]} {uid[6:]}"))
            self.table.setItem(r, 5, QTableWidgetItem(u.get("note") or "-"))
            self.table.setItem(r, 6, QTableWidgetItem(str(counts.get(uid, 0))))
            self.table.setCellWidget(r, 7, _cell(button("Chat", "secondary", "chat", small=True,
                                                        slot=lambda _=False, x=uid: self.main.open_chat(x))))
            self.table.setCellWidget(r, 8, _cell(button("Remove", "danger", small=True,
                                                        slot=lambda _=False, x=uid: self.remove(x))))
        groups = self.core.overview.get("groups", [])
        self.groups.clearContents()
        self.groups.setRowCount(0)
        self.groups.setRowCount(len(groups))
        for r, g in enumerate(groups):
            self.groups.setItem(r, 0, QTableWidgetItem(icons.icon("contacts", C["info"], size=16), g["name"]))
            self.groups.setItem(r, 1, QTableWidgetItem(", ".join(m["display_name"] or m["username"]
                                                                 for m in g["members"]) or "-"))
            self.groups.setCellWidget(r, 2, _cell(button("Add user", "secondary", small=True,
                                                         slot=lambda _=False, gg=g: self.group_add(gg))))
            self.groups.setCellWidget(r, 3, _cell(button("Remove user", "secondary", small=True,
                                                         slot=lambda _=False, gg=g: self.group_remove(gg))))
            self.groups.setCellWidget(r, 4, _cell(button("Delete", "danger", small=True,
                                                         slot=lambda _=False, gg=g: self.group_delete(gg))))

    def remove(self, uid):
        name = self.core.contacts.get(uid, {}).get("name", uid)
        if confirm(self, "Remove user", f"Remove {name}? They lose access to all your folders.", True):
            run_bg(lambda: self.core.remove_user(uid), ok=lambda _r: self.reload())

    def new_group(self):
        name, ok = QInputDialog.getText(self, "New group", "Group name (e.g. Editors):")
        if ok and name.strip():
            run_bg(lambda: self.core.create_group(name.strip()), ok=lambda _g: self.reload())

    def _pick_user(self, title, candidates):
        if not candidates:
            show_error("No users to choose from.", self)
            return None
        labels = [f"{u['display_name'] or u['username']} (@{u['username']})" for u in candidates]
        choice, ok = QInputDialog.getItem(self, title, "User:", labels, 0, False)
        return candidates[labels.index(choice)]["public_id"] if ok else None

    def group_add(self, g):
        have = {m["public_id"] for m in g["members"]}
        uid = self._pick_user(f"Add to {g['name']}", [u for u in self.core.overview.get("clients", [])
                                                        if u["public_id"] not in have])
        if uid:
            run_bg(lambda: self.core.update_group(g["id"], add=[uid]), ok=lambda _g: self.reload())

    def group_remove(self, g):
        uid = self._pick_user(f"Remove from {g['name']}", g["members"])
        if uid:
            run_bg(lambda: self.core.update_group(g["id"], remove=[uid]), ok=lambda _g: self.reload())

    def group_delete(self, g):
        if confirm(self, "Delete group", f"Delete the group '{g['name']}'? Access already given stays.", True):
            run_bg(lambda: self.core.delete_group(g["id"]), ok=lambda _r: self.reload())

    def on_presence(self):
        self.reload()
