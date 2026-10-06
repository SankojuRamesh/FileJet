"""Who is connected to whom - for the platform admin's Relations page (metadata only, never file contents).

A user can be an *admin* (added other users by ID, owns workspaces and shared folders, has groups) and/or a
*member* (was added by someone, has folders shared with them)."""
from __future__ import annotations

from collections import defaultdict

from django.contrib.auth import get_user_model
from django.db.models import Q

from sharing.models import ClientLink, Folder, FolderMember, Group

User = get_user_model()


def _workspaces(folders: list) -> list:
    """Workspaces (folders not inside another of the same owner), each with its shared folders as .subs."""
    ids = {f.pk for f in folders}
    tops = [f for f in folders if f.parent_id not in ids]
    for w in tops:
        w.subs = sorted((f for f in folders if f.parent_id == w.pk), key=lambda f: (f.subpath or f.name).lower())
    return sorted(tops, key=lambda f: f.name.lower())


def build(q: str = "", only_connected: bool = False, user_ids=None) -> dict:
    """Users with everything they are connected to, plus the admin -> user connections and totals."""
    users = User.objects.filter(is_staff=False, is_superuser=False).select_related("subscription__plan")
    if user_ids is not None:
        users = users.filter(pk__in=list(user_ids))
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(display_name__icontains=q)
                             | Q(public_id__icontains=q.replace(" ", "")) | Q(organization__icontains=q)
                             | Q(location__icontains=q))
    users = list(users.order_by("display_name", "username")[:500])
    pks = [u.pk for u in users]

    links = list(ClientLink.objects.filter(Q(admin_id__in=pks) | Q(client_id__in=pks)).select_related("admin", "client"))
    folders = list(Folder.objects.filter(owner_id__in=pks).select_related("owner", "parent")
                   .prefetch_related("members__user"))
    memberships = list(FolderMember.objects.filter(user_id__in=pks).select_related("folder__owner", "folder__parent"))
    groups = list(Group.objects.filter(admin_id__in=pks).prefetch_related("members"))

    added, added_by = defaultdict(list), defaultdict(list)
    for link in links:
        added[link.admin_id].append(link)
        added_by[link.client_id].append(link)
    owned = defaultdict(list)
    for f in folders:
        owned[f.owner_id].append(f)
    member_of = defaultdict(list)
    for m in memberships:
        member_of[m.user_id].append(m)
    groups_of = defaultdict(list)
    for g in groups:
        groups_of[g.admin_id].append(g)

    rows = []
    for u in users:
        ws = _workspaces(owned[u.pk])
        r = {"u": u, "added": sorted(added[u.pk], key=lambda link: link.client.label.lower()),
             "added_by": sorted(added_by[u.pk], key=lambda link: link.admin.label.lower()),
             "workspaces": ws, "shared_n": sum(len(w.subs) for w in ws),
             "member_of": sorted(member_of[u.pk], key=lambda m: (m.folder.owner.label.lower(), m.folder.name.lower())),
             "groups": groups_of[u.pk], "plan": getattr(getattr(u, "subscription", None), "plan", None)}
        r["connections"] = len(r["added"]) + len(r["added_by"]) + len(r["member_of"])
        r["is_admin"] = bool(r["added"] or ws or r["groups"])
        if only_connected and not (r["connections"] or ws):
            continue
        rows.append(r)
    rows.sort(key=lambda r: (-r["connections"], r["u"].label.lower()))

    # admin -> user pairs with the folders the admin shared with that user
    shared_pairs = defaultdict(list)
    for m in memberships:
        shared_pairs[(m.folder.owner_id, m.user_id)].append(m)
    pairs = [{"link": link, "folders": sorted(shared_pairs.get((link.admin_id, link.client_id), []),
                                              key=lambda m: m.folder.name.lower())}
             for link in sorted(links, key=lambda link: (link.admin.label.lower(), link.client.label.lower()))]

    totals = {"users": len(rows), "admins": sum(1 for r in rows if r["is_admin"]),
              "links": len(links), "workspaces": sum(len(r["workspaces"]) for r in rows),
              "shared": sum(r["shared_n"] for r in rows), "memberships": len(memberships),
              "groups": len(groups)}
    return {"rows": rows, "pairs": pairs, "totals": totals}
