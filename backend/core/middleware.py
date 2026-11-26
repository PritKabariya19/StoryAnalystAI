"""Fixed-window, per-IP rate limits (in memory)."""
import threading
import time

from django.conf import settings
from django.http import JsonResponse

# (path prefix, window seconds, max requests, message)
LIMITS = [
    ("/", 15 * 60, 200, "Too many requests. Please slow down."),
    ("/api/ai", 60, 15, "AI rate limit exceeded. Try again in a minute."),
]

# The UI polls every 4s during a run (225 per 15 minutes), so job status and
# screenshots are counted only against their own, higher limit.
POLL_PATHS = ("/api/tests/status/", "/api/tests/screenshots/")
POLL_LIMITS = [("/api/tests", 15 * 60, 1000, "Too many requests. Please slow down.")]


def _client_ip(request):
    """With N trusted proxies, the client is the Nth X-Forwarded-For entry from
    the right. Entries further left can be forged by the client."""
    hops = settings.TRUSTED_PROXY_COUNT
    forwarded = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
    if hops > 0 and len(forwarded) >= hops:
        return forwarded[-hops]
    return request.META.get("REMOTE_ADDR", "")


class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.lock = threading.Lock()
        self.hits = {}  # (prefix, ip) -> (window start, count)

    def __call__(self, request):
        ip = _client_ip(request)
        now = time.monotonic()
        limits = POLL_LIMITS if request.path.startswith(POLL_PATHS) else LIMITS
        for prefix, window, limit, message in limits:
            if not request.path.startswith(prefix):
                continue
            with self.lock:
                start, count = self.hits.get((prefix, ip), (now, 0))
                if now - start >= window:
                    start, count = now, 0
                self.hits[(prefix, ip)] = (start, count + 1)
            if count >= limit:
                return JsonResponse({"error": message}, status=429)
        return self.get_response(request)
