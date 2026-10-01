"""REST API: clients (added by ID), folders, members/permissions, joining by folder ID."""
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.serializers import PeerSerializer
from accounts.services import ServiceError

from . import services
from .models import ActivityEvent, ClientLink, FolderMember, Group


def _err(exc: ServiceError) -> Response:
    return Response({"detail": str(exc)}, status=exc.status)


class OverviewView(APIView):
    def get(self, request):
        return Response(services.overview(request.user))


class ClientsView(APIView):
    def post(self, request):
        try:
            link = services.add_client(request.user, str(request.data.get("query", "")),
                                       str(request.data.get("note", "")))
        except ServiceError as exc:
            return _err(exc)
        return Response(dict(services.user_json(link.client), note=link.note), status=201)


class ClientDetailView(APIView):
    def delete(self, request, public_id):
        services.remove_client(request.user, get_object_or_404(User, public_id=public_id))
        return Response(status=204)


class ConnectionsView(APIView):
    """Admins who added me + clients I added, with their device certificates (for pinning / E2E)."""

    def get(self, request):
        return Response(PeerSerializer(services.connections(request.user), many=True).data)


class FoldersView(APIView):
    def post(self, request):
        try:
            f = services.create_folder(request.user, str(request.data.get("name", "")),
                                       str(request.data.get("device_fingerprint", "")), request.data)
        except ServiceError as exc:
            return _err(exc)
        return Response(services.folder_json(f), status=201)


class FolderDetailView(APIView):
    def get(self, request, folder_id):
        try:
            return Response(services.folder_json(services.owned_folder(request.user, folder_id)))
        except ServiceError as exc:
            return _err(exc)

    def patch(self, request, folder_id):
        try:
            f = services.owned_folder(request.user, folder_id)
        except ServiceError as exc:
            return _err(exc)
        services.apply_folder_settings(f, request.data)
        f.save()
        return Response(services.folder_json(f))

    def delete(self, request, folder_id):
        try:
            services.owned_folder(request.user, folder_id).delete()
        except ServiceError as exc:
            return _err(exc)
        return Response(status=204)


class MembersView(APIView):
    def post(self, request, folder_id):
        try:
            f = services.owned_folder(request.user, folder_id)
            m, created = services.set_member(request.user, f, str(request.data.get("query", "")), request.data,
                                             request)
        except ServiceError as exc:
            return _err(exc)
        return Response(dict(services.member_json(m), created=created), status=201 if created else 200)


class MemberDetailView(APIView):
    def _member(self, request, folder_id, pk):
        m = get_object_or_404(FolderMember.objects.select_related("folder", "user"), pk=pk,
                              folder__folder_id=folder_id)
        if m.folder.owner_id != request.user.id:
            raise ServiceError("not your folder", 403)
        return m

    def patch(self, request, folder_id, pk):
        try:
            m = services.update_member(request.user, self._member(request, folder_id, pk), request.data)
        except ServiceError as exc:
            return _err(exc)
        return Response(services.member_json(m))

    def delete(self, request, folder_id, pk):
        try:
            self._member(request, folder_id, pk).delete()
        except ServiceError as exc:
            return _err(exc)
        return Response(status=204)


class ResendView(APIView):
    def post(self, request, folder_id, pk):
        try:
            m = MemberDetailView()._member(request, folder_id, pk)
        except ServiceError as exc:
            return _err(exc)
        mail = services.send_invitation(m, request)
        return Response({"sent": mail.delivered, "error": mail.error})


class JoinView(APIView):
    def post(self, request):
        try:
            m = services.join_folder(request.user, str(request.data.get("folder_id", "")))
        except ServiceError as exc:
            return _err(exc)
        return Response(dict(services.folder_json(m.folder, members=False), role=m.role, perms=m.perms(),
                             status=m.status))


class GroupsView(APIView):
    def post(self, request):
        name = str(request.data.get("name", "")).strip()[:80]
        if not name:
            return Response({"detail": "group name required"}, status=400)
        g, _ = Group.objects.get_or_create(admin=request.user, name=name)
        return Response({"id": g.id, "name": g.name, "members": []}, status=201)


class GroupDetailView(APIView):
    def patch(self, request, pk):
        g = get_object_or_404(Group, pk=pk, admin=request.user)
        if request.data.get("name"):
            g.name = str(request.data["name"])[:80]
            g.save()
        clients = set(ClientLink.objects.filter(admin=request.user).values_list("client__public_id", flat=True))
        for pid in request.data.get("add") or []:
            if str(pid) not in clients:
                return Response({"detail": "add the user to your Users list first"}, status=400)
            g.members.add(User.objects.get(public_id=str(pid)))
        for pid in request.data.get("remove") or []:
            u = User.objects.filter(public_id=str(pid)).first()
            if u:
                g.members.remove(u)
        return Response({"id": g.id, "name": g.name, "members": [services.user_json(u) for u in g.members.all()]})

    def delete(self, request, pk):
        get_object_or_404(Group, pk=pk, admin=request.user).delete()
        return Response(status=204)


class FolderGroupView(APIView):
    """Give every member of a group access to a folder (one member row each)."""

    def post(self, request, folder_id):
        try:
            f = services.owned_folder(request.user, folder_id)
            g = get_object_or_404(Group, pk=int(request.data.get("group_id", 0)), admin=request.user)
            n = services.add_group_to_folder(request.user, f, g, request.data, request)
        except ServiceError as exc:
            return _err(exc)
        return Response({"added": n, "folder": services.folder_json(f)})


class ActivityView(APIView):
    """Folder audit log. The owner's app reports actions it performed for members (metadata only)."""

    def get(self, request, folder_id):
        try:
            f = services.owned_folder(request.user, folder_id)
        except ServiceError as exc:
            return _err(exc)
        return Response([{"action": e.action, "path": e.path, "detail": e.detail, "created_at": e.created_at,
                          "actor": services.user_json(e.actor) if e.actor else None}
                         for e in ActivityEvent.objects.filter(folder=f).select_related("actor")[:200]])

    def post(self, request, folder_id):
        try:
            f = services.owned_folder(request.user, folder_id)
        except ServiceError as exc:
            return _err(exc)
        actor = User.objects.filter(public_id=str(request.data.get("actor", ""))).first()
        e = services.log_event(f, request.user, str(request.data.get("action", "")), request.data.get("path", ""),
                               request.data.get("detail", ""), actor=actor)
        return Response({"id": e.id}, status=201)


class FolderFilesView(APIView):
    """Files sent to a folder (metadata only). The admin sees all; a user sees the files they sent."""

    def get(self, request, folder_id):
        from transfers.models import TransferRecord
        from transfers.serializers import TransferSerializer
        from .models import Folder, normalize_folder_id
        fid = normalize_folder_id(folder_id)
        folder = Folder.objects.filter(folder_id=fid).first()
        if folder is None:
            return Response({"detail": "folder not found"}, status=404)
        qs = TransferRecord.objects.filter(folder_id=fid).select_related("sender", "receiver")
        if folder.owner_id != request.user.id:
            if not FolderMember.objects.filter(folder=folder, user=request.user).exists():
                return Response({"detail": "folder not found"}, status=404)
            qs = qs.filter(sender=request.user)
        stats = services.folder_stats([fid], sender=None if folder.owner_id == request.user.id else request.user)
        limit = min(int(request.GET.get("limit", 200) or 200), 1000)
        return Response({"folder_id": fid, "name": folder.name, "stats": stats.get(fid, services.EMPTY_STATS),
                         "files": TransferSerializer(qs[:limit], many=True, context={"user": request.user}).data})
