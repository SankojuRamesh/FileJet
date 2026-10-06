"""REST client for the cloud app (accounts, contacts, subscription, transfer metadata).

The cloud never sees file contents. This client only logs in, registers the device's public
certificate, fetches contacts/permissions and reports transfer *metadata*.

Session: the refresh token is stored in ``<data_dir>/session.json`` (file mode 0600); access
tokens (15 min) are refreshed transparently, and a new signal token (1 h) is fetched when
the old one is about to expire.
"""
from __future__ import annotations

import json
import os
import platform
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import jwt as _jwt   # only used to read (not verify) token expiry locally


class CloudError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class CloudClient:
    def __init__(self, base_url: str, data_dir: Path, app: str = "sender", timeout: float = 15.0):
        self.base = base_url.rstrip("/") + "/"
        self.data_dir = Path(data_dir)
        self.app = app
        self.timeout = timeout
        self._session_file = self.data_dir / "session.json"
        self._lock = threading.RLock()
        self.access: str | None = None
        self.refresh: str | None = None
        self.user: dict | None = None
        self.device_fp: str | None = None
        self._signal: dict | None = None
        self._load()

    # ------------------------------------------------------------ plumbing
    def _load(self) -> None:
        try:
            data = json.loads(self._session_file.read_text())
            if data.get("base") == self.base:
                self.refresh, self.user = data.get("refresh"), data.get("user")
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._session_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"base": self.base, "refresh": self.refresh, "user": self.user}, f)
        os.replace(tmp, self._session_file)

    def _raw(self, method: str, path: str, body=None, token: str | None = None):
        url = urllib.parse.urljoin(self.base, path.lstrip("/"))
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:   # noqa: S310 (configured URL)
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {"detail": raw[:200].decode(errors="replace")}
            return exc.code, payload
        except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
            raise CloudError(f"cloud unreachable ({self.base}): {getattr(exc, 'reason', exc)}") from None

    @staticmethod
    def _message(payload) -> str:
        if isinstance(payload, dict):
            if "detail" in payload:
                return str(payload["detail"])
            if "reason" in payload:
                return str(payload["reason"])
            parts = [f"{k}: {' '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in payload.items()]
            return "; ".join(parts) or "request failed"
        return str(payload)

    def request(self, method: str, path: str, body=None, expect=(200, 201, 204)):
        with self._lock:
            if not self.access:
                self._refresh_access()
            token = self.access
        status, payload = self._raw(method, path, body, token)
        if status == 401 and self.refresh:
            with self._lock:
                self._refresh_access()
                token = self.access
            status, payload = self._raw(method, path, body, token)
        if status not in expect:
            raise CloudError(self._message(payload), status)
        return payload

    def _refresh_access(self) -> None:
        if not self.refresh:
            raise CloudError("not logged in", 401)
        status, payload = self._raw("POST", "api/auth/refresh/", {"refresh": self.refresh})
        if status != 200:
            self.refresh = self.access = None
            self._save()
            raise CloudError("session expired - please log in again", 401)
        self.access = payload["access"]
        self.refresh = payload.get("refresh", self.refresh)
        self._save()

    # ------------------------------------------------------------- account
    @property
    def logged_in(self) -> bool:
        return bool(self.refresh)

    def login(self, username: str, password: str) -> dict:
        status, payload = self._raw("POST", "api/auth/login/", {"username": username, "password": password})
        if status == 401:
            raise CloudError("wrong username/e-mail or password", 401)
        if status != 200:
            raise CloudError(self._message(payload), status)
        with self._lock:
            self.access, self.refresh = payload["access"], payload["refresh"]
        self.user = self.me()
        self._save()
        return self.user

    def register(self, username: str, email: str, password: str, display_name: str = "", organization: str = "",
                 location: str = "") -> dict:
        status, payload = self._raw("POST", "api/auth/register/", {
            "username": username, "email": email, "password": password, "display_name": display_name,
            "organization": organization, "location": location})
        if status != 201:
            raise CloudError(self._message(payload), status)
        with self._lock:
            self.access, self.refresh = payload["access"], payload["refresh"]
        self.user = payload["user"]
        self._save()
        return self.user

    def logout(self) -> None:
        if self.refresh:
            try:
                self._raw("POST", "api/auth/logout/", {"refresh": self.refresh}, self.access)
            except CloudError:
                pass
        self.access = self.refresh = self.user = self._signal = None
        self._save()

    def me(self) -> dict:
        self.user = self.request("GET", "api/me/")
        return self.user

    def config(self) -> dict:
        status, payload = self._raw("GET", "api/config/")
        if status != 200:
            raise CloudError(self._message(payload), status)
        return payload

    # -------------------------------------------------------------- device
    def register_device(self, identity, name: str | None = None) -> dict:
        d = self.request("POST", "api/devices/", {
            "cert_pem": identity.cert_pem, "name": name or f"{socket.gethostname()} ({self.app})",
            "platform": f"{platform.system()} {platform.release()}", "app": self.app})
        self.device_fp = d["fingerprint"]
        return d

    def signal_token(self) -> str:
        """Current signaling/presence token for this device (refreshed ~2 minutes before expiry)."""
        with self._lock:
            if self._signal and self._signal["exp"] - time.time() > 120:
                return self._signal["token"]
            data = self.request("POST", "api/signal-token/", {"fingerprint": self.device_fp})
            exp = _jwt.decode(data["token"], options={"verify_signature": False})["exp"]
            self._signal = {"token": data["token"], "exp": exp, "url": data.get("signaling_url")}
            return data["token"]

    @property
    def signaling_url(self) -> str | None:
        return (self._signal or {}).get("url")

    # ------------------------------------------------------------ contacts
    def contacts(self) -> list[dict]:
        return self.request("GET", "api/contacts/")

    def contact_requests(self) -> dict:
        return self.request("GET", "api/contact-requests/")

    def add_contact(self, query: str) -> dict:
        return self.request("POST", "api/contact-requests/", {"query": query})

    def answer_request(self, request_id: int, action: str) -> None:
        self.request("POST", f"api/contact-requests/{int(request_id)}/{action}/")

    def remove_contact(self, public_id: str) -> None:
        self.request("DELETE", f"api/contacts/{urllib.parse.quote(public_id)}/")

    # --------------------------------------------------- billing / transfers
    def subscription(self) -> dict:
        return self.request("GET", "api/billing/subscription/")

    def authorize(self, file_size: int) -> tuple[bool, str]:
        try:
            r = self.request("POST", "api/transfers/authorize/", {"file_size": int(file_size)}, expect=(200,))
            return True, r.get("reason", "ok")
        except CloudError as exc:
            if exc.status == 402:
                return False, str(exc)
            raise

    def report(self, record: dict) -> dict:
        return self.request("POST", "api/transfers/report/", dict(record, device_fingerprint=self.device_fp))

    def history(self, page: int = 1, **filters) -> dict:
        q = urllib.parse.urlencode({"page": page, **{k: v for k, v in filters.items() if v}})
        return self.request("GET", f"api/transfers/?{q}")

    def transfer_attempts(self, transfer_id: str) -> dict:
        return self.request("GET", f"api/transfers/{urllib.parse.quote(transfer_id)}/attempts/")

    # --------------------------------------------------- users, folders, members (metadata only)
    def overview(self) -> dict:
        """{"owned": [...folders with members], "member": [...], "clients": [...], "admins": [...], "groups": [...]}"""
        return self.request("GET", "api/folders/")

    def connections(self) -> list[dict]:
        return self.request("GET", "api/folders/connections/")

    def add_user(self, query: str, note: str = "") -> dict:
        return self.request("POST", "api/folders/clients/", {"query": query, "note": note})

    def remove_user(self, public_id: str) -> None:
        self.request("DELETE", f"api/folders/clients/{urllib.parse.quote(public_id)}/")

    def create_folder(self, name: str, settings: dict | None = None) -> dict:
        return self.request("POST", "api/folders/create/",
                            dict(settings or {}, name=name, device_fingerprint=self.device_fp))

    def update_folder(self, folder_id: str, data: dict) -> dict:
        return self.request("PATCH", f"api/folders/{folder_id}/", data)

    def delete_folder(self, folder_id: str) -> None:
        self.request("DELETE", f"api/folders/{folder_id}/")

    def set_member(self, folder_id: str, query: str, data: dict) -> dict:
        return self.request("POST", f"api/folders/{folder_id}/members/", dict(data, query=query))

    def update_member(self, folder_id: str, member_id: int, data: dict) -> dict:
        return self.request("PATCH", f"api/folders/{folder_id}/members/{int(member_id)}/", data)

    def remove_member(self, folder_id: str, member_id: int) -> None:
        self.request("DELETE", f"api/folders/{folder_id}/members/{int(member_id)}/")

    def resend_invite(self, folder_id: str, member_id: int) -> dict:
        return self.request("POST", f"api/folders/{folder_id}/members/{int(member_id)}/resend/")

    def join_folder(self, folder_id: str) -> dict:
        return self.request("POST", "api/folders/join/", {"folder_id": folder_id})

    def update_me(self, data: dict) -> dict:
        return self.request("PATCH", "api/me/", data)

    def send_folder_code(self, folder_id: str) -> dict:
        return self.request("POST", f"api/folders/{folder_id}/otp/send/", {})

    def verify_folder_code(self, folder_id: str, code: str) -> dict:
        return self.request("POST", f"api/folders/{folder_id}/otp/verify/", {"code": code})

    def decline_folder(self, folder_id: str) -> dict:
        return self.request("POST", "api/folders/decline/", {"folder_id": folder_id})

    def create_group(self, name: str) -> dict:
        return self.request("POST", "api/folders/groups/", {"name": name})

    def update_group(self, group_id: int, add=(), remove=(), name: str | None = None) -> dict:
        body = {"add": list(add), "remove": list(remove)}
        if name:
            body["name"] = name
        return self.request("PATCH", f"api/folders/groups/{int(group_id)}/", body)

    def delete_group(self, group_id: int) -> None:
        self.request("DELETE", f"api/folders/groups/{int(group_id)}/")

    def add_group_to_folder(self, folder_id: str, group_id: int, data: dict) -> dict:
        return self.request("POST", f"api/folders/{folder_id}/groups/", dict(data, group_id=group_id))

    # ---- support desk
    def support_tickets(self) -> dict:
        return self.request("GET", "api/support/tickets/")

    def support_open(self, subject: str, message: str, category: str = "general") -> dict:
        return self.request("POST", "api/support/tickets/", {"subject": subject, "message": message,
                                                             "category": category})

    def support_ticket(self, ticket_id: int) -> dict:
        return self.request("GET", f"api/support/tickets/{int(ticket_id)}/")

    def support_reply(self, ticket_id: int, body: str) -> dict:
        return self.request("POST", f"api/support/tickets/{int(ticket_id)}/messages/", {"body": body})

    def support_close(self, ticket_id: int) -> dict:
        return self.request("POST", f"api/support/tickets/{int(ticket_id)}/close/")

    def log_activity(self, folder_id: str, action: str, path: str = "", detail: str = "", actor: str = "") -> None:
        self.request("POST", f"api/folders/{folder_id}/activity/",
                     {"action": action, "path": path, "detail": detail, "actor": actor})

    def activity(self, folder_id: str) -> list:
        return self.request("GET", f"api/folders/{folder_id}/activity/")
