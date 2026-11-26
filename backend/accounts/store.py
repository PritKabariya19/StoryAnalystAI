"""Local JSON-file user store."""
import json
import secrets
import string
import threading
import time

from django.conf import settings

from core.http import iso_now

_lock = threading.Lock()


def _read():
    try:
        return json.loads(settings.USERS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    # A corrupt file raises, so a later signup cannot overwrite every account.


def get_user_by_email(email):
    """Case-insensitive. An exact match wins, for older accounts that differ only by case."""
    users = _read()
    wanted = str(email or "").casefold()
    return (next((u for u in users if u.get("email") == email), None)
            or next((u for u in users if str(u.get("email", "")).casefold() == wanted), None))


def set_plan(uid, plan, order_id):
    """Save a new plan and reset usage, once per order. Return False if the user isn't local."""
    with _lock:
        users = _read()
        user = next((u for u in users if u.get("uid") == uid), None)
        if user is None:
            return False
        paid = user.get("paidOrders") or []
        if order_id and order_id in paid:
            return True
        user.update(plan=plan, usageCount=0, paidOrders=paid + [order_id] if order_id else paid)
        settings.USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")
        return True


def create_user(email, name, password_hash):
    """Return the new user, or None if the email is already registered."""
    with _lock:
        users = _read()
        if any(str(u.get("email", "")).casefold() == email.casefold() for u in users):
            return None
        suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(9))
        user = {
            "uid": f"local-{int(time.time() * 1000)}-{suffix}",
            "email": email,
            "name": name,
            "password": password_hash,
            "createdAt": iso_now(),
            "plan": "free",
            "usageCount": 0,
        }
        users.append(user)
        settings.USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        settings.USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")
        return user
