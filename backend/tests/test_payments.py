import hashlib
import hmac
import json
import os
from unittest import mock

from accounts import store
from payments import razorpay
from tests.support import ApiTestCase

BASE = "/api/payments/razorpay"


def sign(message):
    return hmac.new(b"secret", message, hashlib.sha256).hexdigest()


class PaymentCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.token, self.uid = self.signup()
        self.orders = {}
        patch = mock.patch.object(razorpay, "_request", self.fake_razorpay)
        patch.start()
        self.addCleanup(patch.stop)

    def env(self, **values):
        patch = mock.patch.dict(os.environ, values)
        patch.start()
        self.addCleanup(patch.stop)

    def fake_razorpay(self, method, path, **kwargs):
        if method == "POST":
            order = {"id": f"order_{len(self.orders) + 1}", **kwargs["json"]}
            self.orders[order["id"]] = order
            return order
        return self.orders[path.split("/")[1]]

    def create(self, plan="pro"):
        return self.post(f"{BASE}/create-order", {"plan": plan}, self.token)

    def verify(self, order_id, plan="pro", token=None, signature=None):
        return self.post(f"{BASE}/verify", {
            "orderId": order_id, "paymentId": "pay_1", "plan": plan,
            "signature": signature or sign(f"{order_id}|pay_1".encode()),
        }, token or self.token)

    def user(self):
        return next(u for u in store._read() if u["uid"] == self.uid)

    def set_usage(self, count):
        users = store._read()
        users[0]["usageCount"] = count
        store.settings.USERS_FILE.write_text(json.dumps(users))


class UnconfiguredTests(PaymentCase):
    def test_nothing_is_free_without_keys(self):
        self.assertEqual(self.create().status_code, 503)
        self.assertEqual(self.verify("fake").status_code, 503)
        self.assertEqual(self.user()["plan"], "free")

    def test_test_mode_must_be_switched_on(self):
        self.env(PAYMENTS_MOCK="true")
        order = self.create().json()
        self.assertTrue(order["mock"])
        self.assertEqual(self.verify(order["orderId"]).status_code, 200)
        self.assertEqual(self.user()["plan"], "pro")
        self.assertEqual(self.verify(order["orderId"], plan="ultra").status_code, 400)


class ConfiguredTests(PaymentCase):
    def setUp(self):
        super().setUp()
        self.env(RAZORPAY_KEY_ID="id", RAZORPAY_KEY_SECRET="secret", PAYMENTS_MOCK="true")

    def test_plan_comes_from_the_order(self):
        order_id = self.create("pro").json()["orderId"]
        self.assertEqual(self.verify(order_id, plan="premium").json()["plan"], "pro")

    def test_rejects_bad_signature_other_user_and_wrong_amount(self):
        order_id = self.create().json()["orderId"]
        self.assertEqual(self.verify(order_id, signature="bad").status_code, 400)
        other, _ = self.signup("b@x.test")
        self.assertEqual(self.verify(order_id, token=other).status_code, 400)
        self.orders[order_id]["amount"] = 1
        self.assertEqual(self.verify(order_id).status_code, 400)
        self.assertEqual(self.user()["plan"], "free")

    def test_an_order_is_applied_once(self):
        order_id = self.create().json()["orderId"]
        self.verify(order_id)
        self.set_usage(7)
        self.verify(order_id)
        self.assertEqual(self.user()["usageCount"], 7)

    def webhook(self, order_id, signature=None):
        body = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {
            "order_id": order_id, "notes": {"uid": self.uid, "plan": "premium"}}}}}).encode()
        headers = {} if signature == "" else {"HTTP_X_RAZORPAY_SIGNATURE": signature or sign(body)}
        return self.client.post(f"{BASE}/webhook", body, content_type="application/json", **headers)

    def test_webhook_needs_a_configured_secret_and_signature(self):
        order_id = self.create().json()["orderId"]
        self.assertEqual(self.webhook(order_id).status_code, 400)  # no webhook secret
        self.env(RAZORPAY_WEBHOOK_SECRET="secret")
        self.assertEqual(self.webhook(order_id, "").status_code, 400)
        self.assertEqual(self.webhook(order_id, "bad").status_code, 400)
        self.assertEqual(self.user()["plan"], "free")

    def test_webhook_upgrades_using_the_order(self):
        self.env(RAZORPAY_WEBHOOK_SECRET="secret")
        order_id = self.create("pro").json()["orderId"]
        self.assertEqual(self.webhook(order_id).status_code, 200)
        self.assertEqual(self.user()["plan"], "pro")  # not the payment's "premium"

    def test_webhook_asks_for_a_retry_when_razorpay_fails(self):
        self.env(RAZORPAY_WEBHOOK_SECRET="secret")
        self.assertEqual(self.webhook("order_unknown").status_code, 500)
