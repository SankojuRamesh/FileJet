from django.urls import path

from . import api, views

urlpatterns = [
    path("folders/", views.folders, name="folders"),
    path("users/", views.users, name="users"),
    path("inbox/", views.inbox, name="inbox"),
    path("permissions/", views.folders, name="permissions"),       # old link
]

api_urlpatterns = [
    path("", api.OverviewView.as_view()),
    path("create/", api.FoldersView.as_view()),
    path("join/", api.JoinView.as_view()),
    path("decline/", api.DeclineView.as_view()),
    path("clients/", api.ClientsView.as_view()),
    path("clients/<str:public_id>/", api.ClientDetailView.as_view()),
    path("connections/", api.ConnectionsView.as_view()),
    path("groups/", api.GroupsView.as_view()),
    path("groups/<int:pk>/", api.GroupDetailView.as_view()),
    path("<str:folder_id>/", api.FolderDetailView.as_view()),
    path("<str:folder_id>/members/", api.MembersView.as_view()),
    path("<str:folder_id>/members/<int:pk>/", api.MemberDetailView.as_view()),
    path("<str:folder_id>/members/<int:pk>/resend/", api.ResendView.as_view()),
    path("<str:folder_id>/groups/", api.FolderGroupView.as_view()),
    path("<str:folder_id>/activity/", api.ActivityView.as_view()),
    path("<str:folder_id>/files/", api.FolderFilesView.as_view()),
]
