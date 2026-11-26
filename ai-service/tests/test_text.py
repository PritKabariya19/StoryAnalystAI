import unittest
from unittest import mock

from bs4 import BeautifulSoup

import tests  # noqa: F401  (sets the environment)
from agents.combined_generator import CombinedGeneratorAgent
from agents.smart_test_generator import SmartTestGeneratorAgent, _TEST_GEN_SYSTEM_PROMPT
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


class SmartPromptTests(unittest.TestCase):
    def test_prompt_states_the_limit(self):
        self.assertIn("up to a maximum of 100", _TEST_GEN_SYSTEM_PROMPT)
        self.assertNotIn("{max_cases}", _TEST_GEN_SYSTEM_PROMPT)

    def test_cases_are_capped_across_suites(self):
        suites = [{"component": c, "test_cases": [{}] * 60} for c in "ABC"]
        capped = SmartTestGeneratorAgent._limit_cases(suites, 100)
        self.assertEqual([len(s["test_cases"]) for s in capped], [60, 40])

    def test_field_limits_and_choice_values_reach_the_ai(self):
        soup = BeautifulSoup('<input type="password" minlength="8"><input type="radio" value="a">',
                             "html.parser")
        password, radio = [SmartTestGeneratorAgent._type_info(t) for t in soup("input")]
        self.assertEqual(password, {"type": "password", "minlength": "8"})
        self.assertEqual(radio, {"type": "radio", "value": "a"})

    def test_javascript_repeat_in_reply_is_expanded(self):
        raw = '{"test_suites": [{"value": "a".repeat(5) + "@example.com"}]}'
        data = SmartTestGeneratorAgent()._parse_test_cases(raw)
        self.assertEqual(data["test_suites"][0]["value"], "aaaaa@example.com")

    def test_tests_that_cannot_run_are_dropped(self):
        def suite(step):
            return {"component": "A", "test_cases": [{"automation_steps": [step]}]}

        runnable = [{"action": "upload"}, "Assert that the page/response reflects: 'error for email'."]
        impossible = [{"action": "hover"}, "Assert that the page/response reflects: 'page loaded'."]
        kept = SmartTestGeneratorAgent._runnable_only([suite(s) for s in runnable + impossible])
        self.assertEqual([k["test_cases"][0]["automation_steps"][0] for k in kept], runnable)
