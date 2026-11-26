"""Needs Chromium: ./venv/bin/playwright install chromium"""
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import requests

import tests  # noqa: F401
from agents import test_executor, url_guard
from agents.smart_test_generator import SmartTestGeneratorAgent
from agents.website_explorer import WebsiteExplorerAgent
from agents.website_extractor import WebsiteExtractorAgent
from tests.site import TEST_WEB, Site

SECRET_TEXT = "TOPSECRET-MARKER"
PRIVATE = "http://127.0.0.1:{port}"
PAGES = {
    "/": TEST_WEB,
    "/sub/index.html": '<a href="next.html">Next</a>',
    "/moved": "redirect:/sub/index.html",
    "/upload": '<input id="f" type="file" onchange="this.files[0].text().then('
               "t => o.textContent = this.files[0].name + '|' + t)\"><p id='o'></p>",
    "/links": '<a href="/sub/index.html">Merge PDF</a> <a href="/gone">Split PDF</a>',
    # Like many converters: the file field is added by script, and the button
    # appears only after a supported file is chosen
    "/convert": """<div id="box"></div><button id="go" hidden>Convert to PDF</button><script>
        const input = Object.assign(document.createElement('input'),
                                    {type: 'file', accept: '.jpg', multiple: true});
        input.onchange = () => go.hidden = !input.files[0].name.endsWith('.jpg');
        box.append(input);
        go.onclick = () => setTimeout(() => Object.assign(document.createElement('a'),
            {href: URL.createObjectURL(new Blob(['%PDF-1.4 sample'])), download: 'out.pdf'}).click(), 500);
        </script>""",
    "/private": "<p>INTERNAL-DATA</p>",
    "/redirect": f"redirect:{PRIVATE}/secret-redirect",
    "/embeds": f"<p>public page ok</p><img src='{PRIVATE}/secret-img'>"
               f"<iframe src='{PRIVATE}/secret-frame'></iframe>"
               f"<script>fetch('{PRIVATE}/secret-fetch').catch(() => 0)</script>",
}


def see(text):
    return {"action": "assert", "type": "text", "expected": text}


def fill(field, value):
    return {"action": "input", "locator": {"type": "id", "value": field}, "value": value}


SUBMIT = {"action": "click", "locator": {"type": "css", "value": "#loginForm button"}}


class BrowserCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = Site(PAGES)
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.addClassCleanup(cls.site.close)
        cls.folder = Path(folder.name)

    def run_cases(self, *cases):
        cases = [{"tc_id": f"T{i}", "type": "positive", "page_url": self.site.url + "/", **c}
                 for i, c in enumerate(cases)]
        return test_executor.TestExecutorAgent().execute_all(cases, screenshots_dir=self.folder)


