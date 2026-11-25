"""Razorpay order creation and signature checks."""
import hashlib
import hmac
import logging
import os
import time

import requests

from core.http import upstream_error

log = logging.getLogger(__name__)

PLAN_AMOUNTS = {"pro": 19900, "premium": 49900}  # paise: ₹199, ₹499


def create_order(plan):
    key_id, key_secret = os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")
    amount = PLAN_AMOUNTS[plan]

    if not key_id or not key_secret:  # mock mode
        return {"id": f"mock_order_{int(time.time() * 1000)}", "amount": amount, "currency": "INR", "plan": plan, "mock": True}

    response = requests.post(
        "https://api.razorpay.com/v1/orders",
        auth=(key_id, key_secret),
        json={
            "amount": amount,
            "currency": "INR",
            "notes": {"plan": plan},
            "receipt": f"receipt_{plan}_{int(time.time() * 1000)}",
        },
        timeout=30,
    )
    if not response.ok:
        detail = upstream_error(response)
        if isinstance(detail, dict):
            detail = detail.get("description")
        raise RuntimeError(detail or f"Razorpay returned status {response.status_code}")
    return response.json()


def _matches(secret, message, signature):
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), str(signature or "").encode())


def verify_webhook_signature(raw_body, signature):
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        log.warning("RAZORPAY_WEBHOOK_SECRET not set — skipping verification (dev mode).")
        return True
    return _matches(secret, raw_body, signature)


def verify_payment_signature(order_id, payment_id, signature):
    secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not secret:
        return True  # mock mode
    return _matches(secret, f"{order_id}|{payment_id}".encode(), signature)
