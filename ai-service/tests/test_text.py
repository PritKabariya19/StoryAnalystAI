import unittest
from unittest import mock

import tests  # noqa: F401  (sets the environment)
from agents.combined_generator import CombinedGeneratorAgent
from agents.smart_test_generator import SmartTestGeneratorAgent
from agents.test_executor import _is_negative_tc
from agents.url_guard import with_scheme
from engines.rule_analyst import RuleAnalyst
from engines.rule_generator import RuleGenerator
from orchestrator import Orchestrator


class ClassificationTests(unittest.TestCase):
    def test_words_inside_other_words_do_not_count(self):
        for engine in (RuleGenerator(), CombinedGeneratorAgent()):
            for text in ("admin logs in successfully", "user views product description"):
                self.assertEqual(engine._classify(text)[0], "Positive", (engine, text))
            self.assertEqual(engine._classify("password at minimum length")[0], "Boundary")
            self.assertEqual(engine._classify("XSS script in email field")[0], "Edge Case")
            self.assertEqual(engine._classify("login fails with wrong password")[0], "Negative")

    def test_executor_polarity(self):
        self.assertFalse(_is_negative_tc({"title": "View product description"}))
        self.assertFalse(_is_negative_tc({"type": "positive", "title": "No errors after login"}))
        self.assertTrue(_is_negative_tc({"title": "Login fails with invalid password"}))
        self.assertTrue(_is_negative_tc({"type": "negative"}))

    def test_feature_detection(self):
        detect = lambda story: RuleAnalyst().analyze(story).feature
        self.assertNotEqual(detect("As a user, I want to connect my facebook account"), "Booking")
        self.assertNotEqual(detect("As a user, I want to change border colour"), "Checkout")
        self.assertEqual(detect("As a user, I want to reset my password so that I can sign in"),
                         "Password Reset")
        self.assertEqual(detect("As an admin, I want to log in"), "Login")
        self.assertEqual(detect("As a guest, I want to book a room"), "Booking")


class UrlTests(unittest.TestCase):
    def test_missing_scheme(self):
        for local in ("localhost:3000", "127.0.0.1/x", "[::1]:80", "192.168.1.5"):
            self.assertEqual(with_scheme(local), "http://" + local)
        self.assertEqual(with_scheme("example.com/a"), "https://example.com/a")
        self.assertEqual(with_scheme("file:///etc/hosts"), "file:///etc/hosts")


class FallbackTests(unittest.TestCase):
    def test_rule_based_result_is_flagged_and_logged(self):
        with mock.patch("agents.llm.generate", side_effect=RuntimeError("quota")), \
                self.assertLogs("orchestrator", "ERROR"):
            result = Orchestrator().run("As a user, I want to log in")
        self.assertFalse(result["ai_generated"])

    def test_form_names_and_checkable_types(self):
        agent = SmartTestGeneratorAgent()
        html = '<form id="signup"><input id="email" type="email"><button>Go</button></form>'
        reply = mock.Mock(text=html, url="http://site.test/")
        with mock.patch("requests.get", return_value=reply):
            suites = agent._rule_based_fallback({"pages": [{"url": "http://site.test/"}]}, "", "")
        self.assertEqual(suites[0]["component"], "signup Form")
        types = {tc["test_type"] for suite in suites for tc in suite["test_cases"]}
        self.assertFalse(types & {"Performance", "Usability"})
