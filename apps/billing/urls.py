from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("plans/", views.pricing_page, name="plans"),
    path("checkout/", views.checkout, name="checkout"),
    path("callback/", views.callback, name="callback"),
    path("webhook/", views.webhook, name="webhook"),
    # Admin package management
    path("plans/create/", views.plan_create, name="plan-create"),
    path("plans/<int:pk>/edit/", views.plan_edit, name="plan-edit"),
    path("plans/<int:pk>/toggle/", views.plan_toggle, name="plan-toggle"),
    path("plans/<int:pk>/delete/", views.plan_delete, name="plan-delete"),
]
