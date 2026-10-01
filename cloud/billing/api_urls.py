from django.urls import path

from . import api

urlpatterns = [
    path("plans/", api.PlansView.as_view()),
    path("subscription/", api.SubscriptionView.as_view()),
    path("subscription/cancel/", api.CancelView.as_view()),
]
