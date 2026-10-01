"""Template helpers: VS Code-style line icons and human readable sizes."""
import math

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def _gear() -> str:
    pts = []
    for i in range(16):
        a = math.pi * 2 * i / 16
        r = 9.5 if i % 2 == 0 else 7.2
        pts.append(f"{12 + r * math.cos(a):.2f},{12 + r * math.sin(a):.2f}")
    return f'<polygon points="{" ".join(pts)}"/><circle cx="12" cy="12" r="3"/>'


ICONS = {
    "dashboard": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/>'
                 '<rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "transfers": '<path d="M7 20V4M3 8l4-4 4 4M17 4v16M13 16l4 4 4-4"/>',
    "contacts": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/>'
                '<path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.6a6.5 6.5 0 0 1 3.5 5.4"/>',
    "devices": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
    "subscription": '<rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M2.5 10h19M6 15h4"/>',
    "account": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "settings": _gear(),
    "logout": '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H3"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
    "download": '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.8-4.8"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "folder": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="1.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="1.5"/><path d="M3.5 6l8.5 7 8.5-7"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.6a6.5 6.5 0 0 1 3.5 5.4"/>',
    "support": '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4 3.5V16h-.5A1.5 1.5 0 0 1 4 14.5z"/><path d="M9.5 8.3a2.5 2.5 0 1 1 3 2.4c-.5.2-.5.6-.5 1.1M12 13.6v.1"/>',
    "staff": '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><circle cx="12" cy="10" r="2.3"/><path d="M8.3 16a4 4 0 0 1 7.4 0"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/>',
}


@register.simple_tag
def icon(name, size=24, cls=""):
    body = ICONS.get(name, "")
    return mark_safe(f'<svg class="icon {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
                     f'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" '
                     f'aria-hidden="true">{body}</svg>')


@register.filter
def filesize(n):
    if n is None:
        return "Unlimited"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1000 or unit == "PB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
