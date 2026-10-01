from django.contrib import admin
from django.urls import include, path

from sharing.urls import api_urlpatterns as sharing_api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("accounts.api_urls")),
    path("api/billing/", include("billing.api_urls")),
    path("api/transfers/", include("transfers.api_urls")),
    path("api/folders/", include(sharing_api)),
    path("", include("website.urls")),
    path("", include("accounts.urls")),
    path("", include("billing.urls")),
    path("", include("transfers.urls")),
    path("", include("sharing.urls")),
]
