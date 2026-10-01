import datetime as dt

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.services import get_subscription

from .models import Ticket

User = get_user_model()


class SupportTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", "alice@example.com", "Str0ng-pass-123", display_name="Alice")
        self.bob = User.objects.create_user("bob", "bob@example.com", "Str0ng-pass-123")
        self.admin = User.objects.create_superuser("root", "root@example.com", "Str0ng-pass-123")
        for u in (self.alice, self.bob):
            get_subscription(u)

    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_user_ticket_flow_staff_reply_unread_and_privacy(self):
        a = self.api(self.alice)
        r = a.post("/api/support/tickets/", {"subject": "Transfer stops", "message": "It stops at 80%",
                                             "category": "transfer"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        tid = r.json()["id"]
        self.assertTrue(r.json()["number"].startswith("FJ-"))
        self.assertEqual(self.api(self.bob).get(f"/api/support/tickets/{tid}/").status_code, 404)   # private
        self.assertEqual(self.api(self.bob).get("/api/support/tickets/").json()["tickets"], [])
        # staff answers in the console
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(f"/staff/tickets/{tid}/").status_code, 200)
        self.client.post(f"/staff/tickets/{tid}/", {"action": "reply", "body": "Please update to the latest version."})
        t = Ticket.objects.get(pk=tid)
        self.assertEqual((t.status, t.user_unread, t.assigned_to), (Ticket.WAITING, 1, self.admin))
        self.assertEqual(a.get("/api/support/unread/").json()["unread"], 1)
        d = a.get(f"/api/support/tickets/{tid}/").json()                     # opening marks it read
        self.assertEqual([m["from_staff"] for m in d["messages"]], [False, True])
        self.assertEqual(d["messages"][1]["author"], "FileJet Support")    # staff names hidden from users
        self.assertEqual(a.get("/api/support/unread/").json()["unread"], 0)
        a.post(f"/api/support/tickets/{tid}/messages/", {"body": "Works now, thanks!"}, format="json")
        t.refresh_from_db()
        self.assertEqual((t.status, t.staff_unread), (Ticket.OPEN, 1))
        self.assertEqual(a.post(f"/api/support/tickets/{tid}/close/").json()["status"], "closed")
        # web pages for the user
        self.client.force_login(self.alice)
        self.assertContains(self.client.get("/support/"), "Transfer stops")
        self.assertEqual(self.client.get(f"/support/{tid}/").status_code, 200)
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(f"/support/{tid}/").status_code, 404)

    def test_roles_users_cannot_see_staff_console_employees_limited(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get("/staff/").status_code, 403)
        self.assertEqual(self.client.get("/staff/users/").status_code, 403)
        # platform admin creates an employee
        self.client.force_login(self.admin)
        r = self.client.post("/staff/employees/", {"username": "priya", "email": "priya@example.com",
                                                   "password": "Supp0rt-pass-77", "display_name": "Priya"})
        self.assertEqual(r.status_code, 302)
        priya = User.objects.get(username="priya")
        self.assertTrue(priya.is_staff and not priya.is_superuser)
        self.client.force_login(priya)
        for url in ("/staff/", "/staff/users/", "/staff/tickets/", f"/staff/users/{self.alice.pk}/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/staff/employees/").status_code, 403)   # only the platform admin
        r = self.client.post(f"/staff/users/{self.alice.pk}/", {"plan": "business", "expires": "2030-01-01"})
        self.assertEqual(r.status_code, 403)                                      # cannot change subscriptions
        self.assertEqual(get_subscription(self.alice).plan.code, "free")
        # the users list shows plan and expiry
        r = self.client.get("/staff/users/?q=alice")
        self.assertContains(r, "alice@example.com")
        self.assertContains(r, get_subscription(self.alice).current_period_end.strftime("%Y-%m-%d"))

    def test_platform_admin_changes_plan_and_expiry(self):
        self.client.force_login(self.admin)
        r = self.client.post(f"/staff/users/{self.alice.pk}/", {"plan": "pro", "expires": "2030-06-30",
                                                                "status": "active", "end_at_expiry": "1"})
        self.assertEqual(r.status_code, 302)
        sub = get_subscription(self.alice)
        self.assertEqual((sub.plan.code, sub.current_period_end.date(), sub.cancel_at_period_end),
                         ("pro", dt.date(2030, 6, 30), True))
        r = self.client.get("/staff/users/?expiring=7")                          # alice is not expiring soon
        self.assertNotContains(r, "alice@example.com")
        self.assertGreater((sub.current_period_end - timezone.now()).days, 7)
