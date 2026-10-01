"""Unit tests: share path confinement, permission checks, E2E crypto, presence authentication."""
from __future__ import annotations

import base64
import os
import sys
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from client.database import TransferDB
from client.e2e import E2E, E2EError
from client.identity import load_or_create
from client.shares import Perms, ShareService, ShareStore, clean_rel, list_dir, resolve_in_share, safe_child
from server.config import Settings
from server.main import create_app

SECRET = "unit-test-secret-0123456789abcdef0123456789"


# ---------------------------------------------------------------- paths
def test_clean_rel_rejects_traversal():
    assert clean_rel("a/./b//c/") == "a/b/c"
    assert clean_rel("a\\b") == "a/b"
    for bad in ("..", "a/../../b", "C:/Windows", "x:y", "a/\x00b"):
        with pytest.raises(PermissionError):
            clean_rel(bad)


def test_resolve_in_share_confined(tmp_path):
    root = tmp_path / "share"
    (root / "sub").mkdir(parents=True)
    assert resolve_in_share(root, "sub") == (root / "sub").resolve()
    assert resolve_in_share(root, "") == root.resolve()
    with pytest.raises(PermissionError):
        resolve_in_share(root, "../")


@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI_SYMLINKS"),
                    reason="creating symlinks needs developer mode on Windows")
def test_symlink_escape_blocked(tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "keys.txt").write_text("x")
    os.symlink(outside, root / "link", target_is_directory=True)
    with pytest.raises(PermissionError):
        resolve_in_share(root, "link/keys.txt")
    assert [e["name"] for e in list_dir(root, root)["entries"]] == []      # hidden from listings


def test_safe_child(tmp_path):
    assert safe_child(tmp_path, "Album/sub") == (tmp_path / "Album" / "sub").resolve()
    assert safe_child(tmp_path, "CON/x").name == "x"                       # device names neutralised
    with pytest.raises(PermissionError):
        safe_child(tmp_path, "../evil")


# ---------------------------------------------------------- permissions
class FakeCore:
    def __init__(self):
        self.calls = []

    def serve_download(self, *a):
        self.calls.append(("download", a))
        return {"ok": True}

    def serve_upload(self, *a):
        self.calls.append(("upload", a))
        return {"ok": True}

    def log_activity(self, *a):
        self.calls.append(("activity", a))


FID = "FD-TEST-2345"
BOB = "111222333"


def grant(store, uid, read=False, upload=False, edit=False, delete=False, status="active", expires=None):
    store.upsert_member(FID, {"id": 1, "user": {"public_id": uid, "username": "bob", "display_name": "Bob",
                                                "email": "b@example.com"},
                              "perms": {"read": read, "upload": upload, "edit": edit, "delete": delete},
                              "role": "custom", "status": status, "expires_at": expires})


@pytest.fixture
def service(tmp_path):
    db = TransferDB(tmp_path / "t.db")
    store = ShareStore(db)
    root = tmp_path / "share"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_text("hello")
    store.create(FID, "Docs", root)
    return store, ShareService(store, FakeCore()), FID, root


