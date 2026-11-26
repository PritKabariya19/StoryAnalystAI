import json
import logging
import os

import stripe
from django.http import JsonResponse

from accounts import store
from core import firebase
from core.http import api, error, iso_now
from payments import razorpay

log = logging.getLogger(__name__)

PLAN_NAMES = {"pro": "Pro", "premium": "Premium"}


def _update_user_plan(uid, plan, order_id):
    """Apply a paid order once. A repeat must not reset usage again."""
    firestore = firebase.db()
    if firestore is None:
        if not store.set_plan(uid, plan, order_id):
            log.warning("[Payments] Firestore unavailable and no local user — plan not saved.")
        return
    ref = firestore.collection("users").document(uid)
    paid = (ref.get().to_dict() or {}).get("paidOrders") or []
    if order_id and order_id in paid:
        return
    ref.set(
        {"plan": plan, "planUpdatedAt": iso_now(), "usageCount": 0,  # usage resets on upgrade
         "paidOrders": paid + [order_id] if order_id else paid},
        merge=True,
    )


@api("POST", auth=True)
def create_order(request):
    plan = request.json.get("plan")
    if plan not in ("pro", "premium"):
        return error("Invalid plan. Choose 'pro' or 'premium'.", 400)
    if not razorpay.is_configured() and not razorpay.mock_enabled():
        return error("Payments are not configured.", 503)
    try:
        order = razorpay.create_order(plan, request.user_claims["uid"])
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
    if plan not in ("pro", "premium"):
        return error("Invalid plan. Choose 'pro' or 'premium'.", 400)
    uid = request.user_claims["uid"]
    if not razorpay.mock_enabled():
        if not razorpay.is_configured():
            return error("Payments are not configured.", 503)
        if not razorpay.verify_payment_signature(order_id, payment_id, signature):
            return error("Payment verification failed — invalid signature.", 400)
        try:
            order = razorpay.fetch_order(str(order_id))
        except Exception as exc:
            log.error("[verify] Could not fetch order: %s", exc)
            return error("Could not confirm the order with Razorpay. Please try again.", 502)
        # The client's plan is not trusted: use what this user's order was created for.
        purchase = razorpay.purchase_of(order)
        if purchase is None or purchase[0] != uid:
            return error("Payment verification failed — order does not match.", 400)
        plan = purchase[1]
    try:
        _update_user_plan(uid, plan, str(order_id))
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
        log.error("[webhook] Missing or invalid signature (is RAZORPAY_WEBHOOK_SECRET set?)")
        return error("Invalid webhook signature.", 400)
    try:
        event = json.loads(raw_body)
        name = event.get("event")
    except (ValueError, AttributeError):
        return error("Invalid JSON body.", 400)

    log.info("[webhook] Event: %s", name)
    if name != "payment.captured":
        return JsonResponse({"received": True})
    try:
        order_id = event["payload"]["payment"]["entity"]["order_id"]
    except (KeyError, TypeError):
        order_id = None
    if not isinstance(order_id, str) or not order_id:
        return JsonResponse({"received": True})

    # The order, not the payment, records who bought which plan.
    # A 5xx reply makes Razorpay send the event again.
    try:
        purchase = razorpay.purchase_of(razorpay.fetch_order(order_id))
        if purchase:
            _update_user_plan(*purchase, order_id)
            log.info("[webhook] Plan updated: %s → %s", *purchase)
        else:
            log.warning("[webhook] Order %s does not match a plan purchase", order_id)
    except Exception as exc:
        log.error("[webhook] Plan update failed: %s", exc)
        return error("Could not process the payment yet.", 500)
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
