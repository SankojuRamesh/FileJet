from django.urls import path

from . import api

urlpatterns = [
    path("tickets/", api.TicketsView.as_view()),
    path("tickets/<int:pk>/", api.TicketDetailView.as_view()),
    path("tickets/<int:pk>/messages/", api.TicketMessagesView.as_view()),
    path("tickets/<int:pk>/close/", api.TicketCloseView.as_view()),
    path("unread/", api.UnreadView.as_view()),
]
