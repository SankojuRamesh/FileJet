"""Admin folders with per-user permissions, enforced by the ADMIN's app on every request.

Model
  * The admin creates a folder on their own computer (or NAS). It gets a unique ID (FD-XXXX-XXXX)
    from the cloud; the cloud never stores files.
  * The admin adds users by their ID and gives each one a role / permissions:
        read    view & download           upload  send files into the folder
        edit    rename, create folders    delete  delete files and folders
    plus an optional access expiry date. The user receives the folder ID by e-mail and opens it
    in FileJet (status invited -> active).
  * Folder rules: type Share (browse together) or Submit (drop box: uploaders cannot see the
    contents), allowed file types, maximum file size, and upload form fields whose VALUES are
    saved next to the file on the admin's computer only.
  * Paths are confined to the folder: "..", absolute paths, drive letters and symlinks that lead
    outside are rejected.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .database import TransferDB
from .util import fmt_bytes, sanitize_filename

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    folder_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    path       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'share',
    description TEXT NOT NULL DEFAULT '',
    allowed_extensions TEXT NOT NULL DEFAULT '',
    max_file_size INTEGER,
    form_fields TEXT NOT NULL DEFAULT '[]',
    notify_owner INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS folder_members (
    folder_id  TEXT NOT NULL,
    member_id  INTEGER NOT NULL,
    uid        TEXT NOT NULL,
    username   TEXT NOT NULL DEFAULT '',
    name       TEXT NOT NULL DEFAULT '',
    email      TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'custom',
    can_read   INTEGER NOT NULL DEFAULT 0,
    can_upload INTEGER NOT NULL DEFAULT 0,
    can_edit   INTEGER NOT NULL DEFAULT 0,
    can_delete INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'invited',
    expires_at REAL,
    via_group  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (folder_id, uid)
);
CREATE TABLE IF NOT EXISTS outbox (
    item_id     TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL,
    folder_id   TEXT NOT NULL,
    folder_name TEXT NOT NULL DEFAULT '',
    admin_uid   TEXT NOT NULL,
    local_path  TEXT NOT NULL,
    rel         TEXT NOT NULL,
    remote_dir  TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL,
    fields      TEXT NOT NULL DEFAULT '{}',
    transfer_id TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'queued',
    resume      INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    job_total   INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS received_files (
    transfer_id TEXT PRIMARY KEY,
    folder_id   TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    sender_uid  TEXT NOT NULL,
    sender_name TEXT NOT NULL DEFAULT '',
    local_path  TEXT NOT NULL,
    fields      TEXT NOT NULL DEFAULT '{}',
    completed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    msg_id    TEXT NOT NULL,
    peer_uid  TEXT NOT NULL,
    outgoing  INTEGER NOT NULL,
    text      TEXT NOT NULL,
    ts        REAL NOT NULL,
    state     TEXT NOT NULL,          -- outgoing: queued / delivered / read ; incoming: new / read
    PRIMARY KEY (msg_id, peer_uid, outgoing)
);
CREATE INDEX IF NOT EXISTS chat_peer ON chat_messages (peer_uid, ts);
CREATE TABLE IF NOT EXISTS contact_transfers (
    transfer_id TEXT NOT NULL,
    role        TEXT NOT NULL,
    peer_uid    TEXT NOT NULL,
    direction   TEXT NOT NULL,
    share_uid   TEXT,
    remote_path TEXT,
    local_path  TEXT,
    PRIMARY KEY (transfer_id, role)
);
"""

PAGE = 300          # directory entries per encrypted message
PERM_KEYS = ("read", "upload", "edit", "delete")
ROLES = {"uploader": (False, True, False, False), "viewer": (True, False, False, False),
         "editor": (True, True, True, False), "manager": (True, True, True, True)}
ROLE_TEXT = {"uploader": "Uploader (send files only)", "viewer": "Viewer (view & download)",
             "editor": "Editor (view, upload, rename)", "manager": "Manager (everything incl. delete)",
             "custom": "Custom"}


