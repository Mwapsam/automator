from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("auth/login/", auth_views.LoginView.as_view(template_name="auth/login.html"), name="login"),
    path("auth/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("auth/bitrix/", include("apps.bitrix.urls")),
    path("tenants/", include("apps.bitrix.tenant_urls")),
]
