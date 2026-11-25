"""User profiles in Firestore `users/{uid}`, the document that holds plan and usage."""
import logging

from accounts import store
from core import firebase
from core.http import iso_now

log = logging.getLogger(__name__)

FIELDS = ("name", "email", "plan", "usageCount", "createdAt")


def get_or_create(claims):
    """Return the profile for verified token claims, filling in missing fields.

    Stored values are never overwritten, so a paid plan or usage count survives.
    Without Firestore the local record (or free-plan defaults) is returned.
    """
    uid, email = claims["uid"], claims.get("email")
    local = store.get_user_by_email(email) if uid.startswith("local-") else None
    local = local or {}
    defaults = {
        "name": claims.get("name") or local.get("name") or "",
        "email": email or "",
        "plan": local.get("plan") or "free",
        "usageCount": local.get("usageCount") or 0,
        "createdAt": local.get("createdAt") or iso_now(),
    }

    db = firebase.db()
    if db is None:
        return {"uid": uid, **defaults}
    try:
        ref = db.collection("users").document(uid)
        stored = ref.get().to_dict() or {}
        missing = {k: v for k, v in defaults.items() if k not in stored or (k == "name" and v and not stored[k])}
        if missing:
            ref.set(missing, merge=True)
        profile = {**stored, **missing}
        return {"uid": uid, **{k: profile[k] for k in FIELDS}}
    except Exception:
        log.exception("[Auth Profile Error]")
        return {"uid": uid, **defaults}
