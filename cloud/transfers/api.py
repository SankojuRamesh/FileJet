from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.services import check_transfer, usage_summary

from . import services
from .models import TransferRecord
from .serializers import TransferSerializer


class ReportView(APIView):
    """Desktop apps POST their view of a transfer every few seconds and on every state change."""

    def post(self, request):
        try:
            rec = services.apply_report(request.user, request.data)
        except services.ReportError as exc:
            return Response({"detail": str(exc)}, status=exc.status)
        return Response({"status": rec.status})


class AuthorizeView(APIView):
    """Plan limits check before a transfer starts (file size, monthly quota)."""

    def post(self, request):
        try:
            size = int(request.data.get("file_size", 0))
        except (TypeError, ValueError):
            return Response({"detail": "file_size required"}, status=400)
        ok, reason = check_transfer(request.user, size)
        return Response({"allowed": ok, "reason": reason, "usage": usage_summary(request.user)},
                        status=200 if ok else 402)


def filtered(request):
    qs = services.visible_to(request.user)
    params = request.GET
    status = params.get("status")
    if params.get("active") == "1":
        qs = qs.filter(status__in=TransferRecord.LIVE)
    elif status:
        qs = qs.filter(status=status)
    q = params.get("q")
    if q:
        qs = qs.filter(Q(file_name__icontains=q) | Q(share_name__icontains=q) | Q(sender__username__icontains=q)
                       | Q(receiver__username__icontains=q) | Q(transfer_id__startswith=q))
    role = params.get("role")
    if role == "sent":
        qs = qs.filter(sender=request.user)
    elif role == "received":
        qs = qs.filter(receiver=request.user)
    return qs


class TransferListView(APIView):
    def get(self, request):
        paginator = PageNumberPagination()
        paginator.page_size = min(200, int(request.query_params.get("page_size", 25) or 25))
        qs = filtered(request)
        if request.query_params.get("attempts") != "all":
            qs = services.with_attempts(qs)
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(TransferSerializer(page, many=True,
                                                                   context={"user": request.user}).data)


class TransferDetailView(APIView):
    def get(self, request, transfer_id):
        rec = get_object_or_404(services.visible_to(request.user), transfer_id=transfer_id)
        return Response(TransferSerializer(rec, context={"user": request.user}).data)


class AttemptsView(APIView):
    """Every try of one file: when it started, how it ended (failed / completed) and why."""

    def get(self, request, transfer_id):
        log = services.attempt_log(request.user, transfer_id)
        if not log:
            return Response({"detail": "not found"}, status=404)
        total, fails = len(log), sum(1 for r in log if r.status in services.FAILED_STATES)
        first = max(0, total - 200)                                   # the newest 200 tries
        return Response({"total": total, "failures": fails, "shown_from": first + 1, "attempts": [
            {"n": i + 1, "transfer_id": r.transfer_id, "status": r.status, "error": r.error,
             "started_at": r.started_at, "ended_at": r.completed_at or (r.updated_at if r.status not in r.LIVE else None),
             "duration": r.duration, "bytes_transferred": r.bytes_transferred, "file_size": r.file_size,
             "progress": r.progress, "avg_speed": r.avg_speed, "connection_type": r.connection_type}
            for i, r in enumerate(log) if i >= first]})


class StatsView(APIView):
    def get(self, request):
        u = request.user
        qs = services.visible_to(u).filter(superseded=False)          # one per file, not per retry
        month = timezone.now() - timedelta(days=30)
        agg = qs.aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            failed=Count("id", filter=Q(status="failed")),
            active=Count("id", filter=Q(status__in=TransferRecord.LIVE)),
            sent_30d=Sum("file_size", filter=Q(sender=u, status="completed", completed_at__gte=month)),
            received_30d=Sum("file_size", filter=Q(receiver=u, status="completed", completed_at__gte=month)),
            files_30d=Count("id", filter=Q(status="completed", completed_at__gte=month)),
        )
        agg = {k: (v or 0) for k, v in agg.items()}
        return Response({**agg, "usage": usage_summary(u)})