def test_permissions_enforced_per_operation(service):
    store, svc, share, root = service
    with pytest.raises(PermissionError):
        svc.handle(BOB, "fs.list", {"share": share, "path": ""})
    assert svc.handle(BOB, "shares.list", {}) == []

    grant(store, BOB, read=True, status="invited")              # not opened with the folder ID yet
    assert svc.handle(BOB, "shares.list", {}) == []
    with pytest.raises(PermissionError, match="open the folder"):
        svc.handle(BOB, "fs.list", {"share": share, "path": ""})

    grant(store, BOB, read=True)
    assert svc.handle(BOB, "shares.list", {})[0]["perms"] == {"read": True, "upload": False, "edit": False,
                                                              "delete": False}
    assert [e["name"] for e in svc.handle(BOB, "fs.list", {"share": share, "path": ""})["entries"]] == ["docs"]
    svc.handle(BOB, "fs.download", {"share": share, "path": "docs/a.txt"})
    for method, params in (("fs.mkdir", {"name": "x"}), ("fs.upload", {"dir": "", "name": "n", "size": 1}),
                           ("fs.rename", {"path": "docs", "new_name": "d2"}), ("fs.delete", {"path": "docs/a.txt"})):
        with pytest.raises(PermissionError):
            svc.handle(BOB, method, {"share": share, **params})

    grant(store, BOB, upload=True)                               # upload only = drop box
    listing = svc.handle(BOB, "fs.list", {"share": share, "path": ""})
    assert listing["dropbox"] and listing["entries"] == []
    with pytest.raises(PermissionError):                         # upload does not imply download
        svc.handle(BOB, "fs.download", {"share": share, "path": "docs/a.txt"})
    svc.handle(BOB, "fs.mkdir", {"share": share, "path": "", "name": "new/deeper"})   # folder uploads
    assert (root / "new" / "deeper").is_dir()
    with pytest.raises(PermissionError):
        svc.handle(BOB, "fs.rename", {"share": share, "path": "docs", "new_name": "d2"})

    grant(store, BOB, edit=True)
    svc.handle(BOB, "fs.rename", {"share": share, "path": "docs", "new_name": "d2"})
    assert (root / "d2" / "a.txt").exists()

    grant(store, BOB, delete=True)
    svc.handle(BOB, "fs.delete", {"share": share, "path": "d2/a.txt"})
    assert not (root / "d2" / "a.txt").exists()
    with pytest.raises(PermissionError):
        svc.handle(BOB, "fs.delete", {"share": share, "path": ""})          # never the root

    grant(store, BOB, read=True, expires=time.time() - 60)       # expired access
    with pytest.raises(PermissionError, match="expired"):
        svc.handle(BOB, "fs.list", {"share": share, "path": ""})
    with pytest.raises(PermissionError):                         # someone else has nothing
        svc.handle("999888777", "fs.list", {"share": share, "path": ""})
    store.remove_member(share, BOB)
    assert store.perms(share, BOB) == Perms()


def test_upload_rules(service):
    from client.shares import check_upload_rules
    store, svc, share, root = service
    store.update_settings(share, {"name": "Docs", "kind": "submit", "allowed_extensions": "mp4,pdf",
                                  "max_file_size": 1000, "form_fields": [{"name": "Project", "required": True}]})
    grant(store, BOB, upload=True)
    folder = store.get(share)
    check_upload_rules(folder, "a.MP4", 10, {"Project": "x"})
    for name, size, fields, msg in (("a.exe", 10, {"Project": "x"}, "file types"),
                                    ("a.pdf", 5000, {"Project": "x"}, "maximum"),
                                    ("a.pdf", 10, {"Project": "  "}, "Project")):
        with pytest.raises(PermissionError, match=msg):
            check_upload_rules(folder, name, size, fields)
        with pytest.raises(PermissionError, match=msg):          # the admin's app enforces the same
            svc.handle(BOB, "fs.upload", {"share": share, "dir": "", "name": name, "size": size, "fields": fields})
    svc.handle(BOB, "fs.upload", {"share": share, "dir": "", "name": "ok.pdf", "size": 10,
                                  "fields": {"Project": "Launch"}})
    assert svc.core.calls[-1][0] == "upload"


# ------------------------------------------------------------------ E2E
def test_e2e_seal_open_tamper_replay(tmp_path):
    a = E2E(load_or_create(tmp_path / "a"), "100000001")
    b = E2E(load_or_create(tmp_path / "b"), "200000002")
    m = load_or_create(tmp_path / "m")
    msg = a.seal("200000002", b.identity.cert_pem, {"hello": "world"})
    assert b.open("100000001", a.identity.cert_pem, msg) == {"hello": "world"}
    with pytest.raises(E2EError):                                # replay
        b.open("100000001", a.identity.cert_pem, msg)
    raw = bytearray(base64.b64decode(a.seal("200000002", b.identity.cert_pem, {"x": 1})))
    raw[-1] ^= 1
    with pytest.raises(E2EError):                                # tampered
        b.open("100000001", a.identity.cert_pem, base64.b64encode(bytes(raw)).decode())
    with pytest.raises(E2EError):                                # wrong sender key (impostor)
        b.open("100000001", m.cert_pem, a.seal("200000002", b.identity.cert_pem, {"x": 2}))
    with pytest.raises(E2EError):                                # addressed to someone else
        b.open("300000003", a.identity.cert_pem, a.seal("200000002", b.identity.cert_pem, {"x": 3}))
    assert a.rendezvous("200000002", b.identity.cert_pem, "n1") == b.rendezvous("100000001", a.identity.cert_pem, "n1")
    assert a.rendezvous("200000002", b.identity.cert_pem, "n1") != a.rendezvous("200000002", b.identity.cert_pem, "n2")


