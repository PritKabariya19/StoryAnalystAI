import time

import jwt

from tests.support import ApiTestCase


class SignupTests(ApiTestCase):
    def signup_status(self, **changes):
        body = {"email": "a@x.test", "password": "secret123", "name": "N", **changes}
        return self.post("/api/auth/signup", body).status_code

    def test_rejects_weak_or_invalid_input(self):
        for changes in ({"password": "1"}, {"email": "notanemail"}, {"name": "  "},
                        {"password": 12345678}, {"email": ["a@x.test"]}):
            self.assertEqual(self.signup_status(**changes), 400, changes)

    def test_email_is_case_insensitive(self):
        self.signup("QA@Example.com")
        self.assertEqual(self.signup_status(email="qa@example.com"), 400)
        login = self.post("/api/auth/login", {"email": "Qa@EXAMPLE.com", "password": "secret123"})
        self.assertEqual(login.status_code, 200)

    def test_wrong_types_on_login_are_not_a_server_error(self):
        response = self.post("/api/auth/login", {"email": "a@x.test", "password": 123})
        self.assertEqual(response.status_code, 400)


class TokenTests(ApiTestCase):
    def test_tokens_signed_with_public_defaults_are_rejected(self):
        claims = {"uid": "made-up", "email": "x@x.test", "exp": int(time.time()) + 600}
        for secret in ("dev_secret_change_me", "dev_secret_change_me_to_something_very_secure",
                       "test-only-internal-secret"):
            token = jwt.encode(claims, secret, algorithm="HS256")
            self.assertEqual(self.get("/api/auth/me", token).status_code, 401, secret)
