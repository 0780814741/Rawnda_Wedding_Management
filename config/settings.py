"""
Django settings for the Rwanda Wedding Marketplace.

All secrets and environment-specific values come from environment variables.
See .env.example for the full list. Nothing sensitive is hardcoded here.
"""
import logging
import os
import sys
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader for local development (real environment variables always win)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:  # empty placeholders (e.g. DATABASE_URL=) must not override built-in defaults
            os.environ.setdefault(key.strip(), value)


_load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


DEBUG = env_bool("DEBUG", False)
TESTING = "test" in sys.argv

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "insecure-dev-only-key-do-not-use-in-production"
    else:
        raise RuntimeError("SECRET_KEY environment variable is required when DEBUG is off.")

# --- Hosts / origins ---------------------------------------------------------
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")  # set automatically by Render
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")
if not DEBUG and not ALLOWED_HOSTS and not TESTING:
    raise RuntimeError("Set ALLOWED_HOSTS (or deploy on Render, which sets RENDER_EXTERNAL_HOSTNAME).")

# --- Apps --------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "storages",
    "apps.core",
    "apps.accounts",
    "apps.vendors",
    "apps.planning",
    "apps.operations",
    "apps.payments",
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
    "apps.core.middleware.RequestContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
        },
    },
]

# --- Database ----------------------------------------------------------------
# Render injects DATABASE_URL. Locally you may use PostgreSQL or fall back to SQLite
# (SQLite is for quick local checks and tests only, not production).
_default_db = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL") or _default_db,
        conn_max_age=600,
        conn_health_checks=True,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# DB-backed cache so rate limits are shared between gunicorn workers.
# Create the table with: python manage.py createcachetable (done in build/pre-deploy).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache_table",
    }
}

# --- Auth --------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "planning:dashboard"
LOGOUT_REDIRECT_URL = "core:home"

# --- i18n --------------------------------------------------------------------
LANGUAGE_CODE = "en"
TIME_ZONE = "Africa/Kigali"
USE_I18N = True
USE_TZ = True

# --- Static & media ----------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if (DEBUG or TESTING)
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Persistent object storage (any S3-compatible service: Cloudflare R2, Backblaze B2, AWS S3...).
# Render's filesystem is EPHEMERAL: files written there disappear on redeploy/restart.
AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME", "")
MEDIA_STORAGE_CONFIGURED = bool(AWS_STORAGE_BUCKET_NAME)
if MEDIA_STORAGE_CONFIGURED:
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL") or None
    AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME") or None
    AWS_S3_CUSTOM_DOMAIN = os.environ.get("AWS_S3_CUSTOM_DOMAIN") or None
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False  # portfolio images are public by design; keep the bucket private-write
    AWS_S3_FILE_OVERWRITE = False
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}

# Uploads are accepted when storage is persistent, or in local development.
MEDIA_UPLOADS_ENABLED = MEDIA_STORAGE_CONFIGURED or DEBUG or TESTING
MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_BYTES", 5 * 1024 * 1024)
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --- Business configuration (money is integer RWF; rates are basis points) ----
# 100 bps = 1%. These are OPERATOR-SET business parameters, not market data.
PLATFORM_SERVICE_FEE_BPS = env_int("PLATFORM_SERVICE_FEE_BPS", 500)     # disclosed to customers
VENDOR_COMMISSION_BPS = env_int("VENDOR_COMMISSION_BPS", 0)            # disclosed vendor-side commission
CONTINGENCY_BPS = env_int("CONTINGENCY_BPS", 1000)
PRICE_STALE_DAYS = env_int("PRICE_STALE_DAYS", 90)
DATA_RETENTION_DAYS = env_int("DATA_RETENTION_DAYS", 730)
SHOW_DEMO_VENDORS = env_bool("SHOW_DEMO_VENDORS", DEBUG)
OPERATOR_EMAIL = os.environ.get("OPERATOR_EMAIL", "")
SITE_NAME = os.environ.get("SITE_NAME", "Rwanda Wedding Planner")
SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "")
PAYMENT_INSTRUCTIONS = os.environ.get(
    "PAYMENT_INSTRUCTIONS",
    "Payment instructions will be provided by our team with your invoice. "
    "Never pay anyone who contacts you outside this website or our official contact details.",
)
PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "manual")
PAYMENT_WEBHOOK_SECRET = os.environ.get("PAYMENT_WEBHOOK_SECRET", "")

# --- Email -------------------------------------------------------------------
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@example.com")

# --- Rate limiting (see apps/core/ratelimit.py) -------------------------------
RATELIMIT_ENABLED = env_bool("RATELIMIT_ENABLED", not TESTING)

# --- Security ----------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if not DEBUG and not TESTING:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # Render terminates TLS
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz/?$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 3600)  # raise to 31536000 once HTTPS is verified
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    SECURE_HSTS_PRELOAD = False

# --- Logging (structured, to stdout, never logs secrets or request bodies) ----
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "kv": {"format": "ts=%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "kv"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
if TESTING:
    logging.disable(logging.CRITICAL)
