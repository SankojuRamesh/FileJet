"""REST API: accounts, profile, devices, signal token."""
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import services
from .models import Device
from .serializers import DeviceSerializer, LoginSerializer, MeSerializer, RegisterSerializer


def _error(exc: services.ServiceError) -> Response:
    return Response({"detail": str(exc)}, status=exc.status)


class ConfigView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"signaling_url": settings.P2P_SIGNALING_URL, "version": "2.0",
                         "web_url": request.build_absolute_uri("/")})


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "register"

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        refresh = LoginSerializer.get_token(user)
        return Response({"user": MeSerializer(user).data, "refresh": str(refresh),
                         "access": str(refresh.access_token)}, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    throttle_scope = "login"


class RefreshView(TokenRefreshView):
    throttle_scope = "login"


class LogoutView(APIView):
    def post(self, request):
        try:
            RefreshToken(request.data.get("refresh", "")).blacklist()
        except TokenError:
            pass
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(MeSerializer(request.user).data)

    def patch(self, request):
        ser = MeSerializer(request.user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class DevicesView(APIView):
    def get(self, request):
        return Response(DeviceSerializer(request.user.devices.all(), many=True).data)

    def post(self, request):
        try:
            d = services.register_device(request.user, str(request.data.get("cert_pem", "")),
                                         str(request.data.get("name", "")), str(request.data.get("platform", "")),
                                         str(request.data.get("app", "")))
        except services.ServiceError as exc:
            return _error(exc)
        return Response(DeviceSerializer(d).data, status=status.HTTP_201_CREATED)


class DeviceDetailView(APIView):
    def delete(self, request, pk):
        d = get_object_or_404(Device, pk=pk, user=request.user)
        d.revoked = True
        d.save(update_fields=["revoked"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class SignalTokenView(APIView):
    def post(self, request):
        try:
            return Response(services.issue_signal_token(request.user, str(request.data.get("fingerprint", ""))))
        except services.ServiceError as exc:
            return _error(exc)
