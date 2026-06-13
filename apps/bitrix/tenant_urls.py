from django.urls import path

from . import tenant_views

urlpatterns = [
    path("", tenant_views.tenants_page, name="tenants"),
    path("api/", tenant_views.tenants_api, name="tenants-api"),
    path("api/<int:pk>/", tenant_views.tenant_detail_api, name="tenant-detail-api"),
    path("api/<int:pk>/numbers/", tenant_views.tenant_numbers_api, name="tenant-numbers-api"),
    path("api/<int:pk>/numbers/<int:num_pk>/", tenant_views.tenant_number_detail_api, name="tenant-number-detail-api"),
]
