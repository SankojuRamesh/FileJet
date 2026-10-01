"""File/folder table used by 'Shared folders' (owner) and 'Shared with me' (client)."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from ..util import fmt_bytes
from . import icons

ROLE_ENTRY = Qt.UserRole + 1


class SortItem(QTableWidgetItem):
    """Folders first, then by the numeric/text sort key."""

    def __lt__(self, other):
        a, b = self.data(Qt.UserRole), other.data(Qt.UserRole)
        if a is None or b is None:
            return super().__lt__(other)
        return a < b


class FileTable(QTableWidget):
    opened = Signal(dict)          # double-click / Enter on an entry
    selection_changed = Signal()

    HEADERS = ["Name", "Size", "Modified", "Type"]

    def __init__(self):
        super().__init__(0, len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(26)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSortingEnabled(True)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.cellDoubleClicked.connect(lambda r, _c: self._open(r))
        self.itemSelectionChanged.connect(self.selection_changed.emit)
        self.placeholder = ""

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentRow() >= 0:
            self._open(self.currentRow())
            return
        super().keyPressEvent(e)

    def _open(self, row: int):
        item = self.item(row, 0)
        if item is not None and item.data(ROLE_ENTRY):
            self.opened.emit(item.data(ROLE_ENTRY))

    def set_entries(self, entries: list[dict]) -> None:
        self.setSortingEnabled(False)
        self.setRowCount(0)
        self.setRowCount(len(entries))
        for r, e in enumerate(entries):
            is_dir = bool(e.get("dir"))
            name = SortItem(icons.icon_for_name(e["name"], is_dir), e["name"])
            name.setData(Qt.UserRole, (0 if is_dir else 1, e["name"].lower()))
            name.setData(ROLE_ENTRY, e)
            size = SortItem("" if is_dir else fmt_bytes(e.get("size", 0)))
            size.setData(Qt.UserRole, (0 if is_dir else 1, e.get("size", 0)))
            size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            mtime = e.get("mtime")
            mod = SortItem(dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "")
            mod.setData(Qt.UserRole, (0 if is_dir else 1, mtime or 0))
            kind = SortItem(icons.kind_label(e["name"], is_dir))
            kind.setData(Qt.UserRole, (0 if is_dir else 1, kind.text()))
            for c, it in enumerate((name, size, mod, kind)):
                self.setItem(r, c, it)
        self.setSortingEnabled(True)
        self.sortItems(0, Qt.AscendingOrder)

    def selected_entries(self) -> list[dict]:
        rows = sorted({i.row() for i in self.selectedIndexes()})
        out = []
        for r in rows:
            it = self.item(r, 0)
            if it is not None and it.data(ROLE_ENTRY):
                out.append(it.data(ROLE_ENTRY))
        return out
