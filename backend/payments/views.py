import json
import logging
import os

import stripe
from django.http import JsonResponse

from core import firebase
from core.http import api, error, iso_now
from payments import razorpay

log = logging.getLogger(__name__)

PLAN_NAMES = {"pro": "Pro", "premium": "Premium"}


def _update_user_plan(uid, plan):
    firestore = firebase.db()
    if firestore is None:
        log.warning("[Payments] Firestore unavailable — skipping plan update (mock mode).")
        return
    firestore.collection("users").document(uid).set(
        {"plan": plan, "planUpdatedAt": iso_now(), "usageCount": 0},  # usage resets on upgrade
        merge=True,
    )


@api("POST", auth=True)
def create_order(request):
    plan = request.json.get("plan")
    if plan not in ("pro", "premium"):
        return error("Invalid plan. Choose 'pro' or 'premium'.", 400)
    try:
        order = razorpay.create_order(plan)
    except Exception as exc:
        log.error("[create-order] %s", exc)
        return error(f"Could not create payment order: {exc}", 500)
    return JsonResponse({
        "orderId": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "plan": plan,
        "keyId": os.getenv("RAZORPAY_KEY_ID") or "rzp_test_mock",
        "mock": order.get("mock", False),
    })


@api("POST", auth=True)
def verify(request):
    """Called by the frontend after a successful Razorpay checkout."""
    order_id, payment_id, signature, plan = (
        request.json.get(k) for k in ("orderId", "paymentId", "signature", "plan")
    )
    if not order_id or not payment_id or not plan:
        return error("Missing payment details.", 400)
    if not razorpay.verify_payment_signature(order_id, payment_id, signature):
        return error("Payment verification failed — invalid signature.", 400)
    try:
        _update_user_plan(request.user_claims["uid"], plan)
    except Exception as exc:
        log.error("[verify] %s", exc)
        return error(f"Payment verified but plan update failed: {exc}", 500)
    return JsonResponse({
        "success": True,
        "plan": plan,
        "message": f"Plan upgraded to {PLAN_NAMES.get(plan, plan)} successfully!",
    })


@api("POST", raw=True)
def webhook(request):
    raw_body = request.body  # signature is computed over the exact bytes
    if not razorpay.verify_webhook_signature(raw_body, request.headers.get("x-razorpay-signature")):
        log.error("[webhook] Invalid signature")
        return error("Invalid webhook signature.", 400)
    try:
        event = json.loads(raw_body)
        name = event.get("event")
    except (ValueError, AttributeError):
        return error("Invalid JSON body.", 400)

    log.info("[webhook] Event: %s", name)
    if name == "payment.captured":
        payment = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
        notes = payment.get("notes") or {}
        plan, uid = (notes.get("plan"), notes.get("uid")) if isinstance(notes, dict) else (None, None)
        if uid and plan:
            try:
                _update_user_plan(uid, plan)
                log.info("[webhook] Plan updated: %s → %s", uid, plan)
            except Exception as exc:
                log.error("[webhook] Plan update failed: %s", exc)
    return JsonResponse({"received": True})


@api("POST", auth=True)
def stripe_charge(request):
    plan = request.json.get("plan")
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        return error("Stripe not configured. Please use Razorpay.", 501)
    amount = razorpay.PLAN_AMOUNTS.get(plan) if isinstance(plan, str) else None
    if not amount:
        return error("Invalid plan.", 400)
    frontend = os.getenv("FRONTEND_URL")
    try:
        session = stripe.checkout.Session.create(
            api_key=key,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "inr",
                    "product_data": {"name": f"StoryAnalyst AI — {plan} Plan"},
                    "unit_amount": amount,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{frontend}/subscription?success=true&plan={plan}",
            cancel_url=f"{frontend}/subscription?cancelled=true",
            metadata={"uid": request.user_claims["uid"], "plan": plan},
        )
    except Exception as exc:
        log.error("[stripe] %s", exc)
        return error(f"Stripe error: {exc}", 500)
    return JsonResponse({"url": session.url})
