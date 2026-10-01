"""Cloud app tests: auth, devices, signal tokens, plans, users by ID, folders, members, queued uploads, pages."""
import datetime as dt
import secrets

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User

GB = 10 ** 9


def make_cert() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "t")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


class Base(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()                     # throttle counters live in the cache

    def register(self, username):
        c = APIClient()
        r = c.post("/api/auth/register/", {"username": username, "email": f"{username}@example.com",
                                           "password": "Str0ng-pass-123", "display_name": username.title()},
                   format="json")
        self.assertEqual(r.status_code, 201, r.content)
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")
        user = User.objects.get(username=username)
        cert = make_cert()
        d = c.post("/api/devices/", {"name": f"{username}-pc", "cert_pem": cert, "app": "sender"}, format="json")
        self.assertEqual(d.status_code, 201, d.content)
        return c, user, d.json()["fingerprint"]

    def link(self, admin, user, fid=None, role="uploader"):
        """Admin adds the user by ID (and optionally gives access to a folder)."""
        admin[0].post("/api/folders/clients/", {"query": user[1].public_id}, format="json")
        if fid:
            r = admin[0].post(f"/api/folders/{fid}/members/", {"query": user[1].public_id, "role": role},
                              format="json")
            self.assertIn(r.status_code, (200, 201), r.content)
            user[0].post("/api/folders/join/", {"folder_id": fid}, format="json")


class AuthTests(Base):
    def test_register_login_refresh_logout(self):
        c, user, fp = self.register("alice")
        anon = APIClient()
        for ident in ("alice", "alice@example.com", "ALICE"):
            r = anon.post("/api/auth/login/", {"username": ident, "password": "Str0ng-pass-123"}, format="json")
            self.assertEqual(r.status_code, 200, ident)
        self.assertEqual(anon.post("/api/auth/login/", {"username": "alice", "password": "nope"},
                                   format="json").status_code, 401)
        tokens = r.json()
        r2 = anon.post("/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(r2.status_code, 200)
        me = APIClient()
        me.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        body = me.get("/api/me/").json()
        self.assertEqual(body["username"], "alice")
        self.assertEqual(len(body["public_id"]), 9)
        self.assertEqual(body["subscription"]["plan"]["code"], "free")
        me.post("/api/auth/logout/", {"refresh": r2.json()["refresh"]}, format="json")
        self.assertEqual(anon.post("/api/auth/refresh/", {"refresh": r2.json()["refresh"]},
                                   format="json").status_code, 401)

    def test_register_validation(self):
        self.register("bob")
        r = APIClient().post("/api/auth/register/", {"username": "bob", "email": "x@example.com",
                                                     "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = APIClient().post("/api/auth/register/", {"username": "carl", "email": "c@example.com",
                                                     "password": "123"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_register_is_throttled(self):
        codes = [APIClient().post("/api/auth/register/", {"username": f"user{i}", "email": f"u{i}@example.com",
                                                         "password": "Str0ng-pass-123"}, format="json").status_code
                 for i in range(6)]
        self.assertEqual(codes, [201] * 5 + [429])

    def test_api_requires_auth(self):
        for url in ("/api/me/", "/api/folders/", "/api/transfers/", "/api/folders/connections/"):
            self.assertEqual(APIClient().get(url).status_code, 401, url)


class DeviceAndTokenTests(Base):
    def test_signal_token_claims(self):
        c, user, fp = self.register("alice")
        r = c.post("/api/signal-token/", {"fingerprint": fp}, format="json")
        self.assertEqual(r.status_code, 200)
        claims = jwt.decode(r.json()["token"], settings.SIGNAL_JWT_SECRET, algorithms=["HS256"])
        self.assertEqual((claims["uid"], claims["fp"], claims["typ"]), (user.public_id, fp, "signal"))
        self.assertNotIn("relay", claims)                              # no relay: file data never via servers

    def test_device_cannot_be_hijacked_or_used_after_revoke(self):
        a = self.register("alice")
        b = self.register("bob")
        cert = a[1].devices.first().cert_pem
        r = b[0].post("/api/devices/", {"name": "x", "cert_pem": cert}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(b[0].post("/api/signal-token/", {"fingerprint": a[2]}, format="json").status_code, 403)
        dev_id = a[0].get("/api/devices/").json()[0]["id"]
        a[0].delete(f"/api/devices/{dev_id}/")
        self.assertEqual(a[0].post("/api/signal-token/", {"fingerprint": a[2]}, format="json").status_code, 403)
        self.assertEqual(a[0].post("/api/devices/", {"name": "x", "cert_pem": "junk"}, format="json").status_code, 400)


class BillingTests(Base):
    def test_plans_and_change(self):
        a = self.register("alice")
        plans = APIClient().get("/api/billing/plans/").json()
        self.assertEqual([p["code"] for p in plans], ["free", "pro", "business"])
        r = a[0].post("/api/billing/subscription/", {"plan": "pro"}, format="json")
        self.assertEqual(r.json()["plan"]["code"], "pro")
        a[0].post("/api/billing/subscription/cancel/")
        self.assertTrue(a[0].get("/api/billing/subscription/").json()["cancel_at_period_end"])

    def test_authorize_limits(self):
        a = self.register("alice")
        self.assertEqual(a[0].post("/api/transfers/authorize/", {"file_size": GB}, format="json").status_code, 200)
        r = a[0].post("/api/transfers/authorize/", {"file_size": 25 * GB}, format="json")
        self.assertEqual(r.status_code, 402)
        self.assertIn("upgrade", r.json()["reason"])


class ReportTests(Base):
    def report(self, side, **kw):
        c, user, fp = side
        data = {"transfer_id": kw.pop("tid"), "device_fingerprint": fp, **kw}
        return c.post("/api/transfers/report/", data, format="json")

    def test_sender_and_receiver_merge(self):
        a, b = self.register("alice"), self.register("bob")
        tid = secrets.token_hex(8)
        r = self.report(a, tid=tid, role="sender", state="active", file_name="movie.iso", file_size=1000,
                        bytes_transferred=300, peer_fingerprint=b[2], initiator=True, connection_type="DIRECT P2P")
        self.assertEqual(r.json()["status"], "active")
        self.report(b, tid=tid, role="receiver", state="active", bytes_transferred=400, peer_fingerprint=a[2])
        rec = a[0].get(f"/api/transfers/{tid}/").json()
        self.assertEqual((rec["sender"]["username"], rec["receiver"]["username"]), ("alice", "bob"))
        self.assertEqual(rec["bytes_transferred"], 400)                 # receiver is authoritative
        self.assertEqual(rec["my_role"], "sender")
        self.assertEqual(b[0].get(f"/api/transfers/{tid}/").json()["my_role"], "receiver")
        self.report(b, tid=tid, role="receiver", state="completed", avg_speed=110e6, file_hash="ab" * 32)
        rec = b[0].get(f"/api/transfers/{tid}/").json()
        self.assertEqual((rec["status"], rec["progress"]), ("completed", 100.0))
        self.report(a, tid=tid, role="sender", state="failed")          # completed never regresses
        self.assertEqual(a[0].get(f"/api/transfers/{tid}/").json()["status"], "completed")
        stats = a[0].get("/api/transfers/stats/").json()
        self.assertEqual((stats["completed"], stats["sent_30d"]), (1, 1000))
        self.assertEqual(a[0].get("/api/me/").json()["usage"]["used_bytes"], 1000)   # initiator's quota

    def test_foreign_users_cannot_claim_or_see(self):
        a, b, m = self.register("alice"), self.register("bob"), self.register("mallory")
        tid = secrets.token_hex(8)
        self.report(a, tid=tid, role="sender", state="active", file_name="x", file_size=10, peer_fingerprint=b[2])
        self.assertEqual(self.report(m, tid=tid, role="sender", state="active").status_code, 403)
        self.assertEqual(self.report(m, tid=tid, role="receiver", state="active").status_code, 403)
        self.assertEqual(m[0].get(f"/api/transfers/{tid}/").status_code, 404)
        self.assertEqual(m[0].get("/api/transfers/").json()["count"], 0)
        # a device of another account cannot be used for reporting
        r = m[0].post("/api/transfers/report/", {"transfer_id": secrets.token_hex(8), "role": "sender",
                                                 "device_fingerprint": a[2]}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.report(a, tid="../x", role="sender").status_code, 400)

    def test_list_filters(self):
        a, b = self.register("alice"), self.register("bob")
        for i, state in enumerate(["completed", "failed", "active"]):
            self.report(a, tid=secrets.token_hex(8), role="sender", state=state, file_name=f"f{i}.bin",
                        file_size=5, peer_fingerprint=b[2])
        self.assertEqual(a[0].get("/api/transfers/?active=1").json()["count"], 1)
        self.assertEqual(a[0].get("/api/transfers/?status=failed").json()["results"][0]["file_name"], "f1.bin")
        self.assertEqual(a[0].get("/api/transfers/?q=f2").json()["count"], 1)
        self.assertEqual(b[0].get("/api/transfers/?role=received").json()["count"], 3)


class FolderTests(Base):
    """Admin adds users by ID -> folder -> member (role / permissions) -> e-mail -> client opens by folder ID."""

    def setUp(self):
        super().setUp()
        from django.core import mail
        mail.outbox = []

    def make_folder(self, admin, **extra):
        r = admin[0].post("/api/folders/create/", {"name": "Deliveries", "device_fingerprint": admin[2], **extra},
                          format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_full_flow_with_email_and_join(self):
        from django.core import mail
        a, b, m = self.register("alice"), self.register("bob"), self.register("mallory")
        f = self.make_folder(a, kind="submit", allowed_extensions=".MP4, mov;pdf", max_file_size=5 * GB,
                             form_fields=[{"name": "Project", "required": True}, {"name": "Notes"}])
        self.assertRegex(f["folder_id"], r"^FD-[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertEqual((f["kind"], f["allowed_extensions"]), ("submit", "mp4,mov,pdf"))
        fid = f["folder_id"]
        # a user must be added by ID before they can get access
        r = a[0].post(f"/api/folders/{fid}/members/", {"query": b[1].public_id, "role": "uploader"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(a[0].post("/api/folders/clients/", {"query": b[1].formatted_id, "note": "Editor"},
                                   format="json").status_code, 201)
        r = a[0].post(f"/api/folders/{fid}/members/", {"query": b[1].public_id, "role": "uploader",
                                                       "expires_at": "2099-12-31"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        member = r.json()
        self.assertEqual((member["role"], member["perms"], member["status"]),
                         ("uploader", {"read": False, "upload": True, "edit": False, "delete": False}, "invited"))
        # the invitation e-mail carries the folder ID
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["bob@example.com"])
        self.assertIn(fid, mail.outbox[0].body)
        self.assertIn("Submit", mail.outbox[0].body)
        # bob sees the invitation and opens the folder by its ID (spaces / lower case tolerated)
        mine = b[0].get("/api/folders/").json()["member"]
        self.assertEqual([(x["folder_id"], x["status"]) for x in mine], [(fid, "invited")])
        r = b[0].post("/api/folders/join/", {"folder_id": fid.lower().replace("-", " ")}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "active"))
        # mallory knows the ID but was never given access
        self.assertEqual(m[0].post("/api/folders/join/", {"folder_id": fid}, format="json").status_code, 404)
        # connections: alice <-> bob only
        self.assertEqual([c["username"] for c in a[0].get("/api/folders/connections/").json()], ["bob"])
        self.assertEqual([c["username"] for c in b[0].get("/api/folders/connections/").json()], ["alice"])
        self.assertEqual(m[0].get("/api/folders/connections/").json(), [])
        # only the owner can change members or settings
        self.assertEqual(b[0].patch(f"/api/folders/{fid}/", {"name": "x"}, format="json").status_code, 404)
        self.assertEqual(b[0].patch(f"/api/folders/{fid}/members/{member['id']}/", {"role": "manager"},
                                    format="json").status_code, 403)
        r = a[0].patch(f"/api/folders/{fid}/members/{member['id']}/", {"read": True, "upload": True, "edit": True},
                       format="json")
        self.assertEqual(r.json()["role"], "editor")
        events = [e["action"] for e in a[0].get(f"/api/folders/{fid}/activity/").json()]
        self.assertEqual(events[:3], ["access", "join", "access"])
        # removing the user removes all their access
        a[0].delete(f"/api/folders/clients/{b[1].public_id}/")
        self.assertEqual(b[0].get("/api/folders/").json()["member"], [])

    def test_expired_access_cannot_open(self):
        a, b = self.register("alice"), self.register("bob")
        fid = self.make_folder(a)["folder_id"]
        a[0].post("/api/folders/clients/", {"query": "bob"}, format="json")
        a[0].post(f"/api/folders/{fid}/members/", {"query": "bob", "role": "viewer", "expires_at": "2000-01-01"},
                  format="json")
        r = b[0].post("/api/folders/join/", {"folder_id": fid}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("expired", r.json()["detail"])

    def test_groups_give_access_to_everyone_in_them(self):
        from django.core import mail
        a, b, c = self.register("alice"), self.register("bob"), self.register("carl")
        for u in ("bob", "carl"):
            a[0].post("/api/folders/clients/", {"query": u}, format="json")
        g = a[0].post("/api/folders/groups/", {"name": "Editors"}, format="json").json()
        r = a[0].patch(f"/api/folders/groups/{g['id']}/", {"add": [b[1].public_id, c[1].public_id]}, format="json")
        self.assertEqual(len(r.json()["members"]), 2)
        fid = self.make_folder(a)["folder_id"]
        r = a[0].post(f"/api/folders/{fid}/groups/", {"group_id": g["id"], "role": "editor"}, format="json")
        self.assertEqual(r.json()["added"], 2)
        members = r.json()["folder"]["members"]
        self.assertEqual({(x["user"]["username"], x["role"], x["via_group"]) for x in members},
                         {("bob", "editor", "Editors"), ("carl", "editor", "Editors")})
        self.assertEqual(len(mail.outbox), 2)

    def test_batch_completion_notifies_owner_once(self):
        from django.core import mail
        a, b = self.register("alice"), self.register("bob")
        a[0].post("/api/folders/clients/", {"query": "bob"}, format="json")
        fid = self.make_folder(a, kind="submit")["folder_id"]
        a[0].post(f"/api/folders/{fid}/members/", {"query": "bob", "role": "uploader"}, format="json")
        mail.outbox = []
        job = secrets.token_hex(8)
        for i in range(2):
            tid = secrets.token_hex(8)
            base = {"transfer_id": tid, "file_name": f"f{i}.mp4", "file_size": 10, "direction": "upload",
                    "folder_id": fid, "job_id": job, "job_total": 2, "share_name": "Deliveries"}
            b[0].post("/api/transfers/report/", dict(base, role="sender", device_fingerprint=b[2],
                                                     peer_fingerprint=a[2], state="active", initiator=True),
                      format="json")
            with self.captureOnCommitCallbacks(execute=True):
                a[0].post("/api/transfers/report/", dict(base, role="receiver", device_fingerprint=a[2],
                                                         peer_fingerprint=b[2], state="completed"), format="json")
            self.assertEqual(len(mail.outbox), i)          # nothing after file 1, one mail after file 2
        self.assertEqual(mail.outbox[0].to, ["alice@example.com"])
        self.assertIn("uploaded 2 file(s)", mail.outbox[0].subject)

    def test_folder_limit(self):
        a = self.register("alice")
        for i in range(3):
            self.make_folder(a)
        self.assertEqual(a[0].post("/api/folders/create/", {"name": "4"}, format="json").status_code, 402)

    def test_queued_upload_while_admin_offline_and_folder_stats(self):
        """A user sends while the admin is offline: the cloud records it as 'queued' for the admin
        (metadata only); after delivery it counts in the folder statistics for both sides."""
        a, b, m = self.register("alice"), self.register("bob"), self.register("mallory")
        fid = self.make_folder(a, kind="submit")["folder_id"]
        self.link(a, b, fid, role="uploader")
        tid = secrets.token_hex(8)
        base = {"transfer_id": tid, "file_name": "cut.mov", "file_size": 5000, "direction": "upload",
                "folder_id": fid, "relative_path": "cut.mov", "job_id": secrets.token_hex(8), "job_total": 1}
        r = b[0].post("/api/transfers/report/", dict(base, role="sender", state="queued", device_fingerprint=b[2],
                                                     initiator=True), format="json")
        self.assertEqual(r.json()["status"], "queued")
        rec = a[0].get(f"/api/transfers/{tid}/").json()                 # the admin sees it already
        self.assertEqual((rec["status"], rec["receiver"]["username"], rec["sender"]["username"]),
                         ("queued", "alice", "bob"))
        files = a[0].get(f"/api/folders/{fid}/files/").json()
        self.assertEqual((files["stats"]["waiting"], files["files"][0]["file_name"]), (1, "cut.mov"))
        # a non-member naming the folder is not attached to the admin
        r = m[0].post("/api/transfers/report/", dict(base, transfer_id=secrets.token_hex(8), role="sender",
                                                     state="queued", device_fingerprint=m[2]), format="json")
        self.assertEqual(a[0].get(f"/api/folders/{fid}/files/").json()["stats"]["waiting"], 1)
        self.assertEqual(m[0].get(f"/api/folders/{fid}/files/").status_code, 404)
        # the admin comes online and receives it
        a[0].post("/api/transfers/report/", dict(base, role="receiver", state="completed", device_fingerprint=a[2],
                                                 peer_fingerprint=b[2]), format="json")
        stats = a[0].get(f"/api/folders/{fid}/files/").json()["stats"]
        self.assertEqual((stats["files"], stats["bytes"], stats["waiting"], stats["senders"]), (1, 5000, 0, 1))
        mine = b[0].get("/api/folders/").json()["member"][0]["stats"]
        self.assertEqual((mine["files"], mine["bytes"]), (1, 5000))    # the user's own view


class PageTests(Base):
    def test_pages_render_and_require_login(self):
        for url in ("/dashboard/", "/transfers/", "/folders/", "/users/", "/inbox/", "/devices/", "/subscription/",
                    "/account/"):
            self.assertEqual(self.client.get(url).status_code, 302, url)
        for url in ("/", "/features/", "/pricing/", "/download/", "/about/"):      # public website
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get("/pricing/"), "Pro")                    # plans from the database
        self.register("alice")
        self.assertTrue(self.client.login(username="alice@example.com", password="Str0ng-pass-123"))
        for url in ("/dashboard/", "/transfers/", "/folders/", "/users/", "/inbox/", "/devices/", "/subscription/",
                    "/account/", "/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        self.assertContains(self.client.get("/api/transfers/stats/"), "completed")

    def test_web_register(self):
        r = self.client.post("/register/", {"username": "zoe", "email": "z@example.com", "display_name": "Zoe",
                                            "password": "Str0ng-pass-123", "password2": "Str0ng-pass-123"})
        self.assertRedirects(r, "/dashboard/")
        self.assertEqual(User.objects.get(username="zoe").subscription.plan.code, "free")
