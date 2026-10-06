from django.urls import path

from . import views

urlpatterns = [
    path("support/", views.support_home, name="support"),
    path("support/<int:pk>/", views.support_ticket, name="support_ticket"),
    path("staff/", views.staff_overview, name="staff"),
    path("staff/users/", views.staff_users, name="staff_users"),
    path("staff/users/<int:pk>/", views.staff_user, name="staff_user"),
    path("staff/relations/", views.staff_relations, name="staff_relations"),
    path("staff/organizations/", views.staff_orgs, name="staff_orgs"),
    path("staff/billing/", views.staff_billing, name="staff_billing"),
    path("staff/tickets/", views.staff_tickets, name="staff_tickets"),
    path("staff/tickets/<int:pk>/", views.staff_ticket, name="staff_ticket"),
    path("staff/employees/", views.staff_employees, name="staff_employees"),
]
