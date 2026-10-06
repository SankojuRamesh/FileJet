"""Painting of the Workspaces tree: each workspace as a card (name, size, users, drive space bar) and its folders
as slim rows with their size."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ..shares import SIZER, resolve_in_share
from ..util import fmt_bytes
from . import icons
from .theme import C

CARD_H, ROW_H = 84, 32


def _col(name: str, alpha: int | None = None) -> QColor:
    c = QColor(C.get(name, name))
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def _rounded(p: QPainter, rect: QRectF, radius: float, fill: QColor, border: QColor | None = None):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.fillPath(path, fill)
    if border is not None:
        p.setPen(border)
        p.drawPath(path)


class WorkspaceDelegate(QStyledItemDelegate):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner          # AdminFoldersView: gives the folders and the drive figures

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), CARD_H if not index.parent().isValid() else ROW_H)

    def paint(self, p: QPainter, opt, index):
        data = index.data(Qt.UserRole)
        if not data:
            return super().paint(p, opt, index)
        fid, rel = data
        f = self.owner.core.store.get(fid)
        if f is None:
            return super().paint(p, opt, index)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        selected = bool(opt.state & QStyle.State_Selected)
        hover = bool(opt.state & QStyle.State_MouseOver)
        if rel:
            self._paint_folder(p, opt, f, rel, selected, hover)
        else:
            self._paint_workspace(p, opt, f, selected, hover)
        p.restore()

    # ------------------------------------------------------------ workspace card
    def _paint_workspace(self, p, opt, f, selected, hover):
        r = QRectF(opt.rect).adjusted(2, 4, -6, -4)
        bg = _col("accent_soft") if selected else _col("hover") if hover else _col("panel")
        _rounded(p, r, 11, bg, _col("selection_border") if selected else _col("border"))

        tile = QRectF(r.left() + 10, r.top() + 12, 34, 34)
        _rounded(p, tile, 9, _col("folder", 40))
        p.drawPixmap(int(tile.left() + 7), int(tile.top() + 7), icons.folder_icon(size=20).pixmap(20, 20))

        base = QFont(opt.font)
        left, right = tile.right() + 10, r.right() - 10
        size = SIZER.peek(f["path"])

        # line 1: the name, full width
        name_f = QFont(base)
        name_f.setPointSizeF(base.pointSizeF() * 1.08)
        name_f.setBold(True)
        p.setFont(name_f)
        p.setPen(_col("strong"))
        name_rect = QRectF(left, r.top() + 8, right - left, 22)
        p.drawText(name_rect, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(name_f).elidedText(f["name"], Qt.ElideRight, int(name_rect.width())))

        # line 2: kind tag · users ......... size pill
        small = QFont(base)
        small.setPointSizeF(base.pointSizeF() * 0.85)
        sfm = QFontMetrics(small)
        y2 = r.top() + 32
        pill_f = QFont(small)
        pill_f.setBold(True)
        pill_text = fmt_bytes(size[0]) if size else "…"
        pw = QFontMetrics(pill_f).horizontalAdvance(pill_text) + 14
        pill = QRectF(right - pw, y2, pw, 18)
        _rounded(p, pill, 9, _col("accent", 46))
        p.setFont(pill_f)
        p.setPen(_col("info"))
        p.drawText(pill, Qt.AlignCenter, pill_text)

        p.setFont(small)
        kind_col = _col("warn") if f["kind"] == "submit" else _col("ok")
        p.setPen(Qt.NoPen)
        p.setBrush(kind_col)
        p.drawEllipse(QRectF(left, y2 + 5.5, 7, 7))
        kind = f["kind"].title()
        p.setPen(kind_col)
        p.drawText(QRectF(left + 12, y2, 200, 18), Qt.AlignVCenter | Qt.AlignLeft, kind)
        x = left + 12 + sfm.horizontalAdvance(kind)
        n = len(self.owner.core.store.members(f["folder_id"]))    # store.get() has no members
        p.setPen(_col("muted"))
        rest = f"  ·  {n} user{'s' if n != 1 else ''}"
        p.drawText(QRectF(x, y2, pill.left() - x - 6, 18), Qt.AlignVCenter | Qt.AlignLeft,
                   sfm.elidedText(rest, Qt.ElideRight, int(max(0.0, pill.left() - x - 6))))

        # line 3: drive space bar + "free" label
        disk = self.owner.disk_info(f)
        y3 = r.top() + 60
        label = f"{fmt_bytes(disk[2])} free" if disk else ""
        lw = sfm.horizontalAdvance(label) + (10 if label else 0)
        bar = QRectF(left, y3 + 5, max(20.0, right - left - lw), 5)
        _rounded(p, bar, 2.5, _col("border_strong"))
        if disk and disk[1]:
            used = min(1.0, disk[0] / disk[1])
            fill = _col("ok") if used < 0.75 else _col("warn") if used < 0.9 else _col("bad")
            _rounded(p, QRectF(bar.left(), bar.top(), max(5.0, bar.width() * used), bar.height()), 2.5, fill)
        if label:
            p.setPen(_col("muted"))
            p.drawText(QRectF(bar.right() + 8, y3, lw, 16), Qt.AlignVCenter | Qt.AlignLeft, label)

    # ------------------------------------------------------------ folder row
    def _paint_folder(self, p, opt, f, rel, selected, hover):
        r = QRectF(opt.rect).adjusted(0, 2, -6, -2)
        if selected:
            _rounded(p, r, 7, _col("accent_soft"))
            _rounded(p, QRectF(r.left(), r.top() + 6, 3, r.height() - 12), 1.5, _col("accent"))
        elif hover:
            _rounded(p, r, 7, _col("hover"))
        is_open = bool(opt.state & QStyle.State_Open)
        p.drawPixmap(int(r.left() + 10), int(r.center().y() - 8), icons.folder_icon(is_open, 16).pixmap(16, 16))
        base = QFont(opt.font)
        small = QFont(base)
        small.setPointSizeF(base.pointSizeF() * 0.85)
        try:
            size = SIZER.peek(resolve_in_share(f["path"], rel))
        except (OSError, ValueError):
            size = None
        size_txt = fmt_bytes(size[0]) if size else ""
        sw = QFontMetrics(small).horizontalAdvance(size_txt)
        p.setFont(small)
        p.setPen(_col("muted"))
        p.drawText(QRectF(r.right() - sw - 10, r.top(), sw + 4, r.height()), Qt.AlignVCenter | Qt.AlignRight,
                   size_txt)
        shared = self.owner.subshare_at(f["folder_id"], rel)
        if shared is not None:                        # this folder alone is shared with people
            n = len(self.owner.core.store.members(shared["folder_id"]))
            badge_f = QFont(small)
            badge_f.setBold(True)
            text = f"Shared · {n}"
            bw = QFontMetrics(badge_f).horizontalAdvance(text) + 14
            badge = QRectF(r.right() - sw - 20 - bw, r.center().y() - 9, bw, 18)
            _rounded(p, badge, 9, _col("ok", 40))
            p.setFont(badge_f)
            p.setPen(_col("ok"))
            p.drawText(badge, Qt.AlignCenter, text)
            sw += bw + 10
        name_f = QFont(base)
        name_f.setBold(selected)
        p.setFont(name_f)
        p.setPen(_col("strong") if selected else _col("text"))
        nr = QRectF(r.left() + 34, r.top(), r.width() - 34 - sw - 20, r.height())
        p.drawText(nr, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(name_f).elidedText(Path(rel).name, Qt.ElideRight, int(nr.width())))
