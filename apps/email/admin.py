from django.contrib import admin

from apps.email.models import EmailApiKey, EmailDomain, EmailMessage


@admin.register(EmailDomain)
class EmailDomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "account", "status", "dkim_ok", "spf_ok", "created_at")
    list_filter = ("status", "dkim_ok", "spf_ok")
    search_fields = ("domain", "account__company_name")
    raw_id_fields = ("account",)
    readonly_fields = ("created_at", "verified_at")


@admin.register(EmailApiKey)
class EmailApiKeyAdmin(admin.ModelAdmin):
    list_display = ("account", "name", "is_active", "created_at", "last_used_at")
    list_filter = ("is_active",)
    search_fields = ("account__company_name", "key")
    raw_id_fields = ("account",)
    readonly_fields = ("key", "created_at", "last_used_at")


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("to_email", "from_email", "account", "status", "created_at", "sent_at")
    list_filter = ("status",)
    search_fields = ("to_email", "from_email", "subject", "account__company_name")
    raw_id_fields = ("account", "domain")
    readonly_fields = ("created_at", "sent_at", "provider_message_id", "error")