@dataclass
class Perms:
    read: bool = False
    upload: bool = False
    edit: bool = False
    delete: bool = False

    def allows(self, need: str) -> bool:
        if need == "mkdir":                      # folder uploads create sub-folders
            return self.upload or self.edit
        if need == "any":
            return self.any()
        return bool(getattr(self, need, False))

    def any(self) -> bool:
        return self.read or self.upload or self.edit or self.delete


def _ts(value) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class ShareStore:
    def __init__(self, db: TransferDB):
        self.db = db
        with db._lock:
            db.conn.executescript(SCHEMA)
            for table, col, decl in (("outbox", "thumb", "BLOB"), ("outbox", "thumb_done", "INTEGER DEFAULT 0"),
                                     ("received_files", "thumb", "BLOB")):
                have = {r[1] for r in db.conn.execute(f"PRAGMA table_info({table})")}
                if col not in have:
                    db.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def _q(self, sql: str, args=()):
        with self.db._lock:
            return self.db.conn.execute(sql, args).fetchall()

    # ---------------------------------------------------------------- folders
    @staticmethod
    def _folder(row) -> dict:
        d = dict(row)
        d["form_fields"] = json.loads(d.get("form_fields") or "[]")
        d["share_uid"] = d["folder_id"]
        return d

    def list(self) -> list[dict]:
        out = []
        for r in self._q("SELECT * FROM folders ORDER BY name COLLATE NOCASE"):
            d = self._folder(r)
            d["members"] = self.members(d["folder_id"])
            out.append(d)
        return out

    def get(self, folder_id: str) -> dict | None:
        rows = self._q("SELECT * FROM folders WHERE folder_id=?", (folder_id,))
        return self._folder(rows[0]) if rows else None

    def create(self, folder_id: str, name: str, path: Path, cloud_folder: dict | None = None) -> None:
        path = Path(path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"not a folder: {path}")
        self._q("INSERT OR REPLACE INTO folders (folder_id, name, path, created_at) VALUES (?,?,?,?)",
                (folder_id, name.strip()[:120] or path.name, str(path), time.time()))
        if cloud_folder:
            self.update_settings(folder_id, cloud_folder)

    def update_settings(self, folder_id: str, f: dict) -> None:
        self._q("UPDATE folders SET name=?, kind=?, description=?, allowed_extensions=?, max_file_size=?, "
                "form_fields=?, notify_owner=? WHERE folder_id=?",
                (f.get("name") or "", f.get("kind") or "share", f.get("description") or "",
                 f.get("allowed_extensions") or "", f.get("max_file_size"), json.dumps(f.get("form_fields") or []),
                 int(bool(f.get("notify_owner", True))), folder_id))

    def delete(self, folder_id: str) -> None:
        self._q("DELETE FROM folder_members WHERE folder_id=?", (folder_id,))
        self._q("DELETE FROM folders WHERE folder_id=?", (folder_id,))

    # ---------------------------------------------------------------- members
    def members(self, folder_id: str) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM folder_members WHERE folder_id=? ORDER BY name COLLATE NOCASE",
                                         (folder_id,))]

    def upsert_member(self, folder_id: str, m: dict) -> None:
        """Store a member as returned by the cloud API."""
        u, p = m["user"], m["perms"]
        self._q("INSERT OR REPLACE INTO folder_members (folder_id, member_id, uid, username, name, email, role, "
                "can_read, can_upload, can_edit, can_delete, status, expires_at, via_group) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (folder_id, int(m["id"]), u["public_id"], u["username"], u.get("display_name") or u["username"],
                 u.get("email", ""), m.get("role", "custom"), int(p["read"]), int(p["upload"]), int(p["edit"]),
                 int(p["delete"]), m.get("status", "invited"), _ts(m.get("expires_at")), m.get("via_group", "")))

    def remove_member(self, folder_id: str, uid: str) -> None:
        self._q("DELETE FROM folder_members WHERE folder_id=? AND uid=?", (folder_id, uid))

    def remove_user_everywhere(self, uid: str) -> None:
        self._q("DELETE FROM folder_members WHERE uid=?", (uid,))

    def member(self, folder_id: str, uid: str) -> dict | None:
        rows = self._q("SELECT * FROM folder_members WHERE folder_id=? AND uid=?", (folder_id, uid))
        return dict(rows[0]) if rows else None

    def perms(self, folder_id: str, uid: str) -> Perms:
        """Effective permissions: only for users who opened the folder and whose access has not expired."""
        m = self.member(folder_id, uid)
        if m is None or m["status"] != "active" or (m["expires_at"] and m["expires_at"] < time.time()):
            return Perms()
        return Perms(bool(m["can_read"]), bool(m["can_upload"]), bool(m["can_edit"]), bool(m["can_delete"]))

    def folders_for(self, uid: str) -> list[tuple[dict, Perms]]:
        out = []
        for r in self._q("SELECT f.* FROM folders f JOIN folder_members m ON m.folder_id=f.folder_id "
                         "WHERE m.uid=? ORDER BY f.name", (uid,)):
            f = self._folder(r)
            p = self.perms(f["folder_id"], uid)
            if p.any():
                out.append((f, p))
        return out

    # ---------------------------------------------------------------- outbox (user side)
    def outbox_add(self, rows: list[dict]) -> None:
        now = time.time()
        for r in rows:
            self._q("INSERT OR REPLACE INTO outbox (item_id, job_id, folder_id, folder_name, admin_uid, local_path, rel, "
                    "remote_dir, size, fields, transfer_id, state, resume, error, job_total, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["item_id"], r["job_id"], r["folder_id"], r.get("folder_name", ""), r["admin_uid"],
                     r["local_path"], r["rel"], r.get("remote_dir", ""), int(r["size"]),
                     json.dumps(r.get("fields") or {}), r["transfer_id"], r.get("state", "queued"), 0, None,
                     int(r.get("job_total", 1)), now, now))
            if r.get("thumb"):
                self._q("UPDATE outbox SET thumb=?, thumb_done=1 WHERE item_id=?", (r["thumb"], r["item_id"]))

    def outbox(self, folder_id: str | None = None, states: tuple | None = None) -> list[dict]:
        sql, args = "SELECT * FROM outbox", []
        cond = []
        if folder_id:
            cond.append("folder_id=?")
            args.append(folder_id)
        if states:
            cond.append(f"state IN ({','.join('?' * len(states))})")
            args.extend(states)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        rows = [dict(r) for r in self._q(sql + " ORDER BY created_at, rel", args)]
        for r in rows:
            r["fields"] = json.loads(r["fields"] or "{}")
        return rows

    def outbox_state(self, item_id: str) -> str | None:
        rows = self._q("SELECT state FROM outbox WHERE item_id=?", (item_id,))
        return rows[0]["state"] if rows else None

    def outbox_update(self, item_id: str, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
        self._q(f"UPDATE outbox SET {cols} WHERE item_id=?", (*fields.values(), time.time(), item_id))

    # ---------------------------------------------------------------- chat (kept only on this computer)
    def chat_add(self, msg_id: str, peer_uid: str, outgoing: bool, text: str, ts: float, state: str) -> bool:
        """Returns False for a duplicate (a message re-sent after a lost reply)."""
        before = self._q("SELECT 1 FROM chat_messages WHERE msg_id=? AND peer_uid=? AND outgoing=?",
                         (msg_id, peer_uid, int(outgoing)))
        if before:
            return False
        self._q("INSERT INTO chat_messages VALUES (?,?,?,?,?,?)", (msg_id, peer_uid, int(outgoing), text, ts, state))
        return True

    def chat_history(self, peer_uid: str, limit: int = 500) -> list[dict]:
        rows = self._q("SELECT * FROM chat_messages WHERE peer_uid=? ORDER BY ts DESC LIMIT ?", (peer_uid, limit))
        return [dict(r) for r in reversed(rows)]

    def chat_set_state(self, peer_uid: str, outgoing: bool, ids, state: str) -> None:
        for mid in ids:
            self._q("UPDATE chat_messages SET state=? WHERE msg_id=? AND peer_uid=? AND outgoing=?",
                    (state, mid, peer_uid, int(outgoing)))

    def chat_queued(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM chat_messages WHERE outgoing=1 AND state='queued' "
                                          "ORDER BY ts")]

    def chat_summary(self) -> dict:
        """peer -> {unread, last_text, last_ts, last_outgoing}"""
        out = {}
        for r in self._q("SELECT peer_uid, SUM(CASE WHEN outgoing=0 AND state='new' THEN 1 ELSE 0 END) AS unread, "
                         "MAX(ts) AS last_ts FROM chat_messages GROUP BY peer_uid"):
            last = self._q("SELECT text, outgoing FROM chat_messages WHERE peer_uid=? ORDER BY ts DESC LIMIT 1",
                           (r["peer_uid"],))[0]
            out[r["peer_uid"]] = {"unread": r["unread"] or 0, "last_ts": r["last_ts"], "last_text": last["text"],
                                  "last_outgoing": bool(last["outgoing"])}
        return out

    # ---------------------------------------------------------------- received files (admin side)
    def received_add(self, transfer_id: str, folder_id: str, rel_path: str, size: int, sender_uid: str,
                     sender_name: str, local_path: str, fields: dict | None = None, thumb: bytes | None = None) -> None:
        self._q("INSERT OR REPLACE INTO received_files (transfer_id, folder_id, rel_path, name, size, sender_uid, "
                "sender_name, local_path, fields, completed_at, thumb) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (transfer_id, folder_id, rel_path, Path(local_path).name, int(size), sender_uid, sender_name,
                 str(local_path), json.dumps(fields or {}), time.time(), thumb))

    def received(self, folder_id: str | None = None, limit: int = 500) -> list[dict]:
        if folder_id:
            rows = self._q("SELECT * FROM received_files WHERE folder_id=? ORDER BY completed_at DESC LIMIT ?",
                           (folder_id, limit))
        else:
            rows = self._q("SELECT * FROM received_files ORDER BY completed_at DESC LIMIT ?", (limit,))
        out = [dict(r) for r in rows]
        for r in out:
            r["fields"] = json.loads(r["fields"] or "{}")
        return out

    # transfers started through folders/users (so they can be resumed after an app restart)
    def remember_transfer(self, transfer_id: str, role: str, peer_uid: str, direction: str,
                          share_uid: str | None = None, remote_path: str | None = None,
                          local_path: str | None = None) -> None:
        self._q("INSERT OR REPLACE INTO contact_transfers VALUES (?,?,?,?,?,?,?)",
                (transfer_id, role, peer_uid, direction, share_uid, remote_path, local_path))

    def contact_transfer(self, transfer_id: str, role: str) -> dict | None:
        rows = self._q("SELECT * FROM contact_transfers WHERE transfer_id=? AND role=?", (transfer_id, role))
        return dict(rows[0]) if rows else None


