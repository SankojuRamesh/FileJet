from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("features/", views.features, name="features"),
    path("pricing/", views.pricing, name="pricing"),
    path("download/", views.download, name="download"),
    path("download/<str:name>", views.download_file, name="download_file"),
    path("about/", views.about, name="about"),
]
