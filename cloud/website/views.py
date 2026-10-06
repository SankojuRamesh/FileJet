"""Public website: home (landing page), features, pricing, download, about.

Prices come from the plans in the database (billing.Plan), so the website and the app's subscription
page always agree. Downloads are served from DOWNLOADS_DIR (put FileJet.exe / the .deb there).
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import render

from billing.models import Plan

GB, TB = 10 ** 9, 10 ** 12

# files offered on the download page: (file name pattern, platform, label)
DOWNLOADS = [
    ("FileJet.exe", "windows", "Windows 10 / 11", "Installer-free app – just run it"),
    ("filejet_*_amd64.deb", "linux", "Ubuntu / Debian (.deb)", "sudo apt install ./filejet_…deb"),
    ("FileJet-linux-x86_64.tar.gz", "linux", "Linux (.tar.gz)", "Any x86-64 Linux desktop"),
]


def _size(n) -> str:
    if not n:
        return "Unlimited"
    if n >= TB:
        return f"{n / TB:g} TB"
    return f"{n / GB:g} GB"


def _plans() -> list[dict]:
    out = []
    for p in Plan.objects.filter(public=True).order_by("sort", "price_month"):
        out.append({
            "code": p.code, "name": p.name, "price": p.price_month, "currency": p.currency,
            "description": p.description,
            "highlight": p.code == "pro",
            "rows": [
                ("File size", _size(p.max_file_size) if p.max_file_size else "Unlimited"),
                ("Transfer volume / month", _size(p.monthly_quota) if p.monthly_quota else "Unlimited"),
                ("Users", f"{p.max_users}" if p.max_users else "Unlimited"),
                ("Shared folders", f"{p.max_folders}" if p.max_folders else "Unlimited"),
            ],
        })
    return out


def _downloads() -> list[dict]:
    folder = Path(settings.DOWNLOADS_DIR)
    items = []
    for pattern, platform, label, hint in DOWNLOADS:
        files = sorted(folder.glob(pattern)) if folder.is_dir() else []
        f = files[-1] if files else None
        items.append({"platform": platform, "label": label, "hint": hint, "name": f.name if f else None,
                      "size": f"{f.stat().st_size / 10 ** 6:.0f} MB" if f else None})
    return items


def _app_version() -> str:
    """Version of the desktop app (pyproject.toml next to the cloud folder), e.g. "2.8.0"."""
    import re
    try:
        text = (Path(settings.BASE_DIR).parent / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else ""


def _ctx(page: str, **extra) -> dict:
    return {"page": page, "contact_email": settings.SITE_CONTACT_EMAIL, "company": settings.SITE_COMPANY, **extra}


def home(request):
    return render(request, "website/home.html", _ctx("home", plans=_plans()))


def features(request):
    return render(request, "website/features.html", _ctx("features"))


def pricing(request):
    return render(request, "website/pricing.html", _ctx("pricing", plans=_plans()))


def download(request):
    return render(request, "website/download.html", _ctx("download", downloads=_downloads(), version=_app_version()))


def download_file(request, name: str):
    folder = Path(settings.DOWNLOADS_DIR).resolve()
    allowed = {d["name"] for d in _downloads() if d["name"]}
    path = (folder / name).resolve()
    if name not in allowed or path.parent != folder or not path.is_file():
        raise Http404("not available")
    return FileResponse(path.open("rb"), as_attachment=True, filename=name)


def about(request):
    return render(request, "website/about.html", _ctx("about"))
