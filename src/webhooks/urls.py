from django.urls import path

from webhooks.views import MessageWebhookView

app_name = "webhooks"

urlpatterns = [
    path("message", MessageWebhookView.as_view(), name="message"),
]
