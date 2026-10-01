"""Support API for the desktop app (JWT): the signed-in user's own tickets only."""
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .models import Ticket


class _Base(APIView):
    def ticket(self, request, pk) -> Ticket:
        return get_object_or_404(Ticket, pk=pk, user=request.user)


class TicketsView(_Base):
    throttle_scope = "support"

    def get_throttles(self):
        return [ScopedRateThrottle()] if self.request.method == "POST" else super().get_throttles()

    def get(self, request):
        qs = Ticket.objects.filter(user=request.user)[:200]
        return Response({"tickets": [services.ticket_json(t) for t in qs],
                         "unread": sum(t.user_unread for t in qs)})

    def post(self, request):
        try:
            t = services.open_ticket(request.user, request.data.get("subject", ""), request.data.get("message", ""),
                                     request.data.get("category", "general"))
        except services.SupportError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(services.ticket_json(t, messages=True), status=201)


class TicketDetailView(_Base):
    def get(self, request, pk):
        t = self.ticket(request, pk)
        services.mark_read(t, by_staff=False)
        return Response(services.ticket_json(t, messages=True))


class TicketMessagesView(_Base):
    throttle_scope = "support"
    throttle_classes = [ScopedRateThrottle]

    def post(self, request, pk):
        t = self.ticket(request, pk)
        try:
            services.add_message(t, request.user, request.data.get("body", ""))
        except services.SupportError as exc:
            return Response({"detail": str(exc)}, status=400)
        services.mark_read(t, by_staff=False)
        return Response(services.ticket_json(t, messages=True), status=201)


class TicketCloseView(_Base):
    def post(self, request, pk):
        t = self.ticket(request, pk)
        t.status = Ticket.CLOSED
        t.save(update_fields=["status", "updated_at"])
        return Response(services.ticket_json(t))


class UnreadView(APIView):
    def get(self, request):
        n = sum(Ticket.objects.filter(user=request.user, user_unread__gt=0).values_list("user_unread", flat=True))
        return Response({"unread": n})