# ------------------------------------------------------------------ rules
def check_upload_rules(folder: dict, name: str, size: int, fields: dict | None = None) -> None:
    """Pre-validation (like CloudSpeX): file type, size, required form fields. Raises PermissionError."""
    exts = [e for e in (folder.get("allowed_extensions") or "").split(",") if e]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if exts and ext not in exts:
        raise PermissionError(f"'{name}': only these file types are accepted in '{folder['name']}': "
                              f"{', '.join('.' + e for e in exts)}")
    limit = folder.get("max_file_size")
    if limit and size > limit:
        raise PermissionError(f"'{name}' is {fmt_bytes(size)}; the maximum in '{folder['name']}' is {fmt_bytes(limit)}")
    fields = fields or {}
    missing = [f["name"] for f in folder.get("form_fields") or [] if f.get("required")
               and not str(fields.get(f["name"], "")).strip()]
    if missing:
        raise PermissionError(f"please fill in: {', '.join(missing)}")


def free_space(path: str | Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


# ------------------------------------------------------------------ path safety
def clean_rel(rel: str) -> str:
    parts = []
    for p in str(rel or "").replace("\\", "/").split("/"):
        if p in ("", "."):
            continue
        if p == ".." or ":" in p or "\x00" in p:
            raise PermissionError("invalid path")
        parts.append(p)
    return "/".join(parts)


def resolve_in_share(root: str | Path, rel: str) -> Path:
    root_real = Path(root).resolve()
    rel = clean_rel(rel)
    target = (root_real / rel) if rel else root_real
    real = target.resolve()
    if real != root_real and root_real not in real.parents:
        raise PermissionError("path is outside the shared folder")
    return real


METADATA_SUFFIX = ".metadata.json"


def list_dir(path: Path, root: Path, offset: int = 0, limit: int = PAGE) -> dict:
    root_real = Path(root).resolve()
    entries = []
    with os.scandir(path) as it:
        for e in it:
            if e.name.endswith(METADATA_SUFFIX):
                continue                         # upload form sidecars are for the admin only
            try:
                st = e.stat(follow_symlinks=True)
                real = Path(e.path).resolve()
            except OSError:
                continue
            if real != root_real and root_real not in real.parents:
                continue                         # symlink escaping the folder: never shown
            is_dir = stat.S_ISDIR(st.st_mode)
            entries.append({"name": e.name, "dir": is_dir, "size": 0 if is_dir else st.st_size,
                            "mtime": st.st_mtime})
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return {"entries": entries[offset:offset + limit], "total": len(entries), "offset": offset}


class FolderSizer:
    """Total size + file count of shared folders, counted in a background thread so a huge folder never
    blocks the UI or an RPC. ``peek`` returns the last result at once and starts a recount when it is older
    than ``max_age`` seconds - so the size follows files being received."""

    def __init__(self, max_age: float = 4.0):
        self.max_age = max_age
        self._cache: dict[str, tuple[int, int, float, float]] = {}   # path -> (bytes, files, counted at, took)
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def peek(self, path) -> tuple[int, int] | None:
        key = str(path)
        with self._lock:
            hit = self._cache.get(key)
            # a folder that takes long to count is recounted less often (at most ~10 % of the time)
            stale = hit is None or time.monotonic() - hit[2] > max(self.max_age, hit[3] * 10)
            if stale and key not in self._busy:
                self._busy.add(key)
                threading.Thread(target=self._count, args=(key,), name="folder-size", daemon=True).start()
        return (hit[0], hit[1]) if hit else None

    def _count(self, key: str) -> None:
        total = files = 0
        t0 = time.monotonic()
        try:
            stack = [key]
            while stack:
                try:
                    with os.scandir(stack.pop()) as it:
                        for e in it:
                            try:
                                if e.is_dir(follow_symlinks=False):
                                    stack.append(e.path)
                                elif e.is_file(follow_symlinks=False) and not e.name.endswith(METADATA_SUFFIX):
                                    total += e.stat(follow_symlinks=False).st_size
                                    files += 1
                            except OSError:
                                continue
                except OSError:
                    continue
        finally:
            with self._lock:
                self._cache[key] = (total, files, time.monotonic(), time.monotonic() - t0)
                self._busy.discard(key)


SIZER = FolderSizer()


def size_text(size: tuple[int, int] | None) -> str:
    if size is None:
        return "counting..."
    return f"{fmt_bytes(size[0])} · {size[1]} file{'s' if size[1] != 1 else ''}"


def walk(path: Path, root: Path, max_files: int = 20000) -> dict:
    """All files below ``path`` (relative to it), for folder downloads."""
    root_real = Path(root).resolve()
    files, dirs = [], []
    for dirpath, dirnames, filenames in os.walk(path):
        dp = Path(dirpath)
        keep = []
        for d in dirnames:
            real = (dp / d).resolve()
            if root_real == real or root_real in real.parents:
                keep.append(d)
                dirs.append((dp / d).relative_to(path).as_posix())
        dirnames[:] = keep
        for f in filenames:
            if f.endswith(METADATA_SUFFIX):
                continue
            full = dp / f
            if root_real not in full.resolve().parents:
                continue
            try:
                files.append({"rel": full.relative_to(path).as_posix(), "size": full.stat().st_size})
            except OSError:
                continue
            if len(files) >= max_files:
                raise ValueError(f"folder has more than {max_files} files")
    return {"files": files, "dirs": dirs}


def safe_child(parent: Path, rel_path: str) -> Path:
    """Destination for a received folder item: sanitised components, confined to ``parent``."""
    parts = [sanitize_filename(p) for p in clean_rel(rel_path).split("/") if p]
    target = Path(parent).resolve().joinpath(*parts) if parts else Path(parent).resolve()
    if Path(parent).resolve() not in target.parents and target != Path(parent).resolve():
        raise PermissionError("path escapes the destination folder")
    return target


def write_metadata_sidecar(file_path: Path, fields: dict, uploader: str) -> None:
    """Upload form values are stored ONLY here, next to the file on the admin's computer."""
    data = {"uploaded_by": uploader, "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"), "fields": fields}
    Path(str(file_path) + METADATA_SUFFIX).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                                      encoding="utf-8")


