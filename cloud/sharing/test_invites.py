from django.contrib.auth import get_user_model
from django.test import TestCase

from . import services
from .models import ActivityEvent, FolderMember

User = get_user_model()


class InvitationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", "boss@example.com", "Str0ng-pass-123")
        self.bob = User.objects.create_user("bob", "bob@example.com", "Str0ng-pass-123")
        services.add_client(self.admin, "bob")
        self.f1 = services.create_folder(self.admin, "test")
        self.f2 = services.create_folder(self.admin, "other")
        for f in (self.f1, self.f2):
            services.set_member(self.admin, f, "bob", {"role": "manager"})
        self.client.force_login(self.bob)

    def test_dashboard_lists_invites_and_accept_decline(self):
        r = self.client.get("/dashboard/")
        self.assertContains(r, "Folder invitations")
        self.assertContains(r, self.f1.folder_id)
        r = self.client.post("/folders/", {"folder_id": self.f1.folder_id, "action": "accept", "next": "dashboard"})
        self.assertRedirects(r, "/dashboard/", fetch_redirect_response=False)
        self.assertEqual(FolderMember.objects.get(folder=self.f1, user=self.bob).status, FolderMember.ACTIVE)
        self.client.post("/folders/", {"folder_id": self.f2.folder_id, "action": "decline"})
        self.assertEqual(FolderMember.objects.get(folder=self.f2, user=self.bob).status, FolderMember.DECLINED)
        self.assertTrue(ActivityEvent.objects.filter(action="leave", actor=self.bob).exists())
        self.assertNotContains(self.client.get("/dashboard/"), "Folder invitations")
        page = self.client.get("/folders/")
        self.assertContains(page, "Leave")
        self.assertContains(page, "declined")
        # changed their mind later: a declined invitation can still be accepted
        self.client.post("/folders/", {"folder_id": self.f2.folder_id, "action": "accept"})
        self.assertEqual(FolderMember.objects.get(folder=self.f2, user=self.bob).status, FolderMember.ACTIVE)

    def test_api_decline(self):
        from rest_framework.test import APIClient
        c = APIClient()
        c.force_authenticate(self.bob)
        self.assertEqual(c.post("/api/folders/decline/", {"folder_id": self.f1.folder_id}, format="json").status_code, 200)
        self.assertEqual(FolderMember.objects.get(folder=self.f1, user=self.bob).status, FolderMember.DECLINED)
        self.assertEqual(c.post("/api/folders/join/", {"folder_id": self.f1.folder_id}, format="json").json()["status"], "active")
        self.assertEqual(c.post("/api/folders/decline/", {"folder_id": "FD-NONE-XXXX"}, format="json").status_code, 404)
