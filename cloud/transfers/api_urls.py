from django.urls import path

from . import api

urlpatterns = [
    path("", api.TransferListView.as_view()),
    path("report/", api.ReportView.as_view()),
    path("authorize/", api.AuthorizeView.as_view()),
    path("stats/", api.StatsView.as_view()),
    path("<str:transfer_id>/", api.TransferDetailView.as_view()),
    path("<str:transfer_id>/attempts/", api.AttemptsView.as_view()),
]
