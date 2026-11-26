import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")



def _jwt_secret():
    """The key that signs login tokens. Without a strong JWT_SECRET, a random
    one is created once and kept in .data/, so there is no guessable default."""
    value = os.getenv("JWT_SECRET", "")
    if len(value) >= 32:
        return value
    path = BASE_DIR / ".data" / "jwt_secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
            file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return path.read_text().strip()


JWT_SECRET = _jwt_secret()

# ─── Django ───────────────────────────────────────────────────────────────────
DEBUG = False
ALLOWED_HOSTS = ["*"]
# No sessions, cookies or signed data are used; auth is by bearer token.
SECRET_KEY = JWT_SECRET
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
# Return demo data when the AI service fails (for UI work without it).
AI_DEMO_DATA = os.getenv("AI_DEMO_DATA", "").lower() == "true"
# Reverse proxies in front of this server. 0 ignores X-Forwarded-For.
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT") or 0)
USERS_FILE = BASE_DIR / ".data" / "users.json"
SCREENSHOTS_DIR = BASE_DIR.parent / "ai-service" / "screenshots"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
