from django.urls import path

from . import views

urlpatterns = [
    path("subscription/", views.subscription, name="subscription"),
    path("subscription/paid/", views.checkout_done, name="checkout_done"),
    path("billing/razorpay/webhook/", views.razorpay_webhook, name="razorpay_webhook"),
]
