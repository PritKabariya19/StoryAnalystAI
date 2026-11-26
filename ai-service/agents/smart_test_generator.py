"""
Smart Test Generator Agent
----------------------------
AI-powered test case generation from a URL + natural-language query.

Workflow:
  1. Uses WebsiteExtractorAgent to deep-crawl the target URL
  2. Builds a structured prompt with the real page elements
  3. Sends to Gemini AI to generate comprehensive test cases
  4. Falls back to rule-based CombinedGeneratorAgent if AI fails

Output format is compatible with the existing TestExecutorAgent.
"""

import json
import logging
import re
import time
from typing import List, Dict
from urllib.parse import urljoin, urlparse

from agents import llm
from agents.url_guard import check_url, request_proxies
from agents.uploads import accepted_type, look_at_page
from agents.test_executor import is_runnable
from agents.website_extractor import WebsiteExtractorAgent
from agents.combined_generator import CombinedGeneratorAgent, enter_step, first_option

log = logging.getLogger(__name__)


# 8 testing types for Senior QA methodology
TEST_TYPES = ["UI", "Functional", "Validation", "Boundary", "Negative", "Security"]

MAX_TEST_CASES = 100
MAX_CRAWL_CHARS = 40_000
MAX_BROWSER_PAGES = 3  # pages also opened in Chromium; each takes several seconds

