from pathlib import Path

SECRET_KEY = "tests-only-never-use-in-production"
BASE_DIR = Path(__file__).resolve().parent.parent
INSTALLED_APPS = [
    "modeltranslation",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "eve_sde",
    "aasov",
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3"}}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
ROOT_URLCONF = "tests.urls"
USE_TZ = True
USE_I18N = True
LANGUAGE_CODE = "en"
LANGUAGES = tuple(
    (code, code)
    for code in (
        "en",
        "de",
        "es",
        "it-it",
        "ja",
        "ko-kr",
        "fr-fr",
        "nl-nl",
        "pl-pl",
        "ru",
        "uk",
        "zh-hans",
    )
)
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "DIRS": [BASE_DIR / "tests" / "templates"],
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
STATIC_URL = "/static/"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
ESDE_FREELANCE_FACTION_MODEL = None
ESDE_FREELANCE_CHARACTER_MODEL = None
ESDE_FREELANCE_CORPORATION_MODEL = None
ESDE_FREELANCE_ALLIANCE_MODEL = None