# ------------------------------------------------------------- presence
def _token(uid, fp, exp=3600):
    now = int(time.time())
    return jwt.encode({"typ": "signal", "uid": uid, "fp": fp, "name": uid, "iat": now, "exp": now + exp},
                      SECRET, algorithm="HS256")


def _auth(ws, ident, token, contacts):
    ch = ws.receive_json()
    sig = base64.b64encode(ident.sign(b"p2pft-presence|" + base64.b64decode(ch["nonce"]))).decode()
    ws.send_json({"type": "auth", "token": token, "cert": ident.cert_pem, "sig": sig, "contacts": contacts})
    return ws.receive_json()


def test_presence_auth_and_mutual_visibility(tmp_path):
    a, b, c = (load_or_create(tmp_path / n) for n in "abc")
    app = create_app(Settings(reflector_port=0, stun_servers=[], cloud_jwt_secret=SECRET))
    with TestClient(app) as client:
        # wrong device key for the token's fingerprint -> rejected
        with client.websocket_connect("/ws/presence") as ws:
            assert _auth(ws, b, _token("100000001", a.fingerprint), [])["type"] == "error"
        # expired / forged tokens -> rejected
        with client.websocket_connect("/ws/presence") as ws:
            assert _auth(ws, a, _token("100000001", a.fingerprint, exp=-10), [])["type"] == "error"
        with client.websocket_connect("/ws/presence") as ws:
            forged = jwt.encode({"typ": "signal", "uid": "100000001", "fp": a.fingerprint, "exp": 2 ** 40},
                                "wrong-secret-0123456789abcdef0123", algorithm="HS256")
            assert _auth(ws, a, forged, [])["type"] == "error"

        with client.websocket_connect("/ws/presence") as wa, client.websocket_connect("/ws/presence") as wb, \
                client.websocket_connect("/ws/presence") as wc:
            assert _auth(wa, a, _token("100000001", a.fingerprint), ["200000002", "300000003"])["type"] == "welcome"
            assert wa.receive_json() == {"type": "presence", "uid": "200000002", "online": False}
            assert wa.receive_json() == {"type": "presence", "uid": "300000003", "online": False}
            assert _auth(wb, b, _token("200000002", b.fingerprint), ["100000001"])["type"] == "welcome"
            snap = wb.receive_json()
            assert snap["uid"] == "100000001" and snap["online"] and snap["fp"] == a.fingerprint
            seen = wa.receive_json()
            assert seen["uid"] == "200000002" and seen["online"]
            # carol is listed by alice but does not list alice -> neither sees the other
            assert _auth(wc, c, _token("300000003", c.fingerprint), [])["type"] == "welcome"
            wc.send_json({"type": "msg", "to": "100000001", "data": "x", "ref": 1})
            assert wc.receive_json() == {"type": "undeliverable", "to": "100000001", "ref": 1}
            # mutual contacts can message each other
            wa.send_json({"type": "msg", "to": "200000002", "data": "hello", "ref": 2})
            got = wb.receive_json()
            assert (got["type"], got["from"], got["data"], got["fp"]) == ("msg", "100000001", "hello", a.fingerprint)


def test_signaling_requires_cloud_token_when_enabled(tmp_path):
    a = load_or_create(tmp_path / "a")
    app = create_app(Settings(reflector_port=0, stun_servers=[], cloud_jwt_secret=SECRET))
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "ping"})
                ws.receive_json()
        tok = _token("100000001", a.fingerprint)
        with client.websocket_connect("/ws", headers={"Authorization": f"Bearer {tok}"}) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
