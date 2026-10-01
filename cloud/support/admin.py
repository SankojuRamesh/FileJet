from django.contrib import admin

from .models import Ticket, TicketMessage


class MessageInline(admin.TabularInline):
    model = TicketMessage
    extra = 0
    readonly_fields = ("author", "from_staff", "created_at")


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("number", "subject", "user", "category", "status", "priority", "assigned_to", "last_message_at")
    list_filter = ("status", "category", "priority")
    search_fields = ("subject", "user__username", "user__email")
    inlines = [MessageInline]
