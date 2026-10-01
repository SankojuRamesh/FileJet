"""End-to-end: Django cloud + signaling/presence + Admin and Client app cores (no GUI).

Admin adds users by ID -> creates folders on their own computer -> gives each user a role /
permissions (optionally via a group, with expiry) -> the cloud e-mails the folder ID -> the client
opens the folder -> uploads / downloads / edits / deletes exactly as permitted. Submit folders
act as drop boxes with file-type, size and required-form-field rules. Uploads go through an outbox
on the user's computer: when the admin is offline they wait there (the cloud shows them as
'queued' - metadata only) and are delivered directly when the admin comes back online.
Files never touch the cloud or any server.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from client.cloud_api import CloudError
from client.core import AppCore
from client.presence import RpcError
from tests.conftest import free_port, make_cfg

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-cloud-signal-secret-0123456789abcdef"
MiB = 1 << 20


def wait_until(pred, timeout=20.0, step=0.1, msg="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    raise AssertionError(f"timed out waiting for {msg}")


def _up(port):
    try:
        socket.create_connection(("127.0.0.1", port), 0.3).close()
        return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    import uvicorn

    from server.config import Settings
    from server.main import create_app

    tmp = tmp_path_factory.mktemp("stack")
    sig_port, cloud_port = free_port(), free_port()
    settings = Settings(host="127.0.0.1", port=sig_port, reflector_port=0, stun_servers=[],
                        cloud_jwt_secret=SECRET, signal_rate_per_min=10000)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=sig_port,
                                           log_level="warning", ws_max_size=1 << 20))
    threading.Thread(target=server.run, daemon=True).start()
    db = tmp / "cloud.sqlite3"
    env = dict(os.environ, DJANGO_SQLITE_PATH=str(db), P2P_CLOUD_JWT_SECRET=SECRET,
               P2P_SIGNALING_URL=f"ws://127.0.0.1:{sig_port}/ws", THROTTLE_REGISTER="1000/min",
               THROTTLE_LOGIN="1000/min", DJANGO_DEBUG="1")
    subprocess.run([sys.executable, "manage.py", "migrate", "-v", "0"], cwd=ROOT / "cloud", env=env, check=True)
    cloud = subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{cloud_port}", "--noreload"],
                             cwd=ROOT / "cloud", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_until(lambda: _up(cloud_port) and _up(sig_port), 30, msg="servers")
    yield {"cloud": f"http://127.0.0.1:{cloud_port}/", "tmp": tmp, "db": db}
    cloud.terminate()
    server.should_exit = True


def emails_to(stack, address):
    con = sqlite3.connect(stack["db"])
    try:
        return [dict(zip(("subject", "body", "kind"), r)) for r in con.execute(
            'SELECT subject, body, kind FROM sharing_outboundemail WHERE "to"=? ORDER BY id', (address,))]
    finally:
        con.close()


PASSWORD = "Str0ng-pass-123"


def make_core(stack, name, app, login=False, **cfg):
    core = AppCore(make_cfg("ws://unused/ws", stack["tmp"] / name, **cfg), stack["cloud"], app=app)
    if login:
        core.login(name, PASSWORD)
    else:
        core.register(name, f"{name}@example.com", PASSWORD, name.title())
    return core


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def people(stack):
    admin = make_core(stack, "alice", "admin")
    admin.cloud.request("POST", "api/billing/subscription/", {"plan": "business"})   # Free allows only 2 folders
    admin.me = admin.cloud.me()
    bob = make_core(stack, "bob", "client")
    carol = make_core(stack, "carol", "client")
    mallory = make_core(stack, "mallory", "client")
    yield admin, bob, carol, mallory
    for c in (admin, bob, carol, mallory):
        c.shutdown()


def test_admin_adds_users_by_id_and_presence_follows(people):
    admin, bob, carol, mallory = people
    admin.add_user(bob.me["public_id"], note="Editor - video")          # by ID
    admin.add_user("carol@example.com")                                   # e-mail works too
    with pytest.raises(CloudError):
        admin.add_user("nobody-here")
    wait_until(lambda: admin.is_online(bob.me["public_id"]) and bob.is_online(admin.me["public_id"])
               and admin.is_online(carol.me["public_id"]), timeout=6, msg="presence of added users")
    assert not mallory.is_online(admin.me["public_id"])                  # not added -> sees nothing
    with pytest.raises(RpcError):
        mallory.call(admin.me["public_id"], "shares.list")


def test_submit_folder_invite_email_open_by_id_and_rules(stack, people, tmp_path):
    admin, bob, _carol, mallory = people
    a_uid, b_uid = admin.me["public_id"], bob.me["public_id"]
    inbox = tmp_path / "Deliveries"
    inbox.mkdir()
    fid = admin.create_folder("Deliveries", inbox, settings={
        "kind": "submit", "allowed_extensions": "mp4,pdf", "max_file_size": 20 * MiB,
        "form_fields": [{"name": "Project", "required": True}, {"name": "Notes"}]})
    assert fid.startswith("FD-")
    admin.set_member(fid, b_uid, role="uploader")
    mail = emails_to(stack, "bob@example.com")[-1]
    assert fid in mail["body"] and "Deliveries" in mail["subject"]
    # not opened yet: the admin's app refuses the invited-but-not-joined user
    assert bob.remote_shares(a_uid) == []
    with pytest.raises(CloudError):
        mallory.join_folder(fid)                                           # knows the ID, not invited
    bob.join_folder(fid)
    wait_until(lambda: admin.store.member(fid, b_uid)["status"] == "active", 5, msg="admin sees 'opened'")
    shares = bob.remote_shares(a_uid)
    assert [(s["folder_id"], s["kind"], s["perms"]) for s in shares] == \
        [(fid, "submit", {"read": False, "upload": True, "edit": False, "delete": False})]
    listing = bob.remote_list(a_uid, fid, "")
    assert listing["dropbox"] and listing["entries"] == []                # drop box: contents hidden
    rules = shares[0]["rules"]
    bad = tmp_path / "virus.exe"
    bad.write_bytes(b"x")
    with pytest.raises(PermissionError, match="file types"):
        bob.upload(a_uid, fid, "Deliveries", "", [bad], fields={"Project": "X"}, rules=rules)
    with pytest.raises(RpcError, match="file types"):                     # enforced by the admin too
        bob.call(a_uid, "fs.upload", {"share": fid, "dir": "", "name": "virus.exe", "size": 1,
                                      "transfer_id": "ab" * 8, "fields": {"Project": "X"}})
    video = tmp_path / "cut.mp4"
    video.write_bytes(os.urandom(3 * MiB))
    with pytest.raises(PermissionError, match="Project"):
        bob.upload(a_uid, fid, "Deliveries", "", [video], fields={}, rules=rules)
    job = bob.upload(a_uid, fid, "Deliveries", "", [video], fields={"Project": "Launch", "Notes": "v2"}, rules=rules)
    assert job.done.wait(60) and job.state == "completed", job.error
    assert sha(inbox / "cut.mp4") == sha(video)
    wait_until(lambda: (inbox / "cut.mp4.metadata.json").exists(), 5, msg="metadata sidecar")
    side = json.loads((inbox / "cut.mp4.metadata.json").read_text())
    assert side["fields"] == {"Project": "Launch", "Notes": "v2"} and side["uploaded_by"] == "bob"
    # completion e-mail to the admin (one per batch)
    admin.reporter.tick(force=True)
    bob.reporter.tick(force=True)
    wait_until(lambda: any(m["kind"] == "batch_done" for m in emails_to(stack, "alice@example.com")), 15,
               msg="upload notification")
    # the cloud has metadata only - never the form values
    rec = next(t for t in admin.cloud.history(page_size=50)["results"] if t["file_name"] == "cut.mp4")
    assert rec["folder_id"] == fid and "fields" not in rec and "Launch" not in json.dumps(rec)
    # the admin's own received-files log (local): who, size, form values, where it is
    got = admin.received_files(fid)
    assert [(r["name"], r["sender_uid"], r["size"], r["fields"]["Project"]) for r in got] == \
        [("cut.mp4", b_uid, 3 * MiB, "Launch")]
    assert Path(got[0]["local_path"]) == inbox / "cut.mp4"
    # the user's outbox shows it delivered
    assert [r["state"] for r in bob.outbox_items(fid)] == ["delivered"]


def test_share_folder_roles_edit_delete_expiry_and_revocation(people, tmp_path):
    admin, _bob, carol, _m = people
    a_uid, c_uid = admin.me["public_id"], carol.me["public_id"]
    root = tmp_path / "Projects"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_text("hello")
    (root / "big.bin").write_bytes(os.urandom(6 * MiB))
    fid = admin.create_folder("Projects", root)
    admin.set_member(fid, c_uid, role="editor")
    carol.join_folder(fid)
    wait_until(lambda: carol.remote_shares(a_uid), 5, msg="carol sees the folder")
    assert [e["name"] for e in carol.remote_list(a_uid, fid, "")["entries"]] == ["docs", "big.bin"]
    job = carol.download(a_uid, fid, "Projects", [("big.bin", False), ("docs", True)], tmp_path / "dl")
    assert job.done.wait(60) and job.state == "completed", job.error
    assert sha(tmp_path / "dl" / "big.bin") == sha(root / "big.bin")
    carol.remote_rename(a_uid, fid, "docs/a.txt", "b.txt")                # edit allowed
    assert (root / "docs" / "b.txt").exists()
    with pytest.raises(RpcError, match="delete"):
        carol.remote_delete(a_uid, fid, "docs/b.txt")                     # editor cannot delete
    admin.update_member(fid, c_uid, {"read": True, "upload": True, "edit": True, "delete": True})
    carol.remote_delete(a_uid, fid, "docs/b.txt")
    assert not (root / "docs" / "b.txt").exists()
    # expiry in the past -> no access at all
    past = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    admin.update_member(fid, c_uid, {"read": True}, expires_at=past)
    with pytest.raises(RpcError, match="expired"):
        carol.remote_list(a_uid, fid, "")
    # revoking during a running download stops it and leaves no final file
    admin.update_member(fid, c_uid, {"read": True}, expires_at="")
    (root / "slow.bin").write_bytes(os.urandom(24 * MiB))
    admin.cfg.rate_limit = 4 * MiB
    try:
        job = carol.download(a_uid, fid, "Projects", [("slow.bin", False)], tmp_path / "dl2")
        wait_until(lambda: job.items and job.items[0].engine is not None and job.items[0].engine.confirmed > MiB,
                   30, msg="download progress")
        admin.remove_member(fid, c_uid)
        assert job.done.wait(30) and job.state in ("failed", "cancelled")
        assert not (tmp_path / "dl2" / "slow.bin").exists()
    finally:
        admin.cfg.rate_limit = None
    wait_until(lambda: {"join", "download", "rename", "delete", "access"} <=
               {e["action"] for e in admin.cloud.activity(fid)}, 10, msg="activity log")


def test_groups_give_access_to_many_users(stack, people, tmp_path):
    admin, bob, carol, _m = people
    g = admin.create_group("Editors")
    admin.update_group(g["id"], add=[bob.me["public_id"], carol.me["public_id"]])
    fid = admin.create_folder("Team", tmp_path / "Team", create=True)
    assert admin.add_group_to_folder(fid, g["id"], "viewer") == 2
    assert {m["uid"] for m in admin.store.members(fid)} == {bob.me["public_id"], carol.me["public_id"]}
    assert any(fid in m["body"] for m in emails_to(stack, "carol@example.com"))
    bob.join_folder(fid)
    wait_until(lambda: bob.remote_shares(admin.me["public_id"]), 5, msg="bob sees the group folder")
    with pytest.raises(RpcError, match="upload"):                          # viewer: read only
        bob.call(admin.me["public_id"], "fs.upload", {"share": fid, "dir": "", "name": "x.bin", "size": 1,
                                                       "transfer_id": "cd" * 8})


def test_send_while_admin_offline_is_delivered_when_admin_comes_online(stack, people, tmp_path):
    """Movie studio: the admin shares a folder with an editor, then closes the app. The editor still sends a
    folder of files - they wait on the editor's computer (cloud: 'queued', metadata only) and arrive in the
    admin's folder when the admin opens the app again. Nothing is ever stored on a server."""
    _admin, bob, _c, _m = people
    dave = make_core(stack, "dave", "admin")
    b_uid, d_uid = bob.me["public_id"], dave.me["public_id"]
    movie = tmp_path / "Movie"
    movie.mkdir()
    fid = dave.create_folder("Movie", movie)
    dave.add_user(b_uid)
    dave.set_member(fid, b_uid, role="uploader")
    bob.join_folder(fid)
    wait_until(lambda: bob.is_online(d_uid) and bob.remote_shares(d_uid), 8, msg="bob sees dave's folder")
    rules = bob.remote_shares(d_uid)[0]["rules"]

    dave.shutdown()                                                       # the admin goes offline
    wait_until(lambda: not bob.is_online(d_uid), 10, msg="dave offline")

    src = tmp_path / "Reel1"
    (src / "audio").mkdir(parents=True)
    (src / "shot.mov").write_bytes(os.urandom(5 * MiB))
    (src / "audio" / "mix.wav").write_bytes(os.urandom(700_001))
    (src / "notes.txt").write_text("any file type works")
    job = bob.upload(d_uid, fid, "Movie", "", [src], rules=rules)
    time.sleep(2)
    assert job.state == "queued" and not job.done.is_set()
    assert {r["state"] for r in bob.outbox_items(fid)} == {"queued"}
    assert not any(movie.iterdir())                                       # nothing arrived, nothing stored anywhere
    # the cloud shows the admin what is waiting (metadata only)
    probe = AppCore(make_cfg("ws://unused/ws", tmp_path / "probe"), stack["cloud"], app="admin")
    probe.cloud.login("dave", PASSWORD)
    wait_until(lambda: probe.folder_files_cloud(fid)["stats"]["waiting"] == 3, 10, msg="cloud: 3 queued")
    names = {f["file_name"] for f in probe.folder_files_cloud(fid)["files"]}
    assert names == {"shot.mov", "mix.wav", "notes.txt"}

    dave2 = make_core(stack, "dave", "admin", login=True)                 # the admin opens the app again
    try:
        assert job.done.wait(90) and job.state == "completed", job.error
        assert sha(movie / "Reel1" / "shot.mov") == sha(src / "shot.mov")
        assert sha(movie / "Reel1" / "audio" / "mix.wav") == sha(src / "audio" / "mix.wav")
        assert (movie / "Reel1" / "notes.txt").read_text() == "any file type works"
        assert {r["state"] for r in bob.outbox_items(fid)} == {"delivered"}
        wait_until(lambda: len(dave2.received_files(fid)) == 3, 10, msg="received log")
        assert {r["sender_uid"] for r in dave2.received_files(fid)} == {b_uid}
        # the owner's Transfers page shows it as one incoming batch ("Receiving Reel1 from Bob")
        def incoming():
            return [j for j in dave2.jobs.values() if j.kind == "receive"]
        wait_until(lambda: incoming() and incoming()[0].state == "completed", 10, msg="incoming job completed")
        assert len(incoming()) == 1 and incoming()[0].title == "Reel1" and len(incoming()[0].items) == 3
        dave2.reporter.tick(force=True)
        bob.reporter.tick(force=True)
        wait_until(lambda: probe.folder_files_cloud(fid)["stats"]["files"] == 3, 15, msg="cloud: 3 delivered")
        st = probe.folder_files_cloud(fid)["stats"]
        assert (st["waiting"], st["senders"]) == (0, 1)
    finally:
        dave2.shutdown()


