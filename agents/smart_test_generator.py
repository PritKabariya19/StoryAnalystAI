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
import re
import time
from typing import List, Dict

import google.generativeai as genai

from config import GEMINI_API_KEY, GEMINI_MODEL
from agents.website_extractor import WebsiteExtractorAgent
from agents.combined_generator import CombinedGeneratorAgent

genai.configure(api_key=GEMINI_API_KEY)


# 8 testing types for Senior QA methodology
TEST_TYPES = ["UI", "Functional", "Validation", "Boundary", "Negative", "Security", "Performance", "Usability"]

_TEST_GEN_SYSTEM_PROMPT = """You are an expert QA Performance Engineer optimizing for extreme speed and efficiency (Antigravity Mode).

Your job is to generate detailed, accurate test cases for any given feature or functionality, grouped by components.

STEPS:
1. Break the user story into Actor / Action / Goal.
2. Identify website components from crawl data.
3. Map components to the user story.
4. Generate test cases GROUPED BY COMPONENT covering ALL 8 testing types: UI, Functional, Validation, Boundary, Negative, Security, Performance, Usability.

RULES YOU MUST FOLLOW:
1. CLASSIFY every test case as one of:
   - positive   → valid input, expects SUCCESS
   - negative   → invalid/missing input, expects ERROR or REJECTION
   - edge_case  → boundary/unusual input, expects SPECIFIC HANDLING

2. SET EXPECTED RESULT based on type:
   - positive   → "System should allow / complete the action successfully"
   - negative   → "System should BLOCK the action and show an error message"
   - edge_case  → "System should handle gracefully without crashing"

3. PASS/FAIL RULE:
   - PASS = actual behavior MATCHES expected behavior
   - FAIL = actual behavior does NOT match expected behavior
   - ⚠️ NEVER mark a test FAIL just because the action did not complete
   - For NEGATIVE test cases: If system REJECTS → PASS. If system ALLOWS → FAIL.

⚡ CORE PRINCIPLES (ANTIGRAVITY MODE):
- Minimal Steps: navigate → input → click → assert. No extra steps.
- Deterministic Execution: Every step must be fully defined and machine-executable.
- Fastest Locator Strategy: 1. id (fastest), 2. name, 3. css, 4. xpath (only if needed).
- Assertion Efficiency: For negative tests, the assert expected value MUST be the error message text.

RETURN ONLY valid JSON with this exact structure:
{
  "story_breakdown": { "actor": "...", "action": "...", "goal": "..." },
  "test_suites": [
    {
      "component": "<Component Name>",
      "test_cases": [
        {
          "tc_id": "TC_01",
          "title": "<test case title>",
          "type": "positive | negative | edge_case",
          "test_type": "<UI|Functional|Validation|Boundary|Negative|Security|Performance|Usability>",
          "preconditions": "<any setup needed>",
          "test_steps": ["Step 1", "Step 2"],
          "input_data": "<what the user enters>",
          "expected_result": "<what the system SHOULD do>",
          "pass_condition": "<when to mark this PASS>",
          "fail_condition": "<when to mark this FAIL>",
          "status": "NOT RUN",
          "priority": "<Critical|High|Medium|Low>",
          "page_url": "<URL>",
          "automation_steps": [
            {"action": "navigate", "url": "https://example.com/login"},
            {"action": "input", "locator": {"type": "id|name|css|xpath", "value": "username"}, "value": "user@test.com"},
            {"action": "click", "locator": {"type": "css", "value": "button[type='submit']"}},
            {"action": "assert", "type": "url|element", "expected": "/dashboard OR error message"}
          ],
          "mapped": true
        }
      ]
    }
  ]
}

Generate 15-30 test cases total across all components. Use REAL element locators exactly matching the crawl data.
"""


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
        except Exception:
            pass

        # ── Step 4: Fallback to rule-based ──────────────────────────────
        if not test_suites:
            test_suites = self._rule_based_fallback(extraction, query, url)

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

        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=_TEST_GEN_SYSTEM_PROMPT,
        )

        # Retry logic
        wait = 3
        for attempt in range(2):
            try:
                response = model.generate_content(user_prompt)
                raw = response.text.strip()
                return self._parse_test_cases(raw)
            except Exception as exc:
                err = str(exc)
                if ("429" in err or "quota" in err.lower()) and attempt < 1:
                    time.sleep(wait)
                    wait *= 2
                    continue
                raise

        return []

    def _build_crawl_context(self, extraction: dict) -> str:
        """Build a compact JSON context from crawl data for the AI prompt."""
        agg = extraction.get("aggregated", {})
        pages = extraction.get("pages", [])

        context = {
            "start_url": extraction.get("start_url", ""),
            "pages_crawled": agg.get("pages_crawled", 0),
            "pages": [],
        }

        for page in pages:
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
                resp = requests.get(
                    page.get("url", ""),
                    headers=self._extractor.HEADERS,
                    timeout=8, verify=False,
                )
                soup = BeautifulSoup(resp.text, "html.parser")
                for form_tag in soup.find_all("form"):
                    form_data = {
                        "name": form_tag.get("id") or form_tag.get("name") or form_tag.get("action", "form"),
                        "action": form_tag.get("action", ""),
                        "method": form_tag.get("method", "GET"),
                        "fields": [],
                        "buttons": [],
                    }
                    for inp in form_tag.find_all(["input", "select", "textarea"]):
                        inp_type = inp.get("type", "text")
                        if inp_type in ("hidden", "submit"):
                            continue
                        form_data["fields"].append({
                            "name": inp.get("name") or inp.get("id") or inp.get("placeholder", "field"),
                            "type": inp_type,
                            "placeholder": inp.get("placeholder", ""),
                            "required": inp.has_attr("required"),
                        })
                    for btn in form_tag.find_all(["button", "input"]):
                        if btn.name == "input" and btn.get("type") == "submit":
                            form_data["buttons"].append({
                                "text": btn.get("value", "Submit"),
                                "type": "submit",
                            })
                        elif btn.name == "button":
                            form_data["buttons"].append({
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

            except Exception:
                pass

            page_info["forms"] = forms
            page_info["contact_info"] = page.get("contact_info", {})

            context["pages"].append(page_info)

        raw = json.dumps(context, ensure_ascii=False, default=str)
        # Cap to ~6000 chars to stay within token limits
        if len(raw) > 6000:
            raw = raw[:6000] + "...(truncated)"
        return raw

    def _parse_test_cases(self, raw: str):
        """Parse AI response — handles both grouped and flat formats."""
        cleaned = re.sub(r"```(?:json)?", "", raw).strip("`").strip()

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
        "checkbox": "check the checkbox", "select": "select a valid option",
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
            resp = requests.get(page_url, headers=self._extractor.HEADERS,
                                timeout=8, verify=False)
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
                "name": form_tag.get("id") or form_tag.get("name") or form_tag.get("class", ["form"])[0] if form_tag.get("class") else "form",
                "action": form_tag.get("action", ""),
                "fields": [], "buttons": [],
            }
            for inp in form_tag.find_all(["input", "select", "textarea"]):
                itype = inp.get("type", "text")
                if itype in ("hidden",):
                    continue
                name = inp.get("name") or inp.get("id") or inp.get("placeholder", "field")
                form["fields"].append({
                    "name": name, "type": itype,
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
            if text and len(text) < 60 and not a["href"].startswith(("#", "javascript:")):
                links.append({"text": text, "href": a["href"]})
            if len(links) >= 15:
                break

        return {"forms": forms, "links": links, "title": title}

    def _pick_valid(self, fname: str, ftype: str) -> str:
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
        """Component-grouped test suites covering 8 testing types."""
        pages = extraction.get("pages", [])
        suites = {}  # component_name -> list of test cases
        tc_idx = 1
        feature = query or "Website Feature Testing"

        for page in pages:
            if "error" in page:
                continue
            page_url = page.get("url", url)
            comp = self._scrape_page_components(page_url)
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
                    v = self._pick_valid(fld["name"], fld["type"])
                    steps.append(f"Enter '{v}' in '{fld['name']}'")
                    auto.append(f"Enter '{v}' in the '{fld['name']}' field.")
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
                            auto3.append(f"Enter '{inv}' in the '{f2['name']}' field.")
                        else:
                            auto3.append(f"Enter '{self._pick_valid(f2['name'], f2['type'])}' in the '{f2['name']}' field.")
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
                            auto4.append(f"Enter '{self._pick_valid(f2['name'], f2['type'])}' in the '{f2['name']}' field.")
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
                         f"Enter '\\' OR \\'1\\'=\\'1' in the '{target['name']}' field.",
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

                # Performance
                cases.append(self._tc2(tc_idx, comp_name, "Performance",
                    f"{fname} form submission response time",
                    [f"Navigate to {page_url}", "Fill all fields with valid data", f"Click '{btn_text}'", "Measure response time"],
                    "Form submission completes within 3 seconds", "Medium", page_url,
                    [f"Open browser and navigate to '{page_url}'.",
                     f"Assert that the page/response reflects: 'response within 3 seconds'."]))
                tc_idx += 1

                # Usability
                cases.append(self._tc2(tc_idx, comp_name, "Usability",
                    f"Tab navigation through {fname} form fields",
                    [f"Navigate to {page_url}", "Press Tab to move through each field", "Verify logical tab order"],
                    "Focus moves through fields in logical order", "Low", page_url,
                    [f"Open browser and navigate to '{page_url}'.",
                     "Assert that the page/response reflects: 'tab order is logical'."]))
                tc_idx += 1

                suites.setdefault(comp_name, []).extend(cases)

            # Navigation component
            if links:
                nav_cases = []
                for link in links[:5]:
                    nav_cases.append(self._tc2(tc_idx, "Navigation", "Functional",
                        f"Click '{link['text']}' link",
                        [f"Navigate to {page_url}", f"Click '{link['text']}' link"],
                        "Target page loads successfully", "Medium", page_url,
                        [f"Open browser and navigate to '{page_url}'.",
                         f"Find link or button with text '{link['text']}' and click().",
                         "Assert that the page/response reflects: 'page loaded successfully'."]))
                    tc_idx += 1
                # UI test for navigation
                nav_cases.append(self._tc2(tc_idx, "Navigation", "UI",
                    "Navigation menu is visible and styled",
                    [f"Navigate to {page_url}", "Check nav menu is visible"],
                    "Navigation links are displayed with proper styling", "Medium", page_url,
                    [f"Open browser and navigate to '{page_url}'.",
                     "Assert that the page/response reflects: 'navigation menu visible'."]))
                tc_idx += 1
                suites.setdefault("Navigation", []).extend(nav_cases)

            if len([tc for tcs in suites.values() for tc in tcs]) >= 30:
                break

        # Fallback if nothing found
        if not suites:
            suites["Page Load"] = [
                self._tc2(1, "Page Load", "Functional", "Page loads successfully",
                    [f"Navigate to {url}", "Verify page loads"],
                    "Page loads within 3 seconds", "Critical", url,
                    [f"Open browser and navigate to '{url}'.", "Assert that the page/response reflects: 'page loaded'."]),
                self._tc2(2, "Page Load", "Negative", "Invalid URL path returns error",
                    [f"Navigate to {url}/nonexistent", "Verify error page"],
                    "404 or error page displayed", "Medium", url + "/nonexistent",
                    [f"Open browser and navigate to '{url}/nonexistent'.", "Assert that the page/response reflects: '404 or error page'."]),
            ]

        return [{"component": comp, "test_cases": tcs} for comp, tcs in suites.items()]

    def _tc2(self, idx, component, test_type, scenario, steps, expected, priority, page_url, auto_steps):
        """Build a test case dict in the new grouped format."""
        return {
            "tc_id": f"TC_{idx:02d}", "component": component,
            "test_type": test_type, "test_scenario": scenario,
            "test_steps": steps, "expected_result": expected,
            "priority": priority, "page_url": page_url,
            "automation_steps": auto_steps, "mapped": True,
        }