_TEST_GEN_SYSTEM_PROMPT = """You are a Senior QA Engineer who designs test cases from the web components of a real website.

Your job is to test every component found in the crawl data, with test cases grouped by component.

STEPS:
1. Break the user story into Actor / Action / Goal.
2. List every component in the crawl data: each form, and each field, button, toggle, message area, link and heading.
3. Map components to the user story. Components that serve the story come first and get the deepest coverage.
4. Generate test cases GROUPED BY COMPONENT covering these testing types: UI, Functional, Validation, Boundary, Negative, Security.

THE MAIN TASK COMES FIRST:
- Before testing single components, write tests that perform the page's whole task from start to finish and check its result, e.g. upload a file, click the convert button, check that the file is downloaded; or fill a form, submit it and check the outcome.
- A test must never stop at a click: it ends by checking what the task or the click produced.
- File upload (from "file_uploads"): the whole task with one file; with several files if "multiple" is true; with a file type the field does not accept.
- "after_upload" lists the controls that appeared after a sample file was uploaded. Use them for the steps after "upload", located by "id" when they have one.

WHAT TO TEST FOR EACH COMPONENT - write the checks that apply to the component and that the executor can verify:
- Form: visible, submit with all fields valid, submit with all fields empty.
- Every field: its label or input is visible and accepts a valid value.
- Required field: left empty while the other fields are valid.
- email: valid address, no "@", no domain, spaces only, very long address.
- password: valid, too short, wrong value, spaces only, very long; if there is a confirm field, matching and mismatching values.
- text, search, textarea: normal, spaces only, special characters, Unicode, very long, SQL injection string, script tag.
- tel, number, date, url: valid, wrong format, and values just inside and just outside any limit.
- "maxlength", "minlength", "min", "max" or "pattern" in the crawl data: test at the limit and just past it. Never assume a limit the crawl data does not show.
- select: each real option. radio: each choice. checkbox: ticked and unticked.
- Button: visible, and clicking it produces its visible result.
- Toggle or tab: clicking it shows the form or section it opens.
- Link (from "clickable_links"): clicking it leads to a visible result. Locate it by its text, e.g. xpath //a[normalize-space()='About'].
- Heading (from "text_headings"): the text is visible.

HOW MANY TEST CASES:
- Generate as many distinct test cases as the components justify, up to a maximum of {max_cases} in total.
- A page with many components should get close to {max_cases}. A small page gets fewer.
- NEVER pad: no two test cases may check the same behaviour of the same component with equivalent input.
- If there are more possible checks than the maximum, keep the ones that serve the user story and those with Critical or High priority.

WHAT THE EXECUTOR CAN DO — a test that needs anything else cannot be run, so do not write it:
- The only actions are "navigate", "input", "upload", "click" and "assert".
- "upload" puts generated sample files into the page's file field. Leave out "locator" unless the page has several file fields. Optional: "files" (1-5, default 1) and "file_type" ("jpg", "png", "pdf", "txt" or "csv"; default is an accepted type). It cannot make other types.
- A "click" or "assert" step that must wait for processing takes "wait": seconds (up to 90). Give the first click after an upload "wait": 60.
- "assert" with "type": "download" checks that a file was downloaded; "expected" is its extension, e.g. ".pdf".
- "assert" with "type": "hidden" checks that the element in "locator" is not visible. There is no hover, key press, focus check, wait or timing.
- "assert" with "type": "url" checks that the URL contains "expected".
- "assert" with "type": "element" and a "locator" checks that the element is VISIBLE and, if "expected" is not "", that its visible text or field value contains "expected".
- "assert" with "type": "text" and no locator checks that "expected" is visible text on the page. "expected" must be text the page really shows, never a description of behaviour.
- It cannot check that something is hidden, absent, disabled or has disappeared. Assert only what should be visible.
- It cannot check performance, tab order, focus, colours, layout, whether a password is masked, or anything stored on a server.

NEVER INVENT ELEMENTS OR MESSAGES:
- Use only elements listed in the crawl data ("forms" and "elements_outside_forms"). Never make up classes or selectors such as ".error-message" or ":invalid + .error".
- An element with "hidden": true cannot be used until it is shown. If a form is hidden, first click the button or link that shows it, usually one listed in "elements_outside_forms".
- Scripts usually write result messages into an element that is empty at first. "script_messages" lists the texts the page's scripts can show. Assert that element with the exact message this test should produce, so that a success message cannot pass a failure test, or the reverse. Only if no listed message fits, assert the element is visible with "expected": "".
- Every test starts on a fresh page with empty storage. No account or other record exists unless the same test creates it first.
- When the browser itself blocks a submit (a required field is empty, or type="email" is malformed) the page shows no element of its own. Assert "type": "text" with "expected": "Please fill out this field" for an empty required field, or "Please include an" for an email without "@".

RULES YOU MUST FOLLOW:
1. CLASSIFY every test case as one of:
   - positive   → valid input, expects SUCCESS
   - negative   → invalid/missing input, expects ERROR or REJECTION
   - edge_case  → boundary/unusual input, expects SPECIFIC HANDLING

2. SET EXPECTED RESULT based on type:
   - positive   → "System should allow / complete the action successfully"
   - negative   → "System should BLOCK the action and show an error message"
   - edge_case  → "System should handle gracefully without crashing"

CORE PRINCIPLES:
- Minimal Steps: navigate → input or upload → click → assert. No extra steps.
- Deterministic Execution: Every step must be fully defined and machine-executable.
- Fastest Locator Strategy: 1. id (fastest), 2. name, 3. css, 4. xpath (only if needed).
- Locator Accuracy: use type "id" only with an "id" value from the crawl data, and type "name" only with a "name" value. If an element has neither, use css built from what the crawl data shows (e.g. #formId button[type='submit']).
- Assertion Efficiency: every test ends with one assert that follows the rules above.
- Brevity: keep titles and steps short, so that all test cases fit in one response.
- Literal values only: JSON cannot hold code. Never write expressions such as "a".repeat(255) or "a" + "b". Type every value out in full; for a very long input, write exactly 60 characters unless the crawl data shows a larger limit.

RETURN ONLY valid JSON with this exact structure:
{
  "story_breakdown": { "actor": "...", "action": "...", "goal": "..." },
  "test_suites": [
    {
      "component": "<Component Name>",
      "test_cases": [
        {
          "tc_id": "TC_001",
          "title": "<test case title>",
          "type": "positive | negative | edge_case",
          "test_type": "<UI|Functional|Validation|Boundary|Negative|Security>",
          "test_steps": ["Step 1", "Step 2"],
          "expected_result": "<what the system SHOULD do>",
          "priority": "<Critical|High|Medium|Low>",
          "page_url": "<URL>",
          "automation_steps": [
            {"action": "navigate", "url": "https://example.com/login"},
            {"action": "input", "locator": {"type": "id|name|css|xpath", "value": "username"}, "value": "user@test.com"},
            {"action": "upload", "files": 1},
            {"action": "click", "locator": {"type": "css", "value": "button[type='submit']"}, "wait": 60},
            {"action": "assert", "type": "url|element|text|download|hidden", "locator": {"type": "id", "value": "<only for type element>"}, "expected": "<see the rules above>"}
          ],
          "mapped": true
        }
      ]
    }
  ]
}

Number "tc_id" in sequence across all components. Return no more than {max_cases} test cases. Use REAL element locators exactly matching the crawl data.
""".replace("{max_cases}", str(MAX_TEST_CASES))


# Despite the prompt, Gemini sometimes writes a long input as JavaScript, e.g.
# "a".repeat(255) + "@example.com", which makes the whole reply invalid JSON.
_JS_REPEAT = re.compile(r'"([^"\\]*)"\.repeat\((\d+)\)(?:\s*\+\s*"([^"\\]*)")?')


def _expand_repeat(m) -> str:
    return '"' + (m[1] * min(int(m[2]), 1000))[:1000] + (m[3] or "") + '"'


