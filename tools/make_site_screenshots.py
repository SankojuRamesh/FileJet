"""Make the website screenshots from the real FileJet app (demo data, private local servers).

    python tools/make_site_screenshots.py            # writes cloud/static/site/img/{folders,share,shared,users}[@2x].webp

Starts a throw-away cloud + signaling server, signs in a demo studio and its team, builds a workspace with a folder
tree, shares the workspace and one folder of it, sends real files - then captures the app windows at 2x and saves
1x copies. Nothing touches your real account or data.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cloud" / "static" / "site" / "img"
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "2"                       # capture at 2x, 1x is scaled down
if Path("C:/Windows/Fonts").is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PIL import Image                                       # noqa: E402
from PySide6.QtCore import QRect, Qt                        # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap         # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox   # noqa: E402

QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
app = QApplication([])
from client.core import AppCore                             # noqa: E402
from client.gui import admin_view as av, app as gapp, common    # noqa: E402
from PySide6.QtGui import QFont                             # noqa: E402

app.setStyle("Fusion")                                      # the same look as the real app (gapp.run)
app.setStyleSheet(gapp.stylesheet())
app.setFont(QFont("Segoe UI", 9))
from tests.conftest import free_port, make_cfg              # noqa: E402

SECRET = "screenshot-secret-0123456789abcdef0123"
PASSWORD = "Str0ng-pass-123"
W, H = 1240, 700
QMessageBox.warning = staticmethod(lambda *a, **k: print("  (warning)", a[1:3]))
QMessageBox.information = staticmethod(lambda *a, **k: None)
av.confirm = lambda *a, **k: True


def pump(pred=lambda: False, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(0.03)
    return False


def up(port):
    import socket
    try:
        socket.create_connection(("127.0.0.1", port), 0.3).close()
        return True
    except OSError:
        return False


def save(pix: QPixmap, name: str):
    tmp = Path(tempfile.mkdtemp()) / f"{name}.png"
    pix.save(str(tmp), "PNG")
    img = Image.open(tmp).convert("RGB")
    img.save(OUT / f"{name}@2x.webp", "WEBP", quality=88, method=6)
    img.resize((img.width // 2, img.height // 2), Image.LANCZOS).save(OUT / f"{name}.webp", "WEBP", quality=90,
                                                                      method=6)
    print(f"saved {name}.webp ({img.width // 2}x{img.height // 2}) and {name}@2x.webp")


def sized(path: Path, size: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(os.urandom(min(size, 4096)))
    if os.name == "nt" and size > 4096:                     # sparse: shows the size, uses no disk space
        subprocess.run(["fsutil", "sparse", "setflag", str(path)], capture_output=True)
    with open(path, "r+b") as f:
        f.truncate(size)


def main():
    import uvicorn
    from server.config import Settings
    from server.main import create_app

    tmp = Path(tempfile.mkdtemp(prefix="fj_shots_"))
    sig_port, cloud_port = free_port(), free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(Settings(host="127.0.0.1", port=sig_port, reflector_port=0,
                         stun_servers=[], cloud_jwt_secret=SECRET, signal_rate_per_min=10000)),
                         host="127.0.0.1", port=sig_port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    env = dict(os.environ, DJANGO_SQLITE_PATH=str(tmp / "cloud.sqlite3"), P2P_CLOUD_JWT_SECRET=SECRET,
               P2P_SIGNALING_URL=f"ws://127.0.0.1:{sig_port}/ws", THROTTLE_REGISTER="1000/min",
               THROTTLE_LOGIN="1000/min", DJANGO_DEBUG="1")
    env.pop("QT_SCALE_FACTOR", None)
    subprocess.run([sys.executable, "manage.py", "migrate", "-v", "0"], cwd=ROOT / "cloud", env=env, check=True)
    cloud = subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{cloud_port}", "--noreload"],
                             cwd=ROOT / "cloud", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cores = []
    try:
        assert pump(lambda: up(cloud_port) and up(sig_port), 40), "servers did not start"
        url = f"http://127.0.0.1:{cloud_port}/"
        bridge = common.init_bridge()
        bridge.invoke.connect(lambda fn: fn())

        def person(user, name):
            c = AppCore(make_cfg("ws://unused/ws", tmp / user), url, app="app",
                        on_event=lambda kind, data=None: bridge.core_event.emit(kind, data))
            c.register(user, f"{user}@example.com", PASSWORD, name)
            cores.append(c)
            return c

        owner = person("northlight", "Northlight Studio")
        owner.cloud.request("POST", "api/billing/subscription/", {"plan": "business"})
        owner.me = owner.cloud.me()
        maya, leo, ava, sam = (person("maya", "Maya Chen"), person("leo", "Leo Park"), person("ava", "Ava Ruiz"),
                               person("sam", "Sam Lee"))
        for c in (maya, leo, ava, sam):
            owner.add_user(c.me["public_id"])
        g = owner.create_group("Editors")
        owner.update_group(g["id"], add=[leo.me["public_id"], ava.me["public_id"]])

        # ---- workspaces with a folder tree and files
        studio = tmp / "Studio"
        ws = studio / "Feature Film - Reel 2"
        for rel, size in [("Dailies/Day 01/A001_C003.mov", 1_830_000_000), ("Dailies/Day 01/A001_C007.mov", 1_220_000_000),
                          ("Dailies/Day 02/B002_C001.mov", 2_410_000_000), ("Editorial/Reel2_v7.edl", 14_200),
                          ("Editorial/Reel2_v7.aaf", 3_900_000), ("Graphics/Poster_Key_Art.psd", 412_000_000),
                          ("Graphics/Title_Card.png", 8_400_000), ("Graphics/Logos/Northlight_Logo.svg", 48_000),
                          ("Sound/Final_Mix_v3.wav", 980_000_000), ("Sound/Stems/Dialog.wav", 310_000_000),
                          ("Notes_Director.pdf", 420_000), ("Schedule.xlsx", 96_000)]:
            sized(ws / rel, size)
        (ws / "Reel2_Conform").mkdir()
        sized(studio / "Client Deliveries" / "Trailer_Final.mp4", 640_000_000)
        sized(studio / "Studio Assets" / "LUTs" / "Show_LUT_v2.cube", 1_100_000)
        fid = owner.create_folder("Feature Film - Reel 2", ws)
        owner.create_folder("Client Deliveries", studio / "Client Deliveries", settings={"kind": "submit"})
        owner.create_folder("Studio Assets", studio / "Studio Assets")

        win = gapp.MainWindow(owner, "app", dict(common.load_settings(tmp / "northlight"), cloud_url=url),
                              tmp / "northlight", bridge)
        win.resize(W, H)
        win.show()
        F = win.pages["folders"]
        win.go("folders")

        def share(dialog_target_keys, role):
            def fake_exec(d):
                for k in dialog_target_keys:
                    d._picked(d.query.findData(k))
                d.role.setCurrentIndex(d.role.findData(role))
                return QDialog.Accepted
            return mock.patch.object(av.ShareDialog, "exec", fake_exec)

        F.refresh_folders(fid, "")
        with share([f"user:{maya.me['public_id']}"], "contributor"):
            F.give_access()
        with share([f"group:{g['id']}"], "editor"):
            F.give_access()
        pump(lambda: len(owner.store.members(fid)) == 3, 15)
        F.refresh_folders(fid, "Graphics")
        with share([f"user:{sam.me['public_id']}"], "viewer"):
            F.share_clicked()                             # only the Graphics folder
            pump(lambda: F.subshare_at(fid, "Graphics") is not None, 15)
        sub = F.subshare_at(fid, "Graphics")
        for c in (maya, leo, ava):
            c.join_folder(fid)
        sam.join_folder(sub["folder_id"])

        # ---- Maya sends the conform files (arrive on the owner's PC)
        pump(lambda: maya.is_online(owner.me["public_id"]), 20)
        src = tmp / "maya_files" / "Reel2_Conform"
        for name, size in [("Reel2_Conform_v7.mov", 52_000_000), ("EDL_Reel2_v7.edl", 14_000),
                           ("Poster_Key_Art.png", 281_900)]:
            sized(src / name, size)
        rules = next(s for s in maya.remote_shares(owner.me["public_id"]) if s["folder_id"] == fid)["rules"]
        job = maya.upload(owner.me["public_id"], fid, "Feature Film - Reel 2", "", [src], rules=rules)
        job.done.wait(90)
        print("upload:", job.state)

        # ---- 1. folders: workspace with its tree, all files, people with access
        for k in ("users", "folders"):
            win.go(k)
        F.refresh_folders(fid, "")
        for rel in ("Dailies", "Graphics"):
            it = F._find_item(fid, rel)
            if it is not None:
                it.setExpanded(True)
        F.tabs.setCurrentIndex(0)
        pump(lambda: F.files.rowCount() >= 12, 15)
        pump(timeout=3)                                   # sizes counted, presence settled
        F._update_sizes()
        pump(timeout=0.5)
        save(win.grab(), "folders")

        # ---- 2. share: only the Graphics folder, picking people and groups
        F.refresh_folders(fid, "Sound")
        pump(timeout=0.5)
        base = win.grab()
        d = av.ShareDialog(win, owner, {"name": "Sound", "folder_id": ""})
        d.query._picked = None
        d._picked(d.query.findData(f"group:{g['id']}"))
        d._picked(d.query.findData(f"user:{maya.me['public_id']}"))
        d.role.setCurrentIndex(d.role.findData("contributor"))
        d.resize(640, 690)
        d.show()
        pump(timeout=0.8)
        dlg = d.grab()
        comp = QPixmap(base.size())
        comp.setDevicePixelRatio(base.devicePixelRatio())
        comp.fill(Qt.transparent)
        p = QPainter(comp)
        p.drawPixmap(0, 0, base)
        p.fillRect(QRect(0, 0, W, H), QColor(0, 0, 0, 120))
        dw, dh = dlg.width() / dlg.devicePixelRatio(), dlg.height() / dlg.devicePixelRatio()
        x, y = int((W - dw) / 2 + 150), max(4, int((H - dh) / 2))
        p.fillRect(QRect(x - 1, y - 1, int(dw) + 2, int(dh) + 2), QColor("#323845"))
        p.drawPixmap(x, y, dlg)
        p.end()
        d.reject()
        save(comp, "share")

        # ---- 3. shared: Maya's view of the workspace
        win_m = gapp.MainWindow(maya, "app", dict(common.load_settings(tmp / "maya"), cloud_url=url), tmp / "maya",
                                bridge)
        win_m.resize(W, H)
        win_m.show()
        win_m.go("myfolders")
        C = win_m.pages["myfolders"]
        maya.refresh_contacts()
        C.refresh_tree()
        maya.cfg.dest_dir = Path("D:/Media/Downloads")          # a tidy path in the picture
        C._update_dest()
        C._select_folder(fid)
        pump(lambda: C.files.rowCount() >= 5, 20)
        pump(timeout=2)
        save(win_m.grab(), "shared")

        # ---- 4. users page
        win.go("users")
        win.pages["users"].reload()
        pump(timeout=1.5)
        save(win.grab(), "users")
    finally:
        for c in cores:
            try:
                c.shutdown()
            except Exception:
                pass
        cloud.terminate()
        srv.should_exit = True
        time.sleep(1)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)            # the demo data is not kept


if __name__ == "__main__":
    main()
    os._exit(0)
