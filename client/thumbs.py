"""Small preview images (thumbnails) for files being sent.

* Images: made from the file itself.
* Videos: a frame grabbed with ffmpeg when it is installed (optional).
* Anything else, or when the sender picks one: an image chosen by the sender.

Thumbnails travel end-to-end encrypted with the upload request directly to the folder owner and are kept
only on the two computers - never in the cloud. Without a thumbnail the apps show a folder icon or an
icon for the file type (PDF, video, image, ...).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("p2p.thumbs")

MAX_BYTES = 32 * 1024          # fits into one encrypted control message
SIZE = 160                     # longest side in pixels
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "ico"}
VIDEO_EXT = {"mp4", "mkv", "mov", "avi", "webm", "wmv", "m4v", "flv", "mpg", "mpeg", "mxf", "ts"}
MAX_SOURCE = 200 * 1024 * 1024  # do not decode giant images just for a preview


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_video(name: str) -> bool:
    return ext_of(name) in VIDEO_EXT


def valid(data) -> bool:
    return (isinstance(data, (bytes, bytearray)) and 0 < len(data) <= MAX_BYTES
            and (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"))


def _encode(img) -> bytes | None:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    if img is None or img.isNull():
        return None
    img = img.scaled(SIZE, SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    for quality in (80, 65, 50, 35):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "JPG", quality)
        buf.close()
        data = bytes(ba.data())
        if valid(data):
            return data
    return None


def from_image(path) -> bytes | None:
    """Thumbnail from an image file (e.g. one the sender picked)."""
    try:
        from PySide6.QtGui import QImageReader
        p = Path(path)
        if not p.is_file() or p.stat().st_size > MAX_SOURCE:
            return None
        reader = QImageReader(str(p))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > SIZE * 4:
            size.scale(SIZE * 2, SIZE * 2, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.KeepAspectRatio)
            reader.setScaledSize(size)          # decode small: fast even for huge photos
        return _encode(reader.read())
    except Exception as exc:                    # noqa: BLE001 - a preview must never break a transfer
        log.debug("no image thumbnail for %s: %s", path, exc)
        return None


def from_video(path) -> bytes | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    for seek in ("3", "0"):
        try:
            out = subprocess.run([ffmpeg, "-v", "error", "-ss", seek, "-i", str(path), "-frames:v", "1",
                                  "-vf", f"scale={SIZE * 2}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                                 capture_output=True, timeout=20, creationflags=flags).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("ffmpeg failed for %s: %s", path, exc)
            return None
        if out:
            from PySide6.QtGui import QImage
            img = QImage()
            if img.loadFromData(out):
                return _encode(img)
    return None


def make_thumb(path) -> bytes | None:
    """Automatic thumbnail for images and videos; None for other files."""
    ext = ext_of(Path(path).name)
    if ext in IMAGE_EXT:
        return from_image(path)
    if ext in VIDEO_EXT:
        return from_video(path)
    return None
