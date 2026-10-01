#!/usr/bin/env bash
# Build the MediaRush desktop app for Linux (Ubuntu/Debian, x86_64) - run ON an Ubuntu machine.
#
#   bash packaging/linux/build_linux.sh
#   bash packaging/linux/build_linux.sh --cloud-url http://iotgateway.live/ --signal-url ws://13.204.80.52:8765/ws
#
# Produces in dist/:
#   MediaRush                          single-file program  (run: ./MediaRush)
#   mediarush_<version>_amd64.deb      installer            (sudo apt install ./mediarush_<version>_amd64.deb)
#   MediaRush-linux-x86_64.tar.gz      program + icon + .desktop file (no installer)
#
# A Linux build runs on the SAME OR NEWER Ubuntu than the one it was built on - build on the oldest
# version you want to support (e.g. Ubuntu 22.04). Works on a desktop or a headless server (no screen needed).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
VERSION="$(grep -m1 '^version' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/')"
VERSION="${VERSION:-2.0.0}"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"

step() { printf '\n==> %s\n' "$1"; }

step "Build tools and Qt runtime libraries"
if command -v apt-get >/dev/null; then
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq python3 python3-venv python3-dev binutils dpkg-dev \
      libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 \
      libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
      libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 >/dev/null
fi
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'

step "Python build environment (.venv-build)"
[ -x .venv-build/bin/python ] || python3 -m venv .venv-build
.venv-build/bin/pip install -q --upgrade pip
.venv-build/bin/pip install -q "PySide6>=6.6" "cryptography>=42" "websockets>=13" "PyJWT>=2.8" "psutil>=5.9" "pyinstaller>=6"

step "Building MediaRush"
rm -rf dist build
QT_QPA_PLATFORM=offscreen .venv-build/bin/python build_desktop.py "$@"
test -x dist/MediaRush || { echo "build failed: dist/MediaRush missing"; exit 1; }

step "Smoke test (starts the program without a screen)"
if QT_QPA_PLATFORM=offscreen timeout 8 dist/MediaRush --help >/dev/null 2>&1; then echo "starts OK"; else echo "(--help returned non-zero - check on a desktop)"; fi

DESKTOP_FILE='[Desktop Entry]
Type=Application
Name=MediaRush
GenericName=File transfer
Comment=Fast direct file and folder transfer between computers
Exec=/opt/mediarush/MediaRush %U
Icon=mediarush
Terminal=false
Categories=Network;FileTransfer;
StartupWMClass=MediaRush'

step "Packaging .deb"
PKG="build/deb/mediarush_${VERSION}_${ARCH}"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/mediarush" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/256x256/apps"
install -m 755 dist/MediaRush "$PKG/opt/mediarush/MediaRush"
ln -s /opt/mediarush/MediaRush "$PKG/usr/bin/mediarush"
printf '%s\n' "$DESKTOP_FILE" > "$PKG/usr/share/applications/mediarush.desktop"
[ -f build/mediarush.png ] && install -m 644 build/mediarush.png "$PKG/usr/share/icons/hicolor/256x256/apps/mediarush.png"
SIZE_KB="$(du -sk "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<EOF
Package: mediarush
Version: $VERSION
Section: net
Priority: optional
Architecture: $ARCH
Installed-Size: $SIZE_KB
Maintainer: MediaRush <no-reply@iotgateway.live>
Depends: libgl1, libegl1, libxkbcommon0, libxkbcommon-x11-0, libfontconfig1, libdbus-1-3, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libxcb-xkb1
Description: MediaRush - fast direct file and folder transfer
 Share folders with users, send and receive files and folders of any size
 directly between computers (P2P, encrypted), chat with your users.
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst"
dpkg-deb --build --root-owner-group "$PKG" "dist/mediarush_${VERSION}_${ARCH}.deb" >/dev/null

step "Packaging .tar.gz"
TAR="build/MediaRush-linux"
rm -rf "$TAR"; mkdir -p "$TAR"
cp dist/MediaRush "$TAR/"
[ -f build/mediarush.png ] && cp build/mediarush.png "$TAR/"
printf '%s\n' "$DESKTOP_FILE" | sed 's|/opt/mediarush/MediaRush|MediaRush|' > "$TAR/mediarush.desktop"
tar -czf "dist/MediaRush-linux-x86_64.tar.gz" -C build MediaRush-linux

step "Done"
ls -lh dist/
echo
echo "Install on Ubuntu:   sudo apt install ./dist/mediarush_${VERSION}_${ARCH}.deb     (then: MediaRush in the app menu)"
echo "Or run directly:     ./dist/MediaRush"
