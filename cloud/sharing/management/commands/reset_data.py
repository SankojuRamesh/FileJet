"""Empty the cloud: every folder, share, group, user link, transfer, ticket and log, and every user except
superusers. Plans (billing settings) are kept.

    python manage.py reset_data            # asks to type RESET first
    python manage.py reset_data --yes      # no question (scripts)
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from sharing.models import ActivityEvent, ClientLink, Folder, FolderMember, Group, OutboundEmail
from support.models import Ticket, TicketMessage
from transfers.models import TransferRecord


class Command(BaseCommand):
    help = "Delete all folders, shares and users (superusers are kept)."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    def handle(self, *args, yes=False, **opts):
        User = get_user_model()
        keep = list(User.objects.filter(is_superuser=True).values_list("username", flat=True))
        if not yes and input(f"Delete ALL folders, shares and users except {keep}? Type RESET: ") != "RESET":
            raise CommandError("cancelled - nothing deleted")
        with transaction.atomic():
            for model in (TransferRecord, ActivityEvent, OutboundEmail, FolderMember, Folder, Group, ClientLink,
                          TicketMessage, Ticket):
                n, _ = model.objects.all().delete()
                self.stdout.write(f"{model.__name__}: {n} deleted")
            n, _ = User.objects.filter(is_superuser=False).delete()
            self.stdout.write(f"users (and their devices, subscriptions): {n} rows deleted")
        self.stdout.write(self.style.SUCCESS(f"Done. Kept: {', '.join(keep) or 'nobody'}"))
