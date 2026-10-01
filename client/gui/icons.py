"""Line icons in the style of VS Code's activity bar, rendered from SVG at any DPI."""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .theme import C


def _gear() -> str:
    pts = []
    for i in range(16):
        a = math.pi * 2 * i / 16
        r = 9.5 if i % 2 == 0 else 7.3
        pts.append(f"{12 + r * math.cos(a):.2f},{12 + r * math.sin(a):.2f}")
    return f'<polygon points="{" ".join(pts)}"/><circle cx="12" cy="12" r="3"/>'


STROKE = {
    "files": '<path d="M14.5 3H8a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7.5z"/><path d="M14.5 3v4.5H19"/>'
             '<path d="M4 7v13a1 1 0 0 0 1 1h10"/>',
    "share": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15'
             'A1.5 1.5 0 0 1 3 18.5z"/><circle cx="15.5" cy="14" r="2"/><path d="M12 19a3.5 3.5 0 0 1 7 0"/>',
    "remote": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>'
              '<path d="M9.5 9.5l2.5 2.5 2.5-2.5M12 6.5v5.5"/>',
    "contacts": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/>'
                '<path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.6a6.5 6.5 0 0 1 3.5 5.4"/>',
    "chat": '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4 3.5V16h-.5A1.5 1.5 0 0 1 4 14.5z"/><path d="M8 9h8M8 12h5"/>',
    "transfers": '<path d="M7 20V4M3 8l4-4 4 4M17 4v16M13 16l4 4 4-4"/>',
    "account": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "settings": _gear(),
    "upload": '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
    "download": '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
    "new_folder": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15'
                  'A1.5 1.5 0 0 1 3 18.5z"/><path d="M12 11v6M9 14h6"/>',
    "add_file": '<path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7z"/><path d="M14 3v4h4M12 11v6M9 14h6"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    "rename": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
    "open": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "up": '<path d="M12 19V5M5 12l7-7 7 7"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "send": '<path d="M4 12l16-8-6 16-3-7z"/><path d="M11 13l9-9"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="1.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    "logout": '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H3"/>',
    "web": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="1.5"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.8-4.8"/>',
}

FILE_COLORS = {
    "image": "#a074c4", "video": "#e37933", "audio": "#cbcb41", "archive": "#d6a756", "code": "#519aba",
    "pdf": "#e05252", "doc": "#4b8bd4", "sheet": "#6fb56f", "disk": "#9da5b4", "file": "#9da5b4",
}
EXT_KIND = {}
for kind, exts in {
    "image": "png jpg jpeg gif bmp webp svg heic tif tiff raw",
    "video": "mp4 mkv mov avi webm wmv m4v flv",
    "audio": "mp3 wav flac aac ogg m4a opus",
    "archive": "zip 7z rar tar gz tgz bz2 xz zst",
    "code": "py js ts tsx jsx html css json xml yml yaml c cpp h cs java go rs php rb sh ps1 sql md",
    "pdf": "pdf", "doc": "doc docx odt rtf txt", "sheet": "xls xlsx csv ods", "disk": "iso img vhd vhdx dmg vmdk",
}.items():
    for e in exts.split():
        EXT_KIND[e] = kind


def _render(svg: str, size: int, dpr: float) -> QPixmap:
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.transparent)
    p = QPainter(px)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, size * dpr, size * dpr))
    p.end()
    px.setDevicePixelRatio(dpr)
    return px


@lru_cache(maxsize=512)
def icon(name: str, color: str = C["activity_icon"], active: str | None = None, size: int = 24) -> QIcon:
    body = STROKE.get(name, "")
    ic = QIcon()
    for dpr in (1.0, 1.5, 2.0):
        for mode, col in ((QIcon.Normal, color), (QIcon.Active, active or color), (QIcon.Selected, active or color)):
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{col}" '
                   f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
            ic.addPixmap(_render(svg, size, dpr), mode, QIcon.Off)
            if active:
                on = svg.replace(f'stroke="{col}"', f'stroke="{active}"')
                ic.addPixmap(_render(on, size, dpr), mode, QIcon.On)
    return ic


@lru_cache(maxsize=64)
def folder_icon(open_: bool = False, size: int = 16) -> QIcon:
    col = C["folder"]
    body = ('<path d="M2.5 5.5A1.5 1.5 0 0 1 4 4h4.2l1.8 2H20a1.5 1.5 0 0 1 1.5 1.5V18A1.5 1.5 0 0 1 20 19.5H4'
            'A1.5 1.5 0 0 1 2.5 18z" fill="%s"/><path d="M2.5 8.5h19" stroke="#b08b4f" stroke-width="1"/>' % col)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'
    ic = QIcon()
    for dpr in (1.0, 1.5, 2.0):
        ic.addPixmap(_render(svg, size, dpr))
    return ic


GLYPH = {   # drawn in the upper part of the page (viewBox 24x24)
    "video": '<path d="M9.5 6.5v6l5-3z" fill="{c}"/>',
    "image": '<circle cx="9" cy="7" r="1.4" fill="{c}"/><path d="M6.5 13l3.2-3.4 2 2 2.3-2.6 3 4z" fill="{c}"/>',
    "audio": '<path d="M13.5 5v6.2a1.8 1.8 0 1 1-1-1.6V6.5l3-1" fill="none" stroke="{c}" stroke-width="1.2"/>',
    "archive": '<path d="M11.5 3v2h1v2h-1v2h1v2h-1v2" fill="none" stroke="{c}" stroke-width="1.2"/>',
    "code": '<path d="M9.5 6.5l-2.5 3 2.5 3M14 6.5l2.5 3-2.5 3" fill="none" stroke="{c}" stroke-width="1.3"/>',
    "disk": '<circle cx="12" cy="9.5" r="3.5" fill="none" stroke="{c}" stroke-width="1.2"/><circle cx="12" cy="9.5" r=".9" fill="{c}"/>',
    "pdf": '<path d="M8 6.5h7M8 9h7M8 11.5h5" stroke="{c}" stroke-width="1.2"/>',
    "doc": '<path d="M8 6.5h7M8 9h7M8 11.5h5" stroke="{c}" stroke-width="1.2"/>',
    "sheet": '<path d="M7.5 6h8v6.5h-8zM7.5 9.2h8M11.5 6v6.5" fill="none" stroke="{c}" stroke-width="1"/>',
    "file": '<path d="M8 7h7M8 9.5h7M8 12h5" stroke="{c}" stroke-width="1.2"/>',
}


