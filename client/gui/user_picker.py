"""Drop-down of your users and groups for the Share window: opens on any click, filters while typing, and each
user row has a × to remove that user from your Users list."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QCompleter, QStyledItemDelegate

from .theme import C

X_SIZE = 20


def _x_rect(item_rect: QRect) -> QRect:
    return QRect(item_rect.right() - X_SIZE - 10, item_rect.center().y() - X_SIZE // 2, X_SIZE, X_SIZE)


class _RowDelegate(QStyledItemDelegate):
    """Normal row + a × button on user rows (red when the mouse is on it)."""

    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(max(s.height(), 34))
        return s

    def paint(self, p, opt, index):
        is_user = str(index.data(Qt.UserRole) or "").startswith("user:")
        if is_user:
            p.save()
            p.setClipRect(opt.rect.adjusted(0, 0, -(X_SIZE + 16), 0))   # text never runs under the ×
        super().paint(p, opt, index)
        if not is_user:
            return
        p.restore()
        r = _x_rect(opt.rect)
        hot = self.combo.hot_row == index.row()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if hot:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["bad"]).darker(260))
            p.drawEllipse(QRectF(r))
        pen = QPen(QColor(C["bad"] if hot else C["muted"]), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        m = 6
        p.drawLine(r.left() + m, r.top() + m, r.right() - m, r.bottom() - m)
        p.drawLine(r.right() - m, r.top() + m, r.left() + m, r.bottom() - m)
        p.restore()


class UserPicker(QComboBox):
    remove_requested = Signal(str)          # "user:<id>"

    def __init__(self, placeholder: str = ""):
        super().__init__()
        self.hot_row = -1
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setMaxVisibleItems(12)
        self.lineEdit().setPlaceholderText(placeholder)
        self.lineEdit().installEventFilter(self)
        comp = QCompleter(self.model(), self)                # type part of a name, @username or ID
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        self.setCompleter(comp)
        self.view().setItemDelegate(_RowDelegate(self))
        self.view().setMouseTracking(True)

    def showPopup(self):
        if self.count() == 0:
            return
        fm = self.view().fontMetrics()
        widest = max(fm.horizontalAdvance(self.itemText(i)) for i in range(self.count()))
        self.view().setMinimumWidth(max(self.width(), widest + 24 + 20 + X_SIZE + 24))
        super().showPopup()
        vp = self.view().viewport()
        vp.removeEventFilter(self)
        vp.installEventFilter(self)         # installed last = runs first: the × click never picks the row

    def eventFilter(self, obj, ev):
        if obj is self.lineEdit():
            if ev.type() == QEvent.MouseButtonPress and not self.lineEdit().text():
                self.showPopup()            # click anywhere in the box: show all users
            return False
        if obj is self.view().viewport():
            t = ev.type()
            if t in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                pos = ev.position().toPoint()
                idx = self.view().indexAt(pos)
                on_x = (idx.isValid() and str(idx.data(Qt.UserRole) or "").startswith("user:")
                        and _x_rect(self.view().visualRect(idx)).contains(pos))
                hot = idx.row() if on_x else -1
                if hot != self.hot_row:
                    self.hot_row = hot
                    self.view().viewport().update()
                if on_x and t in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                    if t == QEvent.MouseButtonRelease:
                        self.remove_requested.emit(str(idx.data(Qt.UserRole)))
                    return True             # do not select the row
            elif t == QEvent.Leave and self.hot_row != -1:
                self.hot_row = -1
                self.view().viewport().update()
        return super().eventFilter(obj, ev)