class SmartTestGeneratorAgent:
    """
    Generates comprehensive, AI-powered test cases from a URL + user query.
    Uses deep website extraction + Gemini AI for intelligent generation,
    with rule-based fallback.
    """

    def __init__(self):
        self._extractor = WebsiteExtractorAgent()
        self._fallback = CombinedGeneratorAgent()

    def generate(self, url: str, query: str = "", user_story: str = "", depth: int = 1) -> dict:
        """Main entry point. Returns component-grouped test suites."""
        # ── Step 1: Deep crawl ──────────────────────────────────────────
        try:
            extraction = self._extractor.extract(url, query="", depth=min(depth, 2))
        except Exception as exc:
            raise ValueError(f"Failed to crawl {url}: {str(exc)}")

        # ── Step 2: Parse user story ────────────────────────────────────
        story_breakdown = self._parse_story(user_story)

        # ── Step 3: Try AI generation ───────────────────────────────────
        test_suites = []
        ai_generated = False
        try:
            ai_result = self._ai_generate(extraction, query, user_story)
            if ai_result:
                if isinstance(ai_result, dict) and "test_suites" in ai_result:
                    test_suites = ai_result["test_suites"]
                    if "story_breakdown" in ai_result:
                        story_breakdown = ai_result["story_breakdown"]
                elif isinstance(ai_result, list):
                    test_suites = self._group_flat_cases(ai_result)
                if test_suites:
                    ai_generated = True
            if not test_suites:
                log.warning("Gemini returned no usable test cases; using rule-based tests.")
        except Exception:
            log.exception("Gemini generation failed; using rule-based tests.")

        # ── Step 4: Fallback to rule-based ──────────────────────────────
        if not test_suites:
            test_suites = self._rule_based_fallback(extraction, query, url)

        test_suites = self._limit_cases(self._runnable_only(test_suites), MAX_TEST_CASES)

        # The executor gets a flat list, so each case needs its suite's component
        for suite in test_suites:
            for tc in suite.get("test_cases", []):
                tc["component"] = tc.get("component") or suite["component"]

        # ── Step 5: Build summary ───────────────────────────────────────
        all_cases = [tc for suite in test_suites for tc in suite.get("test_cases", [])]
        type_counts = {}
        for tc in all_cases:
            t = tc.get("test_type", tc.get("type", "Functional"))
            type_counts[t] = type_counts.get(t, 0) + 1

        components = [s["component"] for s in test_suites]

        return {
            "url": url,
            "query": query,
            "user_story": user_story,
            "story_breakdown": story_breakdown,
            "components": components,
            "test_suites": test_suites,
            "summary": {
                "total": len(all_cases),
                "components": len(components),
                "by_type": type_counts,
                "by_priority": self._count_priorities(all_cases),
            },
            "ai_generated": ai_generated,
            "pages_crawled": extraction.get("aggregated", {}).get("pages_crawled", 0),
        }

    @staticmethod
    def _parse_story(story: str) -> dict:
        """Extract Actor / Action / Goal from a user story."""
        if not story:
            return {"actor": "User", "action": "interact with the website", "goal": "complete the desired task"}
        s = story.strip()
        # "As a <actor>, I want to <action> so that <goal>"
        m = re.match(r"[Aa]s\s+(?:a|an)\s+(.+?),?\s+I\s+want\s+(?:to\s+)?(.+?)(?:\s+so\s+that\s+(.+))?\.?$", s, re.DOTALL)
        if m:
            return {"actor": m.group(1).strip(), "action": m.group(2).strip(), "goal": (m.group(3) or "the feature works correctly").strip()}
        return {"actor": "User", "action": s[:120], "goal": "the feature works as expected"}

    @staticmethod
    def _count_priorities(cases: list) -> dict:
        counts = {}
        for tc in cases:
            p = tc.get("priority", "Medium")
            counts[p] = counts.get(p, 0) + 1
        return counts

    @staticmethod
    def _runnable_only(suites: list) -> list:
        """Drop cases the executor could only report as "not verified"."""
        kept = []
        for suite in suites:
            cases = [tc for tc in suite.get("test_cases", []) if is_runnable(tc)]
            if cases:
                kept.append({**suite, "test_cases": cases})
        return kept

    @staticmethod
    def _limit_cases(suites: list, limit: int) -> list:
        """Keep at most `limit` cases overall, dropping emptied suites."""
        kept = []
        for suite in suites:
            cases = suite.get("test_cases", [])[:limit]
            if cases:
                kept.append({**suite, "test_cases": cases})
                limit -= len(cases)
        return kept

    @staticmethod
    def _group_flat_cases(cases: list) -> list:
        """Group a flat list of test cases by component."""
        groups = {}
        for tc in cases:
            comp = tc.get("component", tc.get("feature", "General"))
            groups.setdefault(comp, []).append(tc)
        return [{"component": comp, "test_cases": tcs} for comp, tcs in groups.items()]

    # ── AI Generation ──────────────────────────────────────────────────

    def _ai_generate(self, extraction: dict, query: str, user_story: str = ""):
        """Send crawled data + query + user story to Gemini."""
        crawl_summary = self._build_crawl_context(extraction)

        story_part = f"USER STORY: \"{user_story}\"\n\n" if user_story else ""
        user_prompt = (
            f"{story_part}"
            f"USER QUERY: \"{query or 'Generate comprehensive test cases for all features'}\"\n\n"
            f"WEBSITE CRAWL DATA:\n```json\n{crawl_summary}\n```\n\n"
            "Generate component-grouped test suites. Return ONLY valid JSON."
        )

        # Retry logic
        wait = 3
        for attempt in range(2):
            try:
                raw = llm.generate(user_prompt, _TEST_GEN_SYSTEM_PROMPT, as_json=True)
                return self._parse_test_cases(raw)
            except Exception as exc:
                err = str(exc)
                if ("429" in err or "quota" in err.lower()) and attempt < 1:
                    time.sleep(wait)
                    wait *= 2
                    continue
                raise

        return []

    @staticmethod
    def _locator_attrs(tag) -> dict:
        """The element's real id / name attributes. Never substitute one for
        the other: the AI builds its locators from these keys."""
        return {key: tag.get(key) for key in ("id", "name") if tag.get(key)}

    @staticmethod
    def _type_info(tag) -> dict:
        """Field type; for a dropdown, also the options it offers."""
        if tag.name != "select":
            field_type = tag.get("type", "text")
            keys = ["maxlength", "minlength", "min", "max", "pattern"]
            if field_type in ("radio", "checkbox"):
                keys.append("value")
            return {"type": field_type, **{k: tag.get(k) for k in keys if tag.get(k) is not None}}
        options = [
            {"value": o.get("value", o.get_text(strip=True)), "text": o.get_text(strip=True)}
            for o in tag.find_all("option") if not o.has_attr("disabled")
        ]
        return {"type": "select", "options": options[:20]}

    @staticmethod
    def _is_hidden(tag) -> bool:
        """Hidden in the HTML itself (attribute, utility class or inline style),
        on the tag or an ancestor. CSS and scripts are not evaluated."""
        for node in [tag, *tag.parents]:
            if not getattr(node, "attrs", None):
                continue
            style = str(node.get("style", "")).replace(" ", "").lower()
            if (node.has_attr("hidden") or "display:none" in style
                    or {"hidden", "d-none"} & set(node.get("class") or [])):
                return True
        return False

    @staticmethod
    def _script_messages(soup) -> list:
        """Texts that inline scripts write into the page or show in alerts, so
        assertions can name the exact message."""
        pattern = re.compile(
            r"""(?:(?:innerText|textContent)\s*=|\balert\()\s*(["'`])((?:(?!\1)[^\n$]){4,120})\1""")
        found = []
        for script in soup.find_all("script", src=False):
            found += [m.group(2) for m in pattern.finditer(script.get_text())]
        return list(dict.fromkeys(found))[:25]

    def _outside_elements(self, soup) -> list:
        """Buttons and id'd elements that are not part of a form: toggles that
        open forms, and the places where scripts write messages."""
        found = []
        for tag in soup.find_all(True):
            if tag.name in ("form", "script", "style", "meta", "link", "html", "head", "body"):
                continue
            if not (tag.get("id") or tag.name == "button"):
                continue
            if tag.find_parent("form") or tag.find(["form", "input", "button"]):
                continue
            found.append({
                **self._locator_attrs(tag),
                "tag": tag.name,
                "text": tag.get_text(strip=True)[:60],
                **({"hidden": True} if self._is_hidden(tag) else {}),
            })
            if len(found) >= 30:
                break
        return found

    def _build_crawl_context(self, extraction: dict) -> str:
        """Build a compact JSON context from crawl data for the AI prompt."""
        agg = extraction.get("aggregated", {})
        pages = extraction.get("pages", [])

        context = {
            "start_url": extraction.get("start_url", ""),
            "pages_crawled": agg.get("pages_crawled", 0),
            "pages": [],
        }

        for index, page in enumerate(pages):
            if "error" in page:
                continue

            page_info = {
                "url": page.get("url", ""),
                "title": page.get("metadata", {}).get("title", ""),
            }

            # Forms — critical for test generation
            # Extract from the raw website_explorer data or from text
            forms = []
            # Look for form data in the page structure
            # The extractor stores raw text; we need to re-parse for forms
            # We'll build form info from the page-level data
            page_info["text_headings"] = page.get("text", {}).get("headings", {})

            # Rebuild form info from the page — re-scrape for form fields
            try:
                import requests
                from bs4 import BeautifulSoup
                check_url(page.get("url", ""))
                resp = requests.get(
                    page.get("url", ""),
                    headers=self._extractor.HEADERS,
                    timeout=8, verify=False,
                    proxies=request_proxies(),
                )
                seen = look_at_page(page.get("url", "")) if index < MAX_BROWSER_PAGES else None
                if seen:
                    page_info["file_uploads"] = seen["file_uploads"]
                    page_info["after_upload"] = seen["after_upload"]
                soup = BeautifulSoup(seen["html"] if seen else resp.text, "html.parser")
                for form_tag in soup.find_all("form"):
                    form_data = {
                        **self._locator_attrs(form_tag),
                        "action": form_tag.get("action", ""),
                        "method": form_tag.get("method", "GET"),
                        **({"hidden": True} if self._is_hidden(form_tag) else {}),
                        "fields": [],
                        "buttons": [],
                    }
                    for inp in form_tag.find_all(["input", "select", "textarea"]):
                        type_info = self._type_info(inp)
                        if type_info["type"] in ("hidden", "submit"):
                            continue
                        form_data["fields"].append({
                            **self._locator_attrs(inp),
                            **type_info,
                            "placeholder": inp.get("placeholder", ""),
                            "required": inp.has_attr("required"),
                        })
                    for btn in form_tag.find_all(["button", "input"]):
                        if btn.name == "input" and btn.get("type") == "submit":
                            form_data["buttons"].append({
                                **self._locator_attrs(btn),
                                "text": btn.get("value", "Submit"),
                                "type": "submit",
                            })
                        elif btn.name == "button":
                            form_data["buttons"].append({
                                **self._locator_attrs(btn),
                                "text": btn.get_text(strip=True) or "Submit",
                                "type": btn.get("type", "submit"),
                            })
                    if form_data["fields"] or form_data["buttons"]:
                        forms.append(form_data)

                # Also find standalone clickable elements
                links_on_page = []
                for a in soup.find_all("a", href=True):
                    text = a.get_text(strip=True)
                    if text and len(text) < 50:
                        links_on_page.append(text)
                page_info["clickable_links"] = links_on_page[:15]
                page_info["elements_outside_forms"] = self._outside_elements(soup)
                page_info["script_messages"] = self._script_messages(soup)

            except Exception:
                pass

            page_info["forms"] = forms
            page_info["contact_info"] = page.get("contact_info", {})

            context["pages"].append(page_info)

        raw = json.dumps(context, ensure_ascii=False, default=str)
        # Cap the size to stay within token limits
        if len(raw) > MAX_CRAWL_CHARS:
            raw = raw[:MAX_CRAWL_CHARS] + "...(truncated)"
        return raw

    def _parse_test_cases(self, raw: str):
        """Parse AI response — handles both grouped and flat formats."""
        cleaned = re.sub(r"```(?:json)?", "", raw).strip("`").strip()
        cleaned = _JS_REPEAT.sub(_expand_repeat, cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    m2 = re.search(r"\[.*\]", cleaned, re.DOTALL)
                    if m2:
                        try:
                            data = json.loads(m2.group())
                        except json.JSONDecodeError:
                            return []
                    else:
                        return []
            else:
                return []

        # New grouped format: {"test_suites": [...], "story_breakdown": {...}}
        if isinstance(data, dict) and "test_suites" in data:
            return data

        # Flat array of test cases
        if isinstance(data, list):
            return data

        # Dict wrapping a flat array
        if isinstance(data, dict):
            for key in ("test_cases", "tests", "testCases"):
                if key in data and isinstance(data[key], list):
                    return data[key]

        return []

    # ── Component-aware rule-based fallback ────────────────────────────

    # Realistic test values by field type
    _VALID_VALUES = {
        "email": "testuser@example.com", "password": "ValidPass@123",
        "tel": "9876543210", "number": "42", "text": "Test Value",
        "url": "https://example.com", "date": "2026-01-15",
        "checkbox": "true",
    }
    _INVALID_VALUES = {
        "email": "not-an-email", "password": "123", "tel": "abc",
        "number": "xyz", "text": "", "url": "not-a-url", "date": "invalid",
    }

    def _scrape_page_components(self, page_url: str) -> dict:
        """Scrape a page and return its forms, buttons, and links."""
        import requests
        from bs4 import BeautifulSoup
        try:
            check_url(page_url)
            resp = requests.get(page_url, headers=self._extractor.HEADERS,
                                timeout=8, verify=False,
                                proxies=request_proxies())
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception:
            return {"forms": [], "links": [], "title": "Page"}

        title = ""
        t = soup.find("title")
        if t:
            title = t.get_text(strip=True)

        forms = []
        for form_tag in soup.find_all("form"):
            form = {
                "name": (form_tag.get("id") or form_tag.get("name")
                         or (form_tag.get("class") or ["form"])[0]),
                "action": form_tag.get("action", ""),
                "fields": [], "buttons": [],
            }
            for inp in form_tag.find_all(["input", "select", "textarea"]):
                type_info = self._type_info(inp)
                if type_info["type"] in ("hidden",):
                    continue
                name = inp.get("name") or inp.get("id") or inp.get("placeholder", "field")
                form["fields"].append({
                    "name": name, **type_info,
                    "placeholder": inp.get("placeholder", ""),
                    "required": inp.has_attr("required"),
                    "selector": f"#{inp.get('id')}" if inp.get("id") else f"[name='{inp.get('name', '')}']",
                })
            for btn in form_tag.find_all(["button", "input"]):
                if btn.name == "input" and btn.get("type") == "submit":
                    form["buttons"].append({"text": btn.get("value", "Submit")})
                elif btn.name == "button":
                    form["buttons"].append({"text": btn.get_text(strip=True) or "Submit"})
            if form["fields"] or form["buttons"]:
                forms.append(form)

        links = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            target = urlparse(urljoin(page_url, a["href"]))
            # A new-tab link leaves this page's URL unchanged
            if (text and len(text) < 60 and not a["href"].startswith("#")
                    and target.scheme in ("http", "https") and a.get("target") != "_blank"):
                links.append({"text": text, "expected_url": target.path.rstrip("/") or target.netloc})
            if len(links) >= 15:
                break

        return {"forms": forms, "links": links, "title": title}

    def _pick_valid(self, fname: str, ftype: str, options: list = None) -> str:
        # Type first: a checkbox named "email_updates" must not get an email
        if ftype == "checkbox": return self._VALID_VALUES["checkbox"]
        if ftype == "select": return first_option(options)
        fl = fname.lower()
        if "email" in fl or ftype == "email": return self._VALID_VALUES["email"]
        if "pass" in fl or ftype == "password": return self._VALID_VALUES["password"]
        if "phone" in fl or "tel" in fl or ftype == "tel": return self._VALID_VALUES["tel"]
        if "name" in fl: return "John Doe"
        if "user" in fl: return "testuser"
        if "search" in fl or "query" in fl: return "test search query"
        return self._VALID_VALUES.get(ftype, f"{fname}_test_value")

    def _pick_invalid(self, fname: str, ftype: str) -> str:
        fl = fname.lower()
        if "email" in fl or ftype == "email": return "not-an-email"
        if "pass" in fl or ftype == "password": return "x"
        if "phone" in fl or "tel" in fl or ftype == "tel": return "abc"
        return self._INVALID_VALUES.get(ftype, "invalid_value")

    def _rule_based_fallback(self, extraction: dict, query: str, url: str) -> list:
        """Component-grouped test suites covering the testing types the executor
        can check. Performance and usability are not among them."""
        pages = extraction.get("pages", [])
        suites = {}  # component_name -> list of test cases
        tc_idx = 1
        feature = query or "Website Feature Testing"

        for index, page in enumerate(pages):
            if "error" in page:
                continue
            page_url = page.get("url", url)
            comp = self._scrape_page_components(page_url)
            seen = look_at_page(page_url) if index < MAX_BROWSER_PAGES else None
            if seen and seen["file_uploads"]:
                cases = self._upload_cases(tc_idx, page_url, seen)
                if cases:
                    suites.setdefault("File Upload", []).extend(cases)
                    tc_idx += len(cases)
            page_title = comp.get("title", "Page")
            forms = comp.get("forms", [])
            links = comp.get("links", [])

            for form in forms:
                fname = form.get("name", "form")
                fields = form.get("fields", [])
                btn_text = form["buttons"][0]["text"] if form.get("buttons") else "Submit"
                if not fields:
                    continue
                comp_name = f"{fname} Form"
                text_flds = [f for f in fields if f["type"] in ("text","email","password","tel","search")]
                target = text_flds[0] if text_flds else fields[0]

                cases = []

                # UI Testing
                cases.append(self._tc2(tc_idx, comp_name, "UI", f"Verify {fname} form is visible on page",
                    [f"Navigate to {page_url}", f"Check {fname} form is displayed"],
                    f"Form with all fields and '{btn_text}' button is visible", "High", page_url,
                    [f"Open browser and navigate to '{page_url}'.", f"Assert that the page/response reflects: '{fname} form is visible'."]))
                tc_idx += 1

                # Functional - valid submit
                steps = [f"Navigate to {page_url}"]
                auto = [f"Open browser and navigate to '{page_url}'."]
                for fld in fields:
                    v = self._pick_valid(fld["name"], fld["type"], fld.get("options"))
                    steps.append(f"Enter '{v}' in '{fld['name']}'")
                    auto.append(enter_step(v, fld["name"]))
                steps.append(f"Click '{btn_text}'")
                auto += [f"Find button with text '{btn_text}' and click().",
                         "Assert that the page/response reflects: 'form submitted successfully'."]
                cases.append(self._tc2(tc_idx, comp_name, "Functional", "Submit form with all valid data",
                    steps, "Form submits successfully", "Critical", page_url, auto))
                tc_idx += 1

                # Validation - empty fields
                auto2 = [f"Open browser and navigate to '{page_url}'."]
                for fld in fields:
                    auto2.append(f"Clear the '{fld['name']}' field.")
                auto2 += [f"Find button with text '{btn_text}' and click().",
                          "Assert that the page/response reflects: 'validation error displayed'."]
                cases.append(self._tc2(tc_idx, comp_name, "Validation", "Submit form with all empty fields",
                    [f"Navigate to {page_url}", "Leave all fields empty", f"Click '{btn_text}'"],
                    "Validation errors shown for required fields", "Critical", page_url, auto2))
                tc_idx += 1

                # Validation - per-field invalid
                for fld in fields:
                    if fld["type"] in ("checkbox","select","radio","submit"):
                        continue
                    inv = self._pick_invalid(fld["name"], fld["type"])
                    auto3 = [f"Open browser and navigate to '{page_url}'."]
                    for f2 in fields:
                        if f2["name"] == fld["name"]:
                            auto3.append(enter_step(inv, f2["name"]))
                        else:
                            auto3.append(enter_step(
                                self._pick_valid(f2["name"], f2["type"], f2.get("options")), f2["name"]))
                    auto3 += [f"Find button with text '{btn_text}' and click().",
                              f"Assert that the page/response reflects: 'error for {fld['name']}'."]
                    cases.append(self._tc2(tc_idx, comp_name, "Validation",
                        f"Invalid data in '{fld['name']}' field",
                        [f"Navigate to {page_url}", f"Enter '{inv}' in '{fld['name']}'", f"Click '{btn_text}'"],
                        f"Error message for '{fld['name']}' is shown", "High", page_url, auto3))
                    tc_idx += 1

                # Negative - empty per-field
                for fld in fields:
                    if fld["type"] in ("checkbox","select","radio","submit"):
                        continue
                    auto4 = [f"Open browser and navigate to '{page_url}'."]
                    for f2 in fields:
                        if f2["name"] == fld["name"]:
                            auto4.append(f"Clear the '{f2['name']}' field.")
                        else:
                            auto4.append(enter_step(
                                self._pick_valid(f2["name"], f2["type"], f2.get("options")), f2["name"]))
                    auto4 += [f"Find button with text '{btn_text}' and click().",
                              f"Assert that the page/response reflects: '{fld['name']} is required'."]
                    cases.append(self._tc2(tc_idx, comp_name, "Negative",
                        f"Empty '{fld['name']}' with other fields valid",
                        [f"Navigate to {page_url}", f"Leave '{fld['name']}' empty, fill others", f"Click '{btn_text}'"],
                        f"Required error for '{fld['name']}'", "High", page_url, auto4))
                    tc_idx += 1

                # Boundary - very long input
                if target:
                    cases.append(self._tc2(tc_idx, comp_name, "Boundary",
                        f"300-char input in '{target['name']}'",
                        [f"Navigate to {page_url}", f"Enter 300 chars in '{target['name']}'", f"Click '{btn_text}'"],
                        "Input accepted or max length enforced", "Medium", page_url,
                        [f"Open browser and navigate to '{page_url}'.",
                         f"Enter '{'A'*300}' in the '{target['name']}' field.",
                         f"Find button with text '{btn_text}' and click().",
                         "Assert that the page/response reflects: 'max length enforced or accepted'."]))
                    tc_idx += 1

                # Security - SQL Injection
                if target:
                    cases.append(self._tc2(tc_idx, comp_name, "Security",
                        f"SQL injection in '{target['name']}'",
                        [f"Navigate to {page_url}", f"Enter \"' OR '1'='1\" in '{target['name']}'", f"Click '{btn_text}'"],
                        "Input rejected or sanitized, no DB error exposed", "High", page_url,
                        [f"Open browser and navigate to '{page_url}'.",
                         enter_step("' OR '1'='1", target["name"]),
                         f"Find button with text '{btn_text}' and click().",
                         "Assert that the page/response reflects: 'input rejected or sanitized'."]))
                    tc_idx += 1

                # Security - XSS
                if target:
                    cases.append(self._tc2(tc_idx, comp_name, "Security",
                        f"XSS payload in '{target['name']}'",
                        [f"Navigate to {page_url}", f"Enter '<script>alert(1)</script>' in '{target['name']}'", f"Click '{btn_text}'"],
                        "Script not executed, input sanitized", "High", page_url,
                        [f"Open browser and navigate to '{page_url}'.",
                         f"Enter '<script>alert(1)</script>' in the '{target['name']}' field.",
                         f"Find button with text '{btn_text}' and click().",
                         "Assert that the page/response reflects: 'script not executed, sanitized'."]))
                    tc_idx += 1

                suites.setdefault(comp_name, []).extend(cases)

            # Navigation component
            if links:
                nav_cases = []
                for link in links[:5]:
                    nav_cases.append(self._tc2(tc_idx, "Navigation", "Functional",
                        f"Click '{link['text']}' link",
                        [f"Navigate to {page_url}", f"Click '{link['text']}' link"],
                        f"URL contains '{link['expected_url']}'", "Medium", page_url,
                        [f"Open browser and navigate to '{page_url}'.",
                         f"Find link or button with text '{link['text']}' and click().",
                         f"Assert that the URL contains '{link['expected_url']}'."]))
                    tc_idx += 1
                suites.setdefault("Navigation", []).extend(nav_cases)

            if len([tc for tcs in suites.values() for tc in tcs]) >= MAX_TEST_CASES:
                break

        # Fallback if nothing found
        if not suites:
            suites["Page Load"] = [
                self._tc2(1, "Page Load", "Functional", "Page loads successfully",
                    [f"Navigate to {url}", "Verify page loads"],
                    "Page loads successfully", "Critical", url,
                    [f"Open browser and navigate to '{url}'.", "Assert that the page/response reflects: 'page loaded'."]),
                self._tc2(2, "Page Load", "Negative", "Invalid URL path returns error",
                    [f"Navigate to {url}/nonexistent", "Verify error page"],
                    "404 or error page displayed", "Medium", url + "/nonexistent",
                    [f"Open browser and navigate to '{url}/nonexistent'.", "Assert that the page/response reflects: '404 or error page'."]),
            ]

        return [{"component": comp, "test_cases": tcs} for comp, tcs in suites.items()]

    # Words on a button that runs the task on an uploaded file
    _TASK_BUTTON = re.compile(r"\b(convert|merge|compress|split|rotate|upload|process|submit)\b", re.I)

    def _upload_cases(self, idx: int, page_url: str, seen: dict) -> list:
        """Tests that perform the whole upload task. Needs a task button among
        the controls that appeared after the sample upload."""
        field = seen["file_uploads"][0]
        button = next((c for c in seen["after_upload"]
                       if c["id"] and self._TASK_BUTTON.search(c["text"])), None)
        if not button or not accepted_type(field["accept"]):
            return []
        text = button["text"]
        extension = ".pdf" if re.search(r"\bto pdf\b", text, re.I) else ""
        locator = {"type": "id", "value": button["id"]}
        start = {"action": "navigate", "url": page_url}

        def task(files):
            return [start, {"action": "upload", "files": files},
                    {"action": "click", "locator": locator, "wait": 60},
                    {"action": "assert", "type": "download", "expected": extension}]

        def case(offset, test_type, title, steps, expected, automation):
            return self._tc2(idx + offset, "File Upload", test_type, title,
                             steps, expected, "Critical", page_url, automation)

        result = f"A {extension or 'result'} file is downloaded"
        cases = [case(0, "Functional", f"Upload one file and click '{text}'",
                      [f"Navigate to {page_url}", "Upload one file", f"Click '{text}'", result],
                      result, task(1))]
        if field["multiple"]:
            cases.append(case(len(cases), "Functional", f"Upload two files and click '{text}'",
                              [f"Navigate to {page_url}", "Upload two files", f"Click '{text}'", result],
                              result, task(2)))
        accepted = field["accept"].lower()
        if accepted and "txt" not in accepted and "text" not in accepted:
            blocked = f"'{text}' is not offered"
            cases.append(case(len(cases), "Negative", "Upload a file type that is not accepted",
                              [f"Navigate to {page_url}", "Upload a .txt file", blocked], blocked,
                              [start, {"action": "upload", "file_type": "txt"},
                               {"action": "assert", "type": "hidden", "locator": locator, "wait": 5}]))
        return cases

    def _tc2(self, idx, component, test_type, scenario, steps, expected, priority, page_url, auto_steps):
        """Build a test case dict in the new grouped format.

        Emits both ``test_type`` (human-readable category label) and ``type``
        (execution-polarity field consumed by TestExecutorAgent).  The executor
        uses ``type`` to decide whether a test is positive/negative/edge_case
        so it must be present for every test case, regardless of generator path.
        """
        # Map test_type label → execution polarity understood by the executor
        _POLARITY = {
            "Negative": "negative",
            "Security": "negative",   # security tests expect rejection
            "Boundary": "edge_case",
            "Validation": "negative", # validation tests expect error messages
        }
        polarity = _POLARITY.get(test_type, "positive")
        return {
            "tc_id": f"TC_{idx:02d}", "component": component,
            "test_type": test_type, "type": polarity,
            "test_scenario": scenario,
            "test_steps": steps, "expected_result": expected,
            "priority": priority, "page_url": page_url,
            "automation_steps": auto_steps, "mapped": True,
        }
