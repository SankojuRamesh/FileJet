from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render

from accounts.services import ServiceError
from transfers.services import with_attempts

from . import services
from .models import ClientLink, Folder, FolderMember, OutboundEmail


@login_required
def folders(request):
    from transfers.models import TransferRecord
    if request.method == "POST":
        fid, action = request.POST.get("folder_id", ""), request.POST.get("action")
        try:
            if action == "accept":
                m = services.join_folder(request.user, fid)
                messages.success(request, f"You joined '{m.folder.name}'. Open it in the FileJet app to send and receive files.")
            elif action in ("decline", "leave"):
                services.decline_folder(request.user, fid)
                messages.success(request, ("Invitation declined." if action == "decline" else "You left the folder.")
                                 + " You can accept it again any time under Folders shared with me.")
        except ServiceError as exc:
            messages.error(request, str(exc))
        return redirect("dashboard" if request.POST.get("next") == "dashboard" else "folders")
    owned = list(Folder.objects.filter(owner=request.user).prefetch_related("members__user"))
    mine = list(FolderMember.objects.filter(user=request.user).select_related("folder__owner"))
    stats = services.folder_stats([f.folder_id for f in owned])
    my_stats = services.folder_stats([m.folder.folder_id for m in mine], sender=request.user)
    for f in owned:
        f.stats = stats.get(f.folder_id, services.EMPTY_STATS)
        f.recent_files = with_attempts(TransferRecord.objects.filter(folder_id=f.folder_id)).select_related("sender")[:15]
    for m in mine:
        m.stats = my_stats.get(m.folder.folder_id, services.EMPTY_STATS)
    sent = with_attempts(TransferRecord.objects.filter(sender=request.user, direction="upload",
                                         folder_id__in=[m.folder.folder_id for m in mine]))[:30]
    names = {m.folder.folder_id: m.folder.name for m in mine}
    for t in sent:
        t.folder_name = names.get(t.folder_id, t.share_name)
    return render(request, "cloud/folders.html", {"owned": owned, "mine": mine, "sent": sent, "active": "folders"})


@login_required
def users(request):
    if request.method == "POST":
        try:
            if request.POST.get("remove"):
                from accounts.models import User
                services.remove_client(request.user, User.objects.get(public_id=request.POST["remove"]))
                messages.success(request, "User removed from your list and from all your folders.")
            else:
                link = services.add_client(request.user, request.POST.get("query", ""), request.POST.get("note", ""))
                messages.success(request, f"{link.client.label} added.")
        except (ServiceError, Exception) as exc:
            messages.error(request, str(exc))
        return redirect("users")
    return render(request, "cloud/users.html", {
        "clients": ClientLink.objects.filter(admin=request.user).select_related("client"),
        "admins": ClientLink.objects.filter(client=request.user).select_related("admin"),
        "active": "users"})


@login_required
def inbox(request):
    """Development helper: the e-mails the cloud sent to you (enable with SHOW_EMAIL_OUTBOX)."""
    if not settings.SHOW_EMAIL_OUTBOX:
        raise Http404
    mails = OutboundEmail.objects.filter(to__iexact=request.user.email)[:50]
    return render(request, "cloud/inbox.html", {"mails": mails, "active": "inbox"})
