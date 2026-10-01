from django.conf import settings


def ui(request):
    ctx = {"show_outbox": settings.SHOW_EMAIL_OUTBOX}
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from support.models import Ticket
        ctx["support_unread"] = Ticket.objects.filter(user=user, user_unread__gt=0).exists()
        if user.is_staff or user.is_superuser:
            ctx["staff_open"] = Ticket.objects.filter(status=Ticket.OPEN).exists()
    return ctx
