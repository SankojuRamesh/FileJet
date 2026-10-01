from django.contrib import admin

from .models import ClientLink, Folder, FolderMember, OutboundEmail


class MemberInline(admin.TabularInline):
    model = FolderMember
    extra = 0


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("folder_id", "name", "owner", "created_at")
    search_fields = ("folder_id", "name", "owner__username")
    inlines = [MemberInline]


@admin.register(ClientLink)
class ClientLinkAdmin(admin.ModelAdmin):
    list_display = ("admin", "client", "note", "created_at")


@admin.register(OutboundEmail)
class OutboundEmailAdmin(admin.ModelAdmin):
    list_display = ("to", "subject", "folder_id", "delivered", "created_at")
    readonly_fields = ("to", "subject", "body", "kind", "folder_id", "delivered", "error", "created_at")
