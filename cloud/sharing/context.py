from django.conf import settings


def ui(request):
    return {"show_outbox": settings.SHOW_EMAIL_OUTBOX}
