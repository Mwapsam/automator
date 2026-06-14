from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("plans/", views.pricing_page, name="plans"),
    path("checkout/", views.checkout, name="checkout"),
    path("callback/", views.callback, name="callback"),
    path("webhook/", views.webhook, name="webhook"),
]
