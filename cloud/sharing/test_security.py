import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import services
from .models import FolderMember, OutboundEmail

User = get_user_model()


class SecurityTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", "boss@example.com", "Str0ng-pass-123")
        self.bob = User.objects.create_user("bob", "bob@example.com", "Str0ng-pass-123")
        services.add_client(self.admin, "bob")
        self.f = services.create_folder(self.admin, "Inbox")
        self.m, _ = services.set_member(self.admin, self.f, "bob", {"role": "uploader", "require_otp": True,
                                                                    "approve_uploads": "first"})
        self.c = APIClient()
        self.c.force_authenticate(self.bob)
        self.url = f"/api/folders/{self.f.folder_id}/otp/"

    def code(self):
        return re.search(r"Your code:\s+(\d{6})", OutboundEmail.objects.filter(kind="otp").order_by("pk").last().body).group(1)

    def test_security_saved_and_sent_to_both_sides(self):
        self.assertEqual((self.m.require_otp, self.m.approve_uploads), (True, "first"))
        self.assertIn("code by e-mail", OutboundEmail.objects.get(kind="folder_invite").body)
        member = self.c.get("/api/folders/").json()["member"][0]
        self.assertEqual(member["security"], {"require_otp": True, "otp_verified": False, "otp_channel": "email",
                                              "approve_uploads": "first", "first_upload_approved": False,
                                              "delete_scope": "own"})

    def test_otp_flow_wrong_code_limit_resend_wait(self):
        r = self.c.post(self.url + "send/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["sent_to"], "bo***@example.com")
        self.assertEqual(self.c.post(self.url + "send/").status_code, 429)          # 60 s between codes
        good = self.code()
        bad = "000000" if good != "000000" else "111111"
        m = FolderMember.objects.get(pk=self.m.pk)
        self.assertNotIn(good, m.otp_hash)                                          # only a keyed hash is stored
        for _ in range(4):
            self.assertEqual(self.c.post(self.url + "verify/", {"code": bad}).status_code, 400)
        r = self.c.post(self.url + "verify/", {"code": bad})
        self.assertIn("too many", r.json()["detail"])
        self.assertEqual(self.c.post(self.url + "verify/", {"code": good}).status_code, 429)   # locked: new code
        FolderMember.objects.filter(pk=self.m.pk).update(otp_sent_at=None)
        r = self.c.post(self.url + "send/")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.c.post(self.url + "verify/", {"code": self.code()})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["security"]["otp_verified"], True)

    def test_only_owner_changes_security_and_first_approval(self):
        other = APIClient()
        other.force_authenticate(self.bob)
        url = f"/api/folders/{self.f.folder_id}/members/{self.m.pk}/"
        self.assertEqual(other.patch(url, {"first_upload_approved": True}, format="json").status_code, 403)
        own = APIClient()
        own.force_authenticate(self.admin)
        r = own.patch(url, {"first_upload_approved": True, "approve_uploads": "every"}, format="json")
        self.assertEqual(r.json()["security"]["approve_uploads"], "every")
        self.assertTrue(r.json()["security"]["first_upload_approved"])
        self.assertEqual(r.json()["perms"]["upload"], True)                       # permissions untouched


class PhoneCodeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", "boss@example.com", "Str0ng-pass-123")
        self.bob = User.objects.create_user("bob", "bob@example.com", "Str0ng-pass-123")
        services.add_client(self.admin, "bob")
        self.f = services.create_folder(self.admin, "Inbox")
        self.m, _ = services.set_member(self.admin, self.f, "bob", {"role": "uploader", "require_otp": True,
                                                                    "otp_channel": "sms"})
        self.c = APIClient()
        self.c.force_authenticate(self.bob)
        self.url = f"/api/folders/{self.f.folder_id}/otp/"

    def test_phone_number_is_validated_and_saved(self):
        self.assertEqual(self.c.patch("/api/me/", {"phone": "98765"}, format="json").status_code, 400)
        r = self.c.patch("/api/me/", {"phone": "+91 98765-43210"}, format="json")
        self.assertEqual(r.json()["phone"], "+919876543210")
        self.client.force_login(self.bob)
        r = self.client.post("/account/", {"form": "profile", "display_name": "Bob", "organization": "",
                                           "email": "bob@example.com", "phone": "0044 7700 900123"})
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.phone, "+447700900123")

    def test_sms_code_needs_a_number_then_works(self):
        self.assertIn("by SMS", OutboundEmail.objects.get(kind="folder_invite").body)
        r = self.c.post(self.url + "send/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("mobile number", r.json()["detail"])
        self.c.patch("/api/me/", {"phone": "+919876543210"}, format="json")
        with self.settings(DEBUG=True):                           # console provider in development
            r = self.c.post(self.url + "send/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["channel"], r.json()["sent_to"]), ("sms", "+91******210"))
        code = re.search(r"Your code:\s+(\d{6})", OutboundEmail.objects.filter(kind="otp").order_by("pk").last().body).group(1)
        r = self.c.post(self.url + "verify/", {"code": code})
        self.assertTrue(r.json()["security"]["otp_verified"])

    def test_whatsapp_not_set_up_on_server(self):
        self.m.otp_channel = "whatsapp"
        self.m.save()
        self.c.patch("/api/me/", {"phone": "+919876543210"}, format="json")
        with self.settings(DEBUG=False):
            r = self.c.post(self.url + "send/")
        self.assertEqual(r.status_code, 503)
        self.assertIn("not set up", r.json()["detail"])

    def test_twilio_request(self):
        from unittest import mock
        from . import messaging
        env = {"P2P_SMS_PROVIDER": "twilio", "TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t",
               "TWILIO_WHATSAPP_FROM": "+14155238886"}
        sent = {}

        class Resp:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake(req, timeout=0):
            sent["url"], sent["data"] = req.full_url, req.data.decode()
            return Resp()
        with mock.patch.dict("os.environ", env), mock.patch("urllib.request.urlopen", fake):
            messaging.send("whatsapp", "+919876543210", "hi", "123456")
        self.assertIn("/Accounts/AC1/Messages.json", sent["url"])
        self.assertIn("To=whatsapp%3A%2B919876543210", sent["data"])
        self.assertIn("From=whatsapp%3A%2B14155238886", sent["data"])
