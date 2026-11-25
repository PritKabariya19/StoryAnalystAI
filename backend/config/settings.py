import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ─── Django ───────────────────────────────────────────────────────────────────
DEBUG = False
ALLOWED_HOSTS = ["*"]
# No sessions, cookies or signed data are used; auth is by bearer token.
SECRET_KEY = os.getenv("INTERNAL_API_SECRET") or "dev_secret_change_me"
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
APPEND_SLASH = False
DATABASES = {}  # users are a JSON file, reports are Firestore or memory
INSTALLED_APPS = ["corsheaders"]
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "core.middleware.RateLimitMiddleware",
]
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024  # report payloads embed screenshots

# ─── CORS ─────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("FRONTEND_URL", "http://localhost:5173").split(",") if o.strip()
]
CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://localhost:\d+$"]
CORS_ALLOW_CREDENTIALS = True

# ─── App config ───────────────────────────────────────────────────────────────
PYTHON_AI_URL = os.getenv("PYTHON_AI_URL") or "http://localhost:10000"
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET") or "dev_secret_change_me"
# The fallback differs from the one above on purpose: tokens issued with it
# (including by the earlier Express backend) keep verifying.
JWT_SECRET = os.getenv("INTERNAL_API_SECRET") or "dev_secret_change_me_to_something_very_secure"
USERS_FILE = BASE_DIR / ".data" / "users.json"
SCREENSHOTS_DIR = BASE_DIR.parent / "ai-service" / "screenshots"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
