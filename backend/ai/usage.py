"""Monthly generation limits, kept on the Firestore user document."""
from datetime import datetime, timezone

from google.cloud import firestore

from core import firebase

PLAN_LIMITS = {"free": 10, "pro": 100, "premium": 9999}


def _month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def count_this_month(data):
    """A count from an earlier month no longer applies. A document with no
    month yet (written before monthly resets) keeps its count."""
    if data.get("usagePeriod") not in (None, _month()):
        return 0
    return data.get("usageCount") or 0


@firestore.transactional
def _change(transaction, ref, delta):
    data = ref.get(transaction=transaction).to_dict() or {}
    plan = data.get("plan") or "free"
    count = count_this_month(data)
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    if delta > 0 and count >= limit:
        return {"allowed": False, "plan": plan, "usageCount": count, "limit": limit}
    count = max(0, count + delta)
    transaction.set(ref, {"plan": plan, "usageCount": count, "usagePeriod": _month()}, merge=True)
    return {"allowed": True, "usageCount": count}


def _apply(uid, delta):
    db = firebase.db()
    if db is None:
        return {"allowed": True}  # mock mode: usage is not tracked
    return _change(db.transaction(), db.collection("users").document(uid), delta)


def reserve(uid):
    """Count one generation, atomically, if the limit allows it. Raises if the
    count can't be checked."""
    return _apply(uid, 1)


def refund(uid):
    """Give back a generation that produced nothing."""
    return _apply(uid, -1)
