from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Device, User


@admin.register(User)
class CloudUserAdmin(UserAdmin):
    list_display = ("username", "email", "display_name", "organization", "public_id", "is_active", "date_joined")
    search_fields = ("username", "email", "display_name", "public_id")
    fieldsets = UserAdmin.fieldsets + (("P2P", {"fields": ("display_name", "organization", "public_id")}),)
    readonly_fields = ("public_id",)


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "app", "platform", "fingerprint", "last_seen_at", "revoked")
    list_filter = ("revoked", "app")
    search_fields = ("user__username", "fingerprint", "name")
