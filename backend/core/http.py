"""Request helpers shared by all views."""
import json
import math
import re
from datetime import datetime, timezone
from functools import wraps

from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse

from core.auth import verify_token

EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def error(message, status, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


def not_found(request, exception=None):
    return error("Route not found.", 404)


def server_error(request):
    return error("Internal server error. Please try again.", 500)


def api(method, auth=False, raw=False):
    """Restrict a view to one HTTP method, optionally require a token, and
    expose the parsed JSON body as `request.json` (skipped when `raw`)."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method != method:
                return not_found(request)

            request.json = {}
            if not raw and request.content_type == "application/json":
                try:
                    body = json.loads(request.body or b"{}")
                except RequestDataTooBig:
                    return error("request entity too large", 413)
                except ValueError as exc:
                    return error(str(exc), 400)
                if isinstance(body, dict):
                    request.json = body

            if auth:
                header = request.headers.get("Authorization", "")
                if not header.startswith("Bearer "):
                    return error("Missing or invalid Authorization header.", 401)
                request.user_claims = verify_token(header[len("Bearer "):])
                if request.user_claims is None:
                    return error("Invalid or expired token.", 401)

            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def is_blank(value):
    """True unless `value` is a string with non-whitespace content."""
    return not isinstance(value, str) or not value.strip()


def to_int(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return int(number) if math.isfinite(number) else default


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def upstream_error(response):
    """The `error` field of a failed Python service response, if it has one."""
    try:
        return response.json().get("error")
    except (ValueError, AttributeError):
        return None
