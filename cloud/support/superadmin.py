"""The platform admin (superuser) only administers the platform: no workspaces, sharing, transfers or
subscription of their own - neither in the web dashboard nor through the app's API."""
from django.shortcuts import redirect
from rest_framework.permissions import BasePermission, IsAuthenticated

# web dashboard pages for customers; the platform admin is sent to the staff console instead
CUSTOMER_PAGES = ("/dashboard/", "/transfers/", "/folders/", "/permissions/", "/users/", "/inbox/", "/devices/",
                  "/subscription/", "/support/")
# app API areas for sharing and sending files
SHARING_API = ("/api/folders/", "/api/transfers/")


class SuperadminOnlyAdminMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_superuser \
                and request.path.startswith(CUSTOMER_PAGES):
            return redirect("staff_orgs")
        return self.get_response(request)


class NoSharingForSuperadmin(BasePermission):
    """API default: signed in - and a platform admin cannot share folders or send files."""
    message = "the platform admin account cannot share folders or send files - use a normal user account"

    def has_permission(self, request, view):
        if not IsAuthenticated().has_permission(request, view):
            return False
        return not (request.user.is_superuser and request.path.startswith(SHARING_API))
