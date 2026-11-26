"""Razorpay order creation and signature checks."""
import hashlib
import hmac
import os
import time
from urllib.parse import quote

import requests

from core.http import upstream_error

PLAN_AMOUNTS = {"pro": 19900, "premium": 49900}  # paise: ₹199, ₹499


def is_configured():
    return bool(os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET"))


def mock_enabled():
    """Free upgrades for testing. Must be switched on; missing keys are not enough."""
    return not is_configured() and os.getenv("PAYMENTS_MOCK", "").lower() == "true"


def _request(method, path, **kwargs):
    response = requests.request(
        method,
        f"https://api.razorpay.com/v1/{path}",
        auth=(os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")),
        timeout=30,
        **kwargs,
    )
    if not response.ok:
        detail = upstream_error(response)
        if isinstance(detail, dict):
            detail = detail.get("description")
        raise RuntimeError(detail or f"Razorpay returned status {response.status_code}")
    return response.json()


def create_order(plan, uid):
    amount = PLAN_AMOUNTS[plan]

    if mock_enabled():
        return {"id": f"mock_order_{int(time.time() * 1000)}", "amount": amount, "currency": "INR", "plan": plan, "mock": True}

    return _request("POST", "orders", json={
        "amount": amount,
        "currency": "INR",
        "notes": {"plan": plan, "uid": uid},  # read back by /verify
        "receipt": f"receipt_{plan}_{int(time.time() * 1000)}",
    })


def fetch_order(order_id):
    return _request("GET", f"orders/{quote(order_id, safe='')}")


def _matches(secret, message, signature):
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), str(signature or "").encode())


def purchase_of(order):
    """(uid, plan) that this order paid for, or None if its notes or amount don't fit."""
    notes = order.get("notes") if isinstance(order.get("notes"), dict) else {}
    uid, plan = notes.get("uid"), notes.get("plan")
    if not uid or plan not in ("pro", "premium") or order.get("amount") != PLAN_AMOUNTS[plan]:
        return None
    return uid, plan


def verify_webhook_signature(raw_body, signature):
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    return bool(secret) and _matches(secret, raw_body, signature)


def verify_payment_signature(order_id, payment_id, signature):
    secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not secret:
        return False
    return _matches(secret, f"{order_id}|{payment_id}".encode(), signature)
