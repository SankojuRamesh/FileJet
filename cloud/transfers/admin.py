from django.contrib import admin

from .models import TransferRecord


@admin.register(TransferRecord)
class TransferRecordAdmin(admin.ModelAdmin):
    list_display = ("file_name", "file_size", "sender", "receiver", "status", "connection_type", "started_at",
                    "completed_at")
    list_filter = ("status", "direction", "connection_type")
    search_fields = ("file_name", "transfer_id", "sender__username", "receiver__username")
    readonly_fields = [f.name for f in TransferRecord._meta.fields]
