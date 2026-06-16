from django.urls import path

from apps.whatsapp import numbers
from apps.whatsapp.views import WhatsAppWebhookView

urlpatterns = [
    path("numbers/", numbers.numbers_list, name="whatsapp-numbers"),
    path("numbers/create/", numbers.numbers_create, name="whatsapp-numbers-create"),
    path("numbers/<int:pk>/delete/", numbers.numbers_delete, name="whatsapp-numbers-delete"),
    path("webhook/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
]
