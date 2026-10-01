"""Settings for the MediaRush cloud app (accounts, users, folders & permissions, transfer metadata).

The cloud app never receives file contents. It stores account data and *metadata* about
transfers (name, size, time, sender, receiver, status, speed) reported by the desktop apps.

Configuration comes from environment variables (see cloud/.env.example).
"""
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-secret-key-change-me")
if not DEBUG and SECRET_KEY.startswith("dev-insecure"):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY in production")

# Public domain of this deployment. Production defaults below are derived from it; every value can still be
# overridden by its own environment variable. Development (DJANGO_DEBUG=1) keeps using 127.0.0.1.
DOMAIN = os.environ.get("P2P_DOMAIN", "iotgateway.live")
# Served over HTTPS? Set DJANGO_HTTPS=0 only while the site has no certificate yet (or for a plain-HTTP office
# network): web sign-in cookies must then be allowed over HTTP.
HTTPS = os.environ.get("DJANGO_HTTPS", "0" if DEBUG else "1") == "1"
_SCHEME, _WS = ("https", "wss") if HTTPS else ("http", "ws")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS",
                         f"{DOMAIN},www.{DOMAIN},13.204.80.52,localhost,127.0.0.1,testserver,*")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS",
                                "" if DEBUG else f"https://{DOMAIN},https://www.{DOMAIN},http://{DOMAIN}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "accounts",
    "billing",
    "transfers",
    "sharing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "sharing.context.ui",
    ]},
}]


def _database():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3",
                "NAME": os.environ.get("DJANGO_SQLITE_PATH", str(BASE_DIR / "db.sqlite3"))}
    u = urlparse(url)
    if u.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured("DATABASE_URL must be postgres://user:pass@host:port/db")
    return {"ENGINE": "django.db.backends.postgresql", "NAME": u.path.lstrip("/"),
            "USER": unquote(u.username or ""), "PASSWORD": unquote(u.password or ""),
            "HOST": u.hostname or "", "PORT": str(u.port or 5432), "CONN_MAX_AGE": 60}


DATABASES = {"default": _database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.EmailOrUsernameBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage" if not DEBUG
                    else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("THROTTLE_ANON", "120/min"),
        "user": os.environ.get("THROTTLE_USER", "1200/min"),
        "login": os.environ.get("THROTTLE_LOGIN", "10/min"),
        "register": os.environ.get("THROTTLE_REGISTER", "5/min"),
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

# Shared with the signaling server (P2P_CLOUD_JWT_SECRET there). Signal tokens let a *registered
# device* of a logged-in user use signaling/presence for a limited time (no file data goes via servers).
SIGNAL_JWT_SECRET = os.environ.get("P2P_CLOUD_JWT_SECRET", "dev-signal-secret-change-me-0123456789abcdef")
if not DEBUG and SIGNAL_JWT_SECRET.startswith("dev-"):
    raise ImproperlyConfigured("Set P2P_CLOUD_JWT_SECRET in production")
SIGNAL_TOKEN_TTL = int(os.environ.get("P2P_SIGNAL_TOKEN_TTL", 3600))
# Where the desktop apps find the signaling server (they ask GET /api/config/).
P2P_SIGNALING_URL = os.environ.get("P2P_SIGNALING_URL",
                                   "ws://127.0.0.1:8765/ws" if DEBUG else f"{_WS}://{DOMAIN}/ws")
BILLING_PROVIDER = os.environ.get("BILLING_PROVIDER", "dummy")
CLOUD_PUBLIC_URL = os.environ.get("CLOUD_PUBLIC_URL", "http://127.0.0.1:8000/" if DEBUG else f"{_SCHEME}://{DOMAIN}/")

# E-mail (folder invitations). Development: printed to the console AND visible under "Inbox" in the
# web app. Production: set EMAIL_HOST etc. (SMTP) and SHOW_EMAIL_OUTBOX=0.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend" if DEBUG
                               else "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", f"MediaRush <no-reply@{DOMAIN}>")
SHOW_EMAIL_OUTBOX = os.environ.get("SHOW_EMAIL_OUTBOX", "1" if DEBUG else "0") == "1"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = HTTPS
    CSRF_COOKIE_SECURE = HTTPS
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", 31536000)) if HTTPS else 0
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
