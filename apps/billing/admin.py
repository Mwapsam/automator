from django.contrib import admin

from .models import Plan, Subscription, UsageSummary


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = [
        "name", "slug", "price_monthly",
        "max_conversations_per_month", "max_automation_rules",
        "max_whatsapp_numbers", "trial_days", "has_priority_support", "is_active",
    ]
    list_filter = ["is_active", "has_priority_support"]
    search_fields = ["name", "slug"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "bitrix_account", "plan", "status",
        "trial_ends_at", "current_period_start", "current_period_end",
        "fw_subscription_id", "created_at",
    ]
    list_filter = ["status", "plan"]
    search_fields = ["bitrix_account__company_name", "bitrix_account__domain", "fw_customer_email"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["bitrix_account"]


@admin.register(UsageSummary)
class UsageSummaryAdmin(admin.ModelAdmin):
    list_display = ["bitrix_account", "period_start", "conversations_used"]
    list_filter = ["period_start"]
    search_fields = ["bitrix_account__company_name"]
    raw_id_fields = ["bitrix_account"]
