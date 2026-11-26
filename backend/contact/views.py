import logging
import re
import time

from django.http import JsonResponse

from core import firebase
from core.http import EMAIL_PATTERN, api, error, is_blank, iso_now

log = logging.getLogger(__name__)

def _sanitize(value):
    return re.sub(r"<[^>]*>", "", value)[:2000] if isinstance(value, str) else ""


@api("POST")
def contact(request):
    name, email, subject, message = (request.json.get(k) for k in ("name", "email", "subject", "message"))
    if is_blank(name) or is_blank(email) or is_blank(message):
        return error("Name, email, and message are required.", 400)
    if not EMAIL_PATTERN.fullmatch(email):
        return error("Invalid email address.", 400)

    entry = {
        "name": _sanitize(name),
        "email": _sanitize(email),
        "subject": _sanitize(subject or "General Inquiry"),
        "message": _sanitize(message),
        "timestamp": int(time.time() * 1000),
        "createdAt": iso_now(),
        "status": "new",
    }

    firestore = firebase.db()
    if firestore is None:
        log.info("[Contact] Mock mode — entry: %s", entry)
        return JsonResponse({"success": True, "mock": True})
    try:
        firestore.collection("contacts").add(entry)
    except Exception as exc:
        log.error("[contact] %s", exc)
        return error("Failed to save message. Please try again.", 500)
    return JsonResponse({"success": True})
