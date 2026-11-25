"""Fixed-window, per-IP rate limits (in memory)."""
import threading
import time

from django.http import JsonResponse

# (path prefix, window seconds, max requests, message)
LIMITS = [
    ("/", 15 * 60, 200, "Too many requests. Please slow down."),
    ("/api/ai", 60, 15, "AI rate limit exceeded. Try again in a minute."),
]


class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.lock = threading.Lock()
        self.hits = {}  # (prefix, ip) -> (window start, count)

    def __call__(self, request):
        ip = request.META.get("REMOTE_ADDR", "")
        now = time.monotonic()
        for prefix, window, limit, message in LIMITS:
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
