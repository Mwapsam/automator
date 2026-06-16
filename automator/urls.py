from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("auth/login/", auth_views.LoginView.as_view(template_name="auth/login.html"), name="login"),
    path("auth/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("apps.accounts.urls")),
    path("whatsapp/", include("apps.whatsapp.urls")),
    path("email/", include("apps.email.urls")),
    path("auth/bitrix/", include("apps.bitrix.urls")),
    path("billing/", include("apps.billing.urls", namespace="billing")),
]
