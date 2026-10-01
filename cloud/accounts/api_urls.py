from django.urls import path

from . import api

urlpatterns = [
    path("config/", api.ConfigView.as_view()),
    path("auth/register/", api.RegisterView.as_view()),
    path("auth/login/", api.LoginView.as_view()),
    path("auth/refresh/", api.RefreshView.as_view()),
    path("auth/logout/", api.LogoutView.as_view()),
    path("me/", api.MeView.as_view()),
    path("devices/", api.DevicesView.as_view()),
    path("devices/<int:pk>/", api.DeviceDetailView.as_view()),
    path("signal-token/", api.SignalTokenView.as_view()),
]