# ------------------------------------------------------------------ admin side
class ShareService:
    """Handles clients' requests on the admin's device. ``core`` starts the P2P engines and logs activity."""

    def __init__(self, store: ShareStore, core):
        self.store = store
        self.core = core

    def _folder(self, folder_id: str, uid: str, need: str) -> tuple[dict, Perms]:
        folder = self.store.get(str(folder_id))
        perms = self.store.perms(str(folder_id), uid) if folder else Perms()
        if folder is not None and not perms.any():
            m = self.store.member(str(folder_id), uid)
            if m and m["status"] != "active" and hasattr(self.core, "refresh_member_status"):
                # The user may have *just* opened the folder by its ID: confirm with the cloud before refusing.
                self.core.refresh_member_status()
                perms = self.store.perms(str(folder_id), uid)
        if folder is None or not perms.any():
            m = self.store.member(str(folder_id), uid) if folder else None
            if m and m["expires_at"] and m["expires_at"] < time.time():
                raise PermissionError("your access to this folder has expired")
            if m and m["status"] != "active":
                raise PermissionError("open the folder with its folder ID first")
            raise PermissionError("no access to this folder")
        if not perms.allows(need):
            label = {"read": "view & download", "mkdir": "upload or edit"}.get(need, need)
            raise PermissionError(f"you do not have {label} permission on '{folder['name']}'")
        if not Path(folder["path"]).is_dir():
            raise FileNotFoundError("the folder is not available on the admin's computer right now")
        return folder, perms

    @staticmethod
    def _public(folder: dict, perms: Perms) -> dict:
        return {"share_uid": folder["folder_id"], "folder_id": folder["folder_id"], "name": folder["name"],
                "kind": folder["kind"], "description": folder["description"], "perms": asdict(perms),
                "rules": {"allowed_extensions": folder["allowed_extensions"],
                          "max_file_size": folder["max_file_size"], "form_fields": folder["form_fields"]}}

    def handle(self, uid: str, method: str, p: dict):
        if method == "shares.list":
            return [self._public(f, perms) for f, perms in self.store.folders_for(uid)]
        if method == "fs.list":
            folder, perms = self._folder(p.get("share"), uid, "any")
            if not perms.read and perms.upload:                   # submit / drop box: no listing
                return {"entries": [], "total": 0, "offset": 0, "dropbox": True,
                        "perms": asdict(perms), "rules": self._public(folder, perms)["rules"]}
            folder, perms = self._folder(p.get("share"), uid, "read")
            target = resolve_in_share(folder["path"], p.get("path", ""))
            if not target.is_dir():
                raise NotADirectoryError("not a folder")
            data = list_dir(target, folder["path"], int(p.get("offset", 0)), PAGE)
            size = SIZER.peek(folder["path"])
            if size is not None:
                data["folder_size"], data["folder_files"] = size
            data["perms"] = asdict(perms)
            data["rules"] = self._public(folder, perms)["rules"]
            return data
        if method == "fs.walk":
            folder, _ = self._folder(p.get("share"), uid, "read")
            return walk(resolve_in_share(folder["path"], p.get("path", "")), folder["path"])
        if method == "fs.stat":
            folder, _ = self._folder(p.get("share"), uid, "read")
            target = resolve_in_share(folder["path"], p.get("path", ""))
            return {"dir": target.is_dir(), "size": 0 if target.is_dir() else target.stat().st_size}
        if method == "fs.mkdir":
            folder, _ = self._folder(p.get("share"), uid, "mkdir")
            parent = resolve_in_share(folder["path"], p.get("path", ""))
            target = parent
            for part in clean_rel(p.get("name", "")).split("/"):
                if part:
                    target = target / sanitize_filename(part)
            resolve_in_share(folder["path"], target.relative_to(Path(folder["path"]).resolve()).as_posix())
            existed = target.exists()
            target.mkdir(parents=True, exist_ok=True)
            if not existed:
                self.core.log_activity(folder, uid, "mkdir", target.relative_to(Path(folder["path"]).resolve()))
            return {"ok": True}
        if method == "fs.rename":
            folder, _ = self._folder(p.get("share"), uid, "edit")
            src = resolve_in_share(folder["path"], p.get("path", ""))
            if src == Path(folder["path"]).resolve():
                raise PermissionError("cannot rename the folder itself")
            dst = src.with_name(sanitize_filename(p.get("new_name", "")))
            if dst.exists():
                raise FileExistsError("a file with that name already exists")
            src.rename(dst)
            side = Path(str(src) + METADATA_SUFFIX)
            if side.exists():
                side.rename(Path(str(dst) + METADATA_SUFFIX))
            self.core.log_activity(folder, uid, "rename", clean_rel(p.get("path", "")), f"-> {dst.name}")
            return {"ok": True}
        if method == "fs.delete":
            folder, _ = self._folder(p.get("share"), uid, "delete")
            target = resolve_in_share(folder["path"], p.get("path", ""))
            if target == Path(folder["path"]).resolve():
                raise PermissionError("cannot delete the folder itself")
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
                Path(str(target) + METADATA_SUFFIX).unlink(missing_ok=True)
            self.core.log_activity(folder, uid, "delete", clean_rel(p.get("path", "")))
            return {"ok": True}
        if method == "fs.download":
            folder, _ = self._folder(p.get("share"), uid, "read")
            rel = clean_rel(p.get("path", ""))
            target = resolve_in_share(folder["path"], rel)
            if not target.is_file():
                raise FileNotFoundError("not a file")
            return self.core.serve_download(uid, folder, rel, target, p.get("resume_tid"), p)
        if method == "fs.upload":
            folder, _ = self._folder(p.get("share"), uid, "upload")
            dest = resolve_in_share(folder["path"], p.get("dir", ""))
            if not dest.is_dir():
                raise NotADirectoryError("destination is not a folder")
            name = sanitize_filename(p.get("name", ""))
            size = int(p.get("size", 0))
            fields = {str(k)[:40]: str(v)[:500] for k, v in (p.get("fields") or {}).items()} \
                if isinstance(p.get("fields"), dict) else {}
            check_upload_rules(folder, name, size, fields)
            free = free_space(dest)
            if free is not None and free < size + (64 << 20):
                raise OSError(f"the admin's disk does not have enough free space for {name} "
                              f"({fmt_bytes(size)} needed, {fmt_bytes(free)} free)")
            return self.core.serve_upload(uid, folder, clean_rel(p.get("dir", "")), dest, name, size,
                                          p.get("resume_tid"), p.get("transfer_id"), fields, p)
        raise ValueError(f"unknown request {method!r}")
