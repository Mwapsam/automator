from django.contrib import admin

from apps.bitrix.models import BitrixConnection


@admin.register(BitrixConnection)
class BitrixConnectionAdmin(admin.ModelAdmin):
    list_display = ("account", "domain", "is_active", "expires_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("account__company_name", "domain")
    readonly_fields = ("created_at",)
    raw_id_fields = ("account",)
