from django.contrib import admin

from .models import Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price_month", "max_file_size", "monthly_quota", "max_users", "max_folders")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "current_period_end", "cancel_at_period_end", "provider")
    list_filter = ("plan", "status")
    search_fields = ("user__username", "user__email")
