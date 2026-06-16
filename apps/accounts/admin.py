from django.contrib import admin

from apps.accounts.models import Account, Membership


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("company_name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("company_name", "slug")
    prepopulated_fields = {"slug": ("company_name",)}
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "account", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("user__username", "user__email", "account__company_name")
    raw_id_fields = ("user", "account")
