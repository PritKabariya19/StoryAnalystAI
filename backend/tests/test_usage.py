from unittest import mock

import requests

from ai import usage, views
from core import firebase
from tests.support import ApiTestCase, FakeFirestore


class Reply:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self.data


class UsageTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.token, self.uid = self.signup()
        self.db = FakeFirestore()
        # The real transaction wrapper needs a real client; test the logic inside it.
        for patch in (mock.patch.object(usage, "_change", usage._change.to_wrap),
                      mock.patch.object(firebase, "db", return_value=self.db)):
            patch.start()
            self.addCleanup(patch.stop)

    def user(self):
        return self.db.docs.get(f"users/{self.uid}")

    def generate(self, **reply):
        with mock.patch.object(views.requests, "post", **reply):
            return self.post("/api/ai/explore-website", {"url": "http://site.test"}, self.token)

    def test_first_request_is_counted_and_reported(self):
        response = self.generate(return_value=Reply({"pages": []}))
        self.assertEqual(response.json()["usageCount"], 1)
        self.assertEqual(self.user()["usageCount"], 1)

    def test_limit(self):
        self.db.docs[f"users/{self.uid}"] = {"plan": "free", "usageCount": 10}
        response = self.generate(return_value=Reply({}))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.user()["usageCount"], 10)

    def test_count_resets_each_month(self):
        self.db.docs[f"users/{self.uid}"] = {"plan": "free", "usageCount": 10, "usagePeriod": "2000-01"}
        self.assertEqual(self.generate(return_value=Reply({})).json()["usageCount"], 1)

    def test_failures_are_not_charged_or_replaced_with_demo_data(self):
        for reply, status in (({"side_effect": requests.Timeout()}, 504),
                              ({"side_effect": requests.ConnectionError()}, 502),
                              ({"return_value": Reply({"error": "boom"}, 500)}, 502),
                              ({"return_value": Reply({"error": "bad url"}, 400)}, 400)):
            response = self.generate(**reply)
            self.assertEqual(response.status_code, status)
            self.assertNotIn("mock", response.json())
            self.assertEqual(self.user()["usageCount"], 0)

    def test_demo_data_only_when_switched_on(self):
        with self.settings(AI_DEMO_DATA=True):
            response = self.generate(side_effect=requests.ConnectionError())
        self.assertTrue(response.json()["mock"])

    def test_request_is_refused_when_usage_cannot_be_checked(self):
        with mock.patch.object(usage, "reserve", side_effect=RuntimeError("down")):
            self.assertEqual(self.generate(return_value=Reply({})).status_code, 503)