def _first_active_bytes(core, kind, timeout=60):
    """Bytes already present when a (resumed) transfer becomes active again."""
    end = time.time() + timeout
    while time.time() < end:
        for j in core.jobs.values():
            for it in j.items:
                if j.kind == kind and it.engine is not None:
                    snap = it.engine.snapshot()
                    if snap["state"] == "active":
                        return snap["transferred"]
        time.sleep(0.02)
    raise AssertionError("transfer did not become active: " + repr([(j.kind, j.state, [(i.state, i.engine and i.engine.snapshot()["state"]) for i in j.items]) for j in core.jobs.values()]))


def test_resume_after_power_or_network_loss_on_either_side(stack, people, tmp_path):
    """A big file is sent chunk by chunk. First the SENDER's PC 'loses power' mid-transfer, later the
    OWNER's PC. Each time the transfer continues from the last verified chunk - not from zero -
    and the final file is bit-identical. Also: the outbox survives an app restart."""
    MB8 = 8 * MiB
    erin = make_core(stack, "erin", "admin")
    frank = make_core(stack, "frank", "client", rate_limit=6 * MiB, chunk_size=4 * MiB)
    e_uid, f_uid = erin.me["public_id"], frank.me["public_id"]
    box = tmp_path / "Inbox"
    box.mkdir()
    fid = erin.create_folder("Inbox", box, settings={"kind": "submit"})
    erin.add_user(f_uid)
    erin.set_member(fid, f_uid, role="uploader")
    frank.join_folder(fid)
    wait_until(lambda: frank.remote_shares(e_uid), 8, msg="frank sees the folder")
    big = tmp_path / "master.mov"
    big.write_bytes(os.urandom(64 * MiB))
    frank.upload(e_uid, fid, "Inbox", "", [big])
    tid = frank.outbox_items(fid)[0]["transfer_id"]

    def received_bytes(core):
        rec = core.db.get(tid, "receiver")
        return (rec["chunks_completed"] * rec["chunk_size"]) if rec else 0
    wait_until(lambda: received_bytes(erin) >= 2 * MB8, 40, msg="first part received")
    frank.shutdown()                                           # 1) the sender's PC loses power
    before = received_bytes(erin)
    assert not (box / "master.mov").exists()                   # only the verified .part so far
    frank2 = make_core(stack, "frank", "client", login=True, rate_limit=6 * MiB, chunk_size=4 * MiB)
    resumed_at = _first_active_bytes(erin, "receive")
    assert resumed_at >= before - 4 * MiB > 0, (resumed_at, before)   # continued, not restarted
    assert frank2.outbox_items(fid)[0]["transfer_id"] == tid

    wait_until(lambda: received_bytes(erin) >= before + 2 * MB8, 40, msg="more received")
    erin.shutdown()                                            # 2) the owner's PC loses power / network
    before2 = received_bytes(erin)
    wait_until(lambda: frank2.outbox_items(fid)[0]["state"] == "queued", 60, msg="back in the outbox")
    erin2 = make_core(stack, "erin", "admin", login=True)
    try:
        resumed_at2 = _first_active_bytes(erin2, "receive")
        assert resumed_at2 >= before2 - 4 * MiB and resumed_at2 > before, (resumed_at2, before2)
        wait_until(lambda: frank2.outbox_items(fid)[0]["state"] == "delivered", 90, msg="delivered")
        assert sha(box / "master.mov") == sha(big)
        wait_until(lambda: [r["name"] for r in erin2.received_files(fid)] == ["master.mov"], 10, msg="log")
    finally:
        frank2.shutdown()
        erin2.shutdown()


