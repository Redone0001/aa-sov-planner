"""Integration settings: real Alliance Auth apps, templates and hooks on SQLite."""

from allianceauth.project_template.project_name.settings.base import *  # noqa: F403

from .settings import (  # noqa: F401
    ALLOWED_HOSTS,
    BASE_DIR,
    DATABASES,
    DEFAULT_AUTO_FIELD,
    ESDE_FREELANCE_ALLIANCE_MODEL,
    ESDE_FREELANCE_CHARACTER_MODEL,
    ESDE_FREELANCE_CORPORATION_MODEL,
    ESDE_FREELANCE_FACTION_MODEL,
    LANGUAGE_CODE,
    LANGUAGES,
    PASSWORD_HASHERS,
    SECRET_KEY,
)

INSTALLED_APPS = ["modeltranslation", *INSTALLED_APPS, "eve_sde", "aasov"]  # noqa: F405
ROOT_URLCONF = "tests.aa_urls"
ESI_SSO_CLIENT_ID = "test"
ESI_SSO_CLIENT_SECRET = "test"
ESI_SSO_CALLBACK_URL = "http://localhost/sso/callback"
ESI_USER_CONTACT_EMAIL = "test@example.invalid"
ESI_USER_CONTACT_URL = "http://example.invalid"
SITE_NAME = "Alliance Auth · Sov Planner"
CELERY_TASK_ALWAYS_EAGER = False
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEBUG = True
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://localhost:6379/15",
        "OPTIONS": {"CLIENT_CLASS": "tests.cache.FakeRedisClient"},
    }
}
SITE_URL = "http://localhost:8765"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"handlers": [], "level": "WARNING"},
}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
CSRF_TRUSTED_ORIGINS = [SITE_URL]
WSGI_APPLICATION = None
# Local HTTP preview only; production AA retains its secure-cookie defaults.
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
