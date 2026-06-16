from django.urls import path

from apps.email import views

urlpatterns = [
    path("domains/", views.domains_list, name="email-domains"),
    path("domains/create/", views.domain_create, name="email-domain-create"),
    path("domains/<int:pk>/verify/", views.domain_verify, name="email-domain-verify"),
    path("keys/create/", views.key_create, name="email-key-create"),
    path("send/", views.api_send, name="email-send"),
]
