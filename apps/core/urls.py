from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("customers/", views.customers, name="customers"),
    path("customers/<int:pk>/toggle/", views.customer_toggle, name="customer-toggle"),
    path("customers/<int:pk>/subscription/", views.customer_subscription, name="customer-subscription"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/users/<int:pk>/toggle-admin/", views.user_toggle_admin, name="user-toggle-admin"),
]