@lru_cache(maxsize=256)
def file_icon(kind: str, ext: str = "", size: int = 16) -> QIcon:
    """A page in the type's colour with a type glyph and the extension (PDF, MP4, ZIP ...)."""
    col = FILE_COLORS.get(kind, FILE_COLORS["file"])
    text = ext.upper()[:4]
    fs = 5.2 if len(text) <= 3 else 4.4
    body = (f'<path d="M6 1.5h8.5L19 6v15.5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-19a1 1 0 0 1 1-1z" fill="#1a1d24" '
            f'stroke="{col}" stroke-width="1.2"/><path d="M14.5 1.5V6H19" fill="none" stroke="{col}" stroke-width="1.2"/>'
            + GLYPH.get(kind, GLYPH["file"]).replace("{c}", col))
    if text:
        body += (f'<rect x="3" y="14.5" width="18" height="7" rx="1.2" fill="{col}"/>'
                 f'<text x="12" y="19.9" font-family="Segoe UI, Arial, sans-serif" font-size="{fs}" font-weight="700" '
                 f'text-anchor="middle" fill="#1e1e1e">{text}</text>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'
    ic = QIcon()
    for dpr in (1.0, 1.5, 2.0):
        ic.addPixmap(_render(svg, size, dpr))
    return ic


def icon_for_name(name: str, is_dir: bool, size: int = 16) -> QIcon:
    """Folder icon for folders; otherwise an icon for the file type (PDF, video, image, ...)."""
    if is_dir:
        return folder_icon(size=size)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return file_icon(EXT_KIND.get(ext, "file"), ext, size)


def thumb_icon(data: bytes | None, name: str = "", is_dir: bool = False, size: int = 40) -> QIcon:
    """The thumbnail when there is one (image/video preview or one the sender picked), else the type icon."""
    if data:
        px = QPixmap()
        if px.loadFromData(bytes(data)):
            canvas = QPixmap(size * 2, size * 2)
            canvas.fill(Qt.transparent)
            scaled = px.scaled(size * 2, size * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p = QPainter(canvas)
            p.drawPixmap((canvas.width() - scaled.width()) // 2, (canvas.height() - scaled.height()) // 2, scaled)
            p.end()
            canvas.setDevicePixelRatio(2.0)
            return QIcon(canvas)
    return icon_for_name(name, is_dir, size)


def kind_label(name: str, is_dir: bool) -> str:
    if is_dir:
        return "Folder"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return f"{ext.upper()} file" if ext else "File"


@lru_cache(maxsize=4)
def brand_mark(size: int = 30) -> QIcon:
    """App logo: two arrows (send / receive) in a rounded accent tile."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
           f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6f9bff"/>'
           f'<stop offset="1" stop-color="#7c5cff"/></linearGradient></defs>'
           f'<rect x="1" y="1" width="30" height="30" rx="9" fill="url(#g)"/>'
           f'<path d="M11 22V10M7.5 13.5L11 10l3.5 3.5M21 10v12M17.5 18.5L21 22l3.5-3.5" fill="none" stroke="#fff" '
           f'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    ic = QIcon()
    for dpr in (1.0, 1.5, 2.0):
        ic.addPixmap(_render(svg, size, dpr))
    return ic


AVATAR_COLORS = ["#5b8cff", "#7c5cff", "#e5669b", "#f08a4b", "#e5b454", "#4cc38a", "#36b5c9", "#9a7bff"]


@lru_cache(maxsize=512)
def avatar(name: str, online: bool | None = None, size: int = 28) -> QIcon:
    """Round avatar with initials (colour from the name) and an optional online/offline badge."""
    import html as _html
    parts = [p for p in (name or "?").replace("@", " ").split() if p]
    initials = _html.escape(("".join(p[0] for p in parts[:2]) or "?").upper())
    col = AVATAR_COLORS[sum(map(ord, name or "?")) % len(AVATAR_COLORS)]
    badge = ""
    if online is not None:
        badge = (f'<circle cx="25" cy="25" r="5.5" fill="#0f1115"/>'
                 f'<circle cx="25" cy="25" r="4" fill="{"#4cc38a" if online else "#5a6170"}"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30">'
           f'<circle cx="14" cy="14" r="13" fill="{col}"/>'
           f'<text x="14" y="18.6" font-family="Segoe UI, Arial, sans-serif" font-size="11.5" font-weight="700" '
           f'text-anchor="middle" fill="#ffffff">{initials}</text>{badge}</svg>')
    ic = QIcon()
    for dpr in (1.0, 1.5, 2.0):
        ic.addPixmap(_render(svg, size, dpr))
    return ic


@lru_cache(maxsize=16)
def dot(color: str, size: int = 10) -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="{color}"/></svg>'
    ic = QIcon()
    for dpr in (1.0, 2.0):
        ic.addPixmap(_render(svg, size, dpr))
    return ic
