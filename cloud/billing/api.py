from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Plan
from .serializers import PlanSerializer, SubscriptionSerializer


class PlansView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(PlanSerializer(Plan.objects.filter(public=True), many=True).data)


class SubscriptionView(APIView):
    def get(self, request):
        sub = services.get_subscription(request.user)
        return Response({**SubscriptionSerializer(sub).data, "usage": services.usage_summary(request.user)})

    def post(self, request):
        try:
            result = services.change_plan(request.user, str(request.data.get("plan", "")))
        except services.BillingError as exc:
            return Response({"detail": str(exc)}, status=400)
        sub = services.get_subscription(request.user)
        return Response({**SubscriptionSerializer(sub).data, "result": result})


class CancelView(APIView):
    def post(self, request):
        services.cancel(request.user)
        return Response(SubscriptionSerializer(services.get_subscription(request.user)).data)