def test_chat_between_linked_users_online_offline_and_read(stack, people):
    """Linked users see each other online/offline and chat end-to-end; a message to an offline user waits
    on the sender's computer and is delivered when they come online. Strangers cannot chat."""
    admin, bob, _c, mallory = people
    a_uid, b_uid = admin.me["public_id"], bob.me["public_id"]
    if b_uid not in admin.contacts:
        admin.add_user(b_uid)
    wait_until(lambda: (bob.refresh_contacts() or True) and a_uid in bob.contacts, 10, msg="linked")
    assert b_uid in admin.contacts and a_uid in bob.contacts             # both directions
    wait_until(lambda: admin.is_online(b_uid) and bob.is_online(a_uid), 8, msg="both online")
    m = admin.send_chat(b_uid, "Hi Bob, the cut is in the folder")
    wait_until(lambda: [x["text"] for x in bob.chat_history(a_uid)][-1:] == ["Hi Bob, the cut is in the folder"],
               10, msg="bob receives")
    wait_until(lambda: admin.chat_history(b_uid)[-1]["state"] == "delivered", 10, msg="delivered tick")
    assert bob.chat_summary()[a_uid]["unread"] == 1
    bob.mark_chat_read(a_uid)
    wait_until(lambda: admin.chat_history(b_uid)[-1]["state"] == "read", 10, msg="read tick")
    assert admin.chat_history(b_uid)[-1]["msg_id"] == m["msg_id"]
    with pytest.raises(PermissionError):
        mallory.send_chat(a_uid, "let me in")                           # not linked
    # bob goes offline; the admin's message waits and is delivered when bob is back
    bob.shutdown()
    wait_until(lambda: not admin.is_online(b_uid), 10, msg="bob offline")
    admin.send_chat(b_uid, "Call me when you are back")
    time.sleep(1.5)
    assert admin.chat_history(b_uid)[-1]["state"] == "queued"
    bob2 = make_core(stack, "bob", "client", login=True)
    try:
        wait_until(lambda: bob2.chat_history(a_uid) and bob2.chat_history(a_uid)[-1]["text"] ==
                   "Call me when you are back", 20, msg="delivered after coming online")
        wait_until(lambda: admin.chat_history(b_uid)[-1]["state"] == "delivered", 10, msg="delivered")
        bob2.send_chat(a_uid, "Back now!")
        wait_until(lambda: admin.chat_history(b_uid)[-1]["text"] == "Back now!", 10, msg="reply")
    finally:
        bob2.shutdown()
