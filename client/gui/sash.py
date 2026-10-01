"""VS Code-style resizable panes ("sashes").

* Drag the thin line between two panes with the left mouse button to resize them; it lights up blue on
  hover and while dragging, and shows a resize cursor.
* Double-click the line to collapse / restore the collapsible pane (e.g. the side bar).
* Sizes are remembered per pane (and per profile) across restarts: ``layout.json`` in the data folder.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSplitter, QSplitterHandle

from .theme import C

log = logging.getLogger("p2p.gui")


class LayoutStore:
    """Pane sizes and window geometry, saved (debounced) to ``<data dir>/layout.json``."""

    def __init__(self):
        self.path: Path | None = None
        self.data: dict = {}
        self._timer: QTimer | None = None

    def load(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "layout.json"
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        if self._timer is None:
            self._timer = QTimer()
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self.save)
        self._timer.start(400)

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1))
        except OSError as exc:
            log.debug("layout not saved: %s", exc)

    def reset(self) -> None:
        self.data = {k: v for k, v in self.data.items() if k == "window"}
        self.save()


LAYOUT = LayoutStore()
ALL: list["Sash"] = []


class SashHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._hover = False
        self._drag = False
        self.setCursor(Qt.SplitHCursor if orientation == Qt.Horizontal else Qt.SplitVCursor)
        self.setMouseTracking(True)
        pass

    def event(self, ev):
        t = ev.type()
        if t == QEvent.Enter:
            self._hover = True
            QTimer.singleShot(0, self.update)
        elif t == QEvent.Leave:
            self._hover = False
            self.update()
        elif t == QEvent.MouseButtonPress:
            self._drag = True
            self.update()
        elif t == QEvent.MouseButtonRelease:
            self._drag = False
            self.update()
        return super().event(ev)

    def mouseDoubleClickEvent(self, ev):
        self.splitter().toggle_collapse()

    def paintEvent(self, _ev):
        p = QPainter(self)
        r = self.rect()
        # a 1px divider normally; a 4px accent bar while hovering / dragging (like VS Code)
        if self._hover or self._drag:
            p.fillRect(r, QColor(C["selection_border"]))
        else:
            p.fillRect(r, QColor(C["editor"]))
            if self.orientation() == Qt.Horizontal:
                p.fillRect(r.width() // 2, 0, 1, r.height(), QColor(C["border"]))
            else:
                p.fillRect(0, r.height() // 2, r.width(), 1, QColor(C["border"]))
        p.end()


class Sash(QSplitter):
    """QSplitter with VS Code-like handles, remembered sizes and collapse on double-click.

    ``name``      key under which the sizes are remembered
    ``collapse``  index of the pane that double-click collapses (the side bar / bottom panel)
    ``sizes``     default sizes (pixels) when nothing is remembered yet
    """

    def __init__(self, name: str, orientation=Qt.Horizontal, collapse: int | None = 0,
                 sizes: list[int] | None = None,
                 minimums: list[int] | None = None, parent=None):
        super().__init__(orientation, parent)
        self.name, self.collapse_index = name, collapse
        self.default_sizes = sizes
        self.minimums = minimums or []
        self._restored = False
        self._last_open: list[int] | None = None
        self.setHandleWidth(5)
        self.setChildrenCollapsible(True)
        self.setOpaqueResize(True)
        self.splitterMoved.connect(lambda _p, _i: self._remember())
        ALL.append(self)

    def createHandle(self):
        return SashHandle(self.orientation(), self)

    def addWidget(self, w):
        super().addWidget(w)
        i = self.count() - 1
        if i < len(self.minimums) and self.minimums[i]:
            if self.orientation() == Qt.Horizontal:
                w.setMinimumWidth(self.minimums[i])
            else:
                w.setMinimumHeight(self.minimums[i])
        self.setCollapsible(i, self.collapse_index is not None and i == self.collapse_index)
        self.setStretchFactor(i, 0 if i == self.collapse_index else 1)

    # ------------------------------------------------------------ remember / restore
    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._restored:
            self._restored = True
            saved = self._store().get(f"sash:{self.name}")
            if isinstance(saved, list) and len(saved) == self.count() and sum(saved) > 0:
                self.setSizes([int(x) for x in saved])
            elif self.default_sizes:
                self.apply_defaults()

    def apply_defaults(self):
        """Default sizes: the collapsible pane gets its default width, the others share the rest."""
        if not self.default_sizes:
            return
        total = sum(self.sizes()) or sum(self.default_sizes)
        flex = [i for i in range(self.count()) if i != self.collapse_index]
        sizes = list(self.default_sizes)
        fixed = sum(sizes[i] for i in range(self.count()) if i not in flex)
        wsum = sum(self.default_sizes[i] for i in flex) or 1
        for i in flex:
            sizes[i] = int(max(total - fixed, 100) * self.default_sizes[i] / wsum)
        self.setSizes(sizes)

    def _store(self) -> "LayoutStore":
        return getattr(self.window(), "layout_store", None) or LAYOUT

    def _remember(self):
        sizes = self.sizes()
        if self.collapse_index is None or sizes[self.collapse_index] > 0:
            self._last_open = list(sizes)
        self._store().set(f"sash:{self.name}", sizes)

    # ------------------------------------------------------------ collapse
    def is_collapsed(self) -> bool:
        return self.collapse_index is not None and self.sizes()[self.collapse_index] == 0

    def toggle_collapse(self):
        if self.collapse_index is None:
            return
        sizes = self.sizes()
        i = self.collapse_index
        if sizes[i] > 0:
            self._last_open = list(sizes)
            other = 1 if i == 0 else i - 1
            sizes[other] += sizes[i]
            sizes[i] = 0
        else:
            want = (self._last_open or self.default_sizes or [250] * self.count())[i] or 250
            other = 1 if i == 0 else i - 1
            want = min(want, max(sizes[other] - 150, 120))
            sizes[other] -= want
            sizes[i] = want
        self.setSizes(sizes)
        self._remember()


def reset_all(window=None) -> None:
    """Back to the default layout (Settings -> Reset layout)."""
    (getattr(window, "layout_store", None) or LAYOUT).reset()
    for sash in list(ALL):
        try:
            if window is None or sash.window() is window:
                sash.apply_defaults()
        except RuntimeError:          # widget already deleted (after sign-out)
            ALL.remove(sash)
