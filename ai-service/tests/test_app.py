import unittest
from unittest import mock

import tests  # noqa: F401
import app as service

SECRET = {"X-Internal-Secret": "test-only-internal-secret"}
REMOTE = {"REMOTE_ADDR": "203.0.113.9"}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = service.app.test_client()

    def test_secret_is_required(self):
        for path in ("/analyze", "/execute", "/report"):
            self.assertEqual(self.client.post(path, json={}).status_code, 403)
        self.assertEqual(self.client.get("/screenshots/x.png").status_code, 403)

    def test_public_default_secret_only_works_locally(self):
        headers = {"X-Internal-Secret": "dev_secret_change_me"}
        with mock.patch.object(service, "INTERNAL_SECRET", "dev_secret_change_me"):
            remote = self.client.post("/explore", json={}, headers=headers, environ_base=REMOTE)
            local = self.client.post("/explore", json={}, headers=headers)
        self.assertEqual((remote.status_code, local.status_code), (403, 400))

    def test_strong_secret_works_remotely(self):
        reply = self.client.post("/explore", json={}, headers=SECRET, environ_base=REMOTE)
        self.assertEqual(reply.status_code, 400)

    def test_latest_report_download_is_gone(self):
        self.assertEqual(self.client.get("/report/download", headers=SECRET).status_code, 404)

    def test_bad_input_types_are_json_400s(self):
        for path, body in (("/explore", {"url": "http://x", "depth": "abc"}),
                           ("/explore", {"url": 5}),
                           ("/analyze", {"story": ["a"]}),
                           ("/execute", {"test_cases": ["a"]}),
                           ("/execute", {"test_cases": [{}], "workers": "many"}),
                           ("/execute-batch", {"test_cases": [{}], "start_index": {}}),
                           ("/extract", [1])):
            reply = self.client.post(path, json=body, headers=SECRET)
            self.assertEqual(reply.status_code, 400, (path, body))
            self.assertIn("error", reply.get_json())
