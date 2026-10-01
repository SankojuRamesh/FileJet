"""Support tickets: a user opens an issue, support employees answer it in a chat-style conversation."""
from django.conf import settings
from django.db import models


class Ticket(models.Model):
    OPEN, WAITING, CLOSED = "open", "waiting", "closed"
    STATUSES = [(OPEN, "Open"), (WAITING, "Waiting for user"), (CLOSED, "Closed")]
    CATEGORIES = [("general", "General question"), ("transfer", "Transfers & connection"),
                  ("account", "Account & sign-in"), ("billing", "Billing & subscription"), ("bug", "Bug report")]
    PRIORITIES = [("normal", "Normal"), ("high", "High")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tickets")
    subject = models.CharField(max_length=150)
    category = models.CharField(max_length=20, choices=CATEGORIES, default="general")
    priority = models.CharField(max_length=10, choices=PRIORITIES, default="normal")
    status = models.CharField(max_length=10, choices=STATUSES, default=OPEN)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="assigned_tickets", limit_choices_to={"is_staff": True})
    user_unread = models.PositiveIntegerField(default=0)       # staff replies the user has not seen yet
    staff_unread = models.PositiveIntegerField(default=0)      # user messages support has not seen yet
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_message_at"]
        indexes = [models.Index(fields=["status", "-last_message_at"])]

    def __str__(self):
        return f"{self.number} {self.subject}"

    @property
    def number(self) -> str:
        return f"FJ-{self.pk:05d}"


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    from_staff = models.BooleanField(default=False)
    body = models.TextField(max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
