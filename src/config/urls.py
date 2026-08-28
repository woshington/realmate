from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("webhook/", include("webhooks.urls")),
    path("api/", include("conversations.urls")),
]