class ExecutorTests(BrowserCase):
    def test_verdicts_on_the_test_page(self):
        wrong_login = [fill("loginEmail", "a@x.test"), fill("loginPassword", "pw"), SUBMIT]
        message = lambda text: {"action": "assert", "type": "element",
                                "locator": {"type": "id", "value": "message"}, "expected": text}
        results = self.run_cases(
            {"automation_steps": wrong_login + [message("Invalid email or password")]},
            {"automation_steps": wrong_login + [message("Login successful!")]},
            {"automation_steps": [see("text that is not there")]},
            {"automation_steps": [{"action": "hover"}]},
        )
        self.assertEqual([r["status"] for r in results], ["Pass", "Fail", "Fail", "Error"])

    def test_link_clicks_happen_and_are_described(self):
        def link(text, target):
            return {"page_url": self.site.url + "/links", "automation_steps": [
                f"Find link or button with text '{text}' and click().",
                f"Assert that the URL contains '{target}'."]}
        good, broken = self.run_cases(link("Merge PDF", "/sub/index.html"), link("Split PDF", "/gone"))
        self.assertEqual([good["status"], broken["status"]], ["Pass", "Fail"])
        self.assertIn(f"After click: went to {self.site.url}/sub/index.html", good["log"])
        self.assertIn("Click led to HTTP 404", broken["error_message"])

    def test_upload_task_is_generated_and_performed(self):
        url = self.site.url + "/convert"
        agent = SmartTestGeneratorAgent()
        with mock.patch("agents.uploads.UPLOAD_SETTLE_MS", 500):
            suites = agent._rule_based_fallback({"pages": [{"url": url}]}, "", url)
        cases = suites[0]["test_cases"]
        self.assertEqual(len(cases), 3)  # one file, two files, wrong type
        results = self.run_cases(*cases)
        self.assertEqual([r["status"] for r in results], ["Pass"] * 3, [r["log"] for r in results])
        self.assertIn("downloaded 'out.pdf'", results[0]["log"])

    def test_missing_download_fails(self):
        steps = [{"action": "upload"}, {"action": "assert", "type": "download", "expected": ".pdf"}]
        with mock.patch.object(test_executor, "DOWNLOAD_WAIT", 1):
            result, = self.run_cases({"page_url": self.site.url + "/convert", "automation_steps": steps})
        self.assertEqual(result["status"], "Fail")
        self.assertIn("No file was downloaded", result["error_message"])

    def test_screenshot_names_cannot_be_guessed(self):
        names = [r["screenshot_path"] for r in self.run_cases(*[{"automation_steps": [see("Login")]}] * 2)]
        self.assertRegex(names[0], r"_[0-9a-f]{32}\.png$")
        self.assertNotEqual(names[0][-36:], names[1][-36:])

    def test_old_screenshots_are_deleted(self):
        old = self.folder / "old.png"
        old.write_bytes(b"x")
        stamp = time.time() - test_executor.SCREENSHOT_TTL - 60
        os.utime(old, (stamp, stamp))
        self.run_cases({"automation_steps": [see("Login")]})
        self.assertFalse(old.exists())

    def test_local_files_cannot_be_opened_or_uploaded(self):
        marker = self.folder / "marker.txt"
        marker.write_text(SECRET_TEXT)
        results = self.run_cases(
            {"page_url": marker.as_uri(), "automation_steps": [see(SECRET_TEXT)]},
            {"automation_steps": [{"action": "navigate", "url": marker.as_uri()}, see(SECRET_TEXT)]},
            {"page_url": self.site.url + "/upload",
             "automation_steps": [fill("f", str(marker)), see("marker.txt|StoryAnalystAI test file")]},
        )
        self.assertEqual([r["status"] for r in results], ["Error", "Error", "Pass"])

    def test_a_crashed_worker_reports_its_tests(self):
        agent = test_executor.TestExecutorAgent()
        with mock.patch.object(agent, "_run_worker", side_effect=RuntimeError("boom")):
            results = agent.execute_all([{"tc_id": "A"}, {"tc_id": "B"}], workers=2,
                                        screenshots_dir=self.folder)
        self.assertEqual([(r["tc_id"], r["status"]) for r in results], [("A", "Error"), ("B", "Error")])


class CrawlTests(BrowserCase):
    def test_relative_links_resolve_against_the_page(self):
        for path in ("/sub/index.html", "/moved"):
            expected = self.site.url + "/sub/next.html"
            extracted = WebsiteExtractorAgent().extract(self.site.url + path, depth=0)
            self.assertEqual(extracted["pages"][0]["links"]["internal"][0]["href"], expected)
            explored = WebsiteExplorerAgent().explore(self.site.url + path, depth=0)
            self.assertEqual(explored["pages"][0]["links"][0]["href"], expected)


class PrivateAddressTests(BrowserCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"BLOCK_PRIVATE_URLS": "true"})
        env.start()
        self.addCleanup(env.stop)

    def test_private_addresses_are_refused(self):
        for host in ("127.0.0.1", "localhost", "[::1]", "0.0.0.0", "2130706433", "127.1",
                     "[::ffff:127.0.0.1]", "10.0.0.1", "192.168.1.1", "169.254.169.254"):
            with self.assertRaises(url_guard.BlockedURL, msg=host):
                url_guard.check_url(f"http://{host}/")
        result = self.run_cases({"automation_steps": [see("Login")]})[0]
        self.assertEqual(result["status"], "Error")

    def test_public_pages_cannot_reach_private_ones(self):
        real = url_guard._public_addresses
        port = self.site.port

        def fake(host, host_port):
            """Treat one made-up host as public; it is served by the test site."""
            if host == "public.test":
                return socket.getaddrinfo("127.0.0.1", port, type=socket.SOCK_STREAM)
            return real(host, host_port)

        self.site.hits.clear()
        with mock.patch.object(url_guard, "_public_addresses", fake):
            results = self.run_cases(
                {"page_url": "http://public.test/embeds", "automation_steps": [see("public page ok")]},
                {"page_url": "http://public.test/redirect", "automation_steps": [see("INTERNAL-DATA")]},
            )
            reply = requests.get("http://public.test/redirect", proxies=url_guard.request_proxies())
        self.assertEqual(results[0]["status"], "Pass", results[0]["log"])
        self.assertNotEqual(results[1]["status"], "Pass")
        self.assertEqual(reply.status_code, 403)
        self.assertEqual([h for h in self.site.hits if "secret" in h[1]], [])
