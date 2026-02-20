"""
Test Executor Agent
--------------------
Takes combined test-case objects (from CombinedGeneratorAgent) and runs them
in a real Chrome browser via Selenium, collecting:

  - status          Pass | Fail | Error
  - duration_seconds
  - error_message   (or null)
  - screenshot_path (on failure; relative to project root)
  - log             human-readable step-by-step trace

Requires: selenium>=4.18, webdriver-manager>=4, Google Chrome installed.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"
WAIT_TIMEOUT = 8  # seconds to wait for elements

# Regex patterns built safely to avoid quote-escaping conflicts
_SEND_KEYS_PAT = re.compile(
    r"""(?:name|id|name/id)[^'"]*(['"])([^'"]+)\1"""
    r""".*?(?:send_keys?|enter|type|keys?)\s*[\(\s]*(['"])([^'"]*)\3""",
    re.I,
)
_ENTER_FIELD_PAT = re.compile(
    r"""enter\s+(['"]?)([^'"]+?)\1\s+in\s+(?:the\s+)?(['"]?)(\w[\w\-]*)['"]?\s*field""",
    re.I,
)


@dataclass
class ExecutionResult:
    tc_id: str
    feature: str
    user_role: str
    condition: str
    page_url: str
    status: str            # Pass | Fail | Error
    duration_seconds: float
    error_message: Optional[str]
    screenshot_path: Optional[str]
    log: str

    def to_dict(self) -> dict:
        return {
            "tc_id":            self.tc_id,
            "feature":          self.feature,
            "user_role":        self.user_role,
            "condition":        self.condition,
            "page_url":         self.page_url,
            "status":           self.status,
            "duration_seconds": round(self.duration_seconds, 2),
            "error_message":    self.error_message,
            "screenshot_path":  self.screenshot_path,
            "log":              self.log,
        }


class TestExecutorAgent:

    def __init__(self):
        self._shot_counter = 0  # global counter per execute_all call
    def execute_all(
        self,
        test_cases: List[dict],
        headless: bool = False,
        screenshots_dir: Optional[Path] = None,
    ) -> List[dict]:
        """
        Execute all provided test cases sequentially with a shared browser.
        Returns a list of ExecutionResult dicts.
        """
        if screenshots_dir is None:
            screenshots_dir = SCREENSHOTS_DIR
        screenshots_dir = Path(screenshots_dir)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        self._shot_counter = 0  # reset for each batch run
        driver = self._build_driver(headless)
        results = []
        try:
            for tc in test_cases:
                result = self._execute_one(tc, driver, screenshots_dir)
                results.append(result.to_dict())
        finally:
            try:
                driver.quit()
            except Exception:
                pass
        return results

    # ── Driver ────────────────────────────────────────────────────────────

    def _build_driver(self, headless: bool) -> webdriver.Chrome:
        opts = Options()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1440,900")
        opts.add_argument("--log-level=3")
        opts.add_experimental_option("excludeSwitches", ["enable-logging"])
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)

    # ── Execute one test case ─────────────────────────────────────────────

    def _execute_one(self, tc: dict, driver: webdriver.Chrome,
                     screenshots_dir: Path) -> ExecutionResult:
        tc_id     = tc.get("tc_id", "TC-???")
        feature   = tc.get("feature", "")
        user_role = tc.get("user_role", "")
        condition = tc.get("condition", "")
        page_url  = tc.get("page_url", "")
        steps     = tc.get("automation_steps", [])

        log_lines: List[str] = []
        start = time.time()

        try:
            driver.get(page_url)
            log_lines.append(f"✔ Navigated to {page_url}")

            for i, step in enumerate(steps, 1):
                try:
                    self._run_step(step, driver)
                    log_lines.append(f"✔ Step {i}: {step[:90]}")
                except (AssertionError, NoSuchElementException,
                        TimeoutException, WebDriverException) as exc:
                    log_lines.append(f"✘ Step {i} FAILED: {step[:90]}")
                    log_lines.append(f"   Reason: {exc}")
                    self._shot_counter += 1
                    shot = self._screenshot(driver, tc_id, self._shot_counter, screenshots_dir)
                    return ExecutionResult(
                        tc_id=tc_id, feature=feature, user_role=user_role,
                        condition=condition, page_url=page_url,
                        status="Fail",
                        duration_seconds=time.time() - start,
                        error_message=str(exc),
                        screenshot_path=shot,
                        log="\n".join(log_lines),
                    )

            log_lines.append("✅ All steps passed.")
            return ExecutionResult(
                tc_id=tc_id, feature=feature, user_role=user_role,
                condition=condition, page_url=page_url,
                status="Pass",
                duration_seconds=time.time() - start,
                error_message=None,
                screenshot_path=None,
                log="\n".join(log_lines),
            )

        except Exception as exc:
            log_lines.append(f"💥 Unexpected error: {exc}")
            self._shot_counter += 1
            shot = self._screenshot(driver, tc_id, self._shot_counter, screenshots_dir)
            return ExecutionResult(
                tc_id=tc_id, feature=feature, user_role=user_role,
                condition=condition, page_url=page_url,
                status="Error",
                duration_seconds=time.time() - start,
                error_message=str(exc),
                screenshot_path=shot,
                log="\n".join(log_lines),
            )

    # ── Step interpreter ──────────────────────────────────────────────────

    def _run_step(self, step: str, driver: webdriver.Chrome) -> None:
        s  = step.strip()
        sl = s.lower()

        # Skip comments / empty
        if not s or sl.startswith("#"):
            return

        wait = WebDriverWait(driver, WAIT_TIMEOUT)

        # navigate
        if any(k in sl for k in ("navigate to", "open browser", "go to")):
            url = self._extract_url(s) or self._extract_quoted(s)
            if url:
                driver.get(url)
            return

        # send_keys — pattern: find … name/id 'X' … send_keys('val')
        m = _SEND_KEYS_PAT.search(s)
        if m:
            locator, value = m.group(2), m.group(4)
            el = self._find_input(wait, locator)
            el.clear()
            el.send_keys(value)
            return

        # send_keys — pattern: Enter 'val' in the 'name' field
        m2 = _ENTER_FIELD_PAT.search(s)
        if m2:
            value, name = m2.group(2).strip(), m2.group(4).strip()
            el = self._find_input(wait, name)
            el.clear()
            el.send_keys(value)
            return

        # click button
        if any(k in sl for k in ("click()", "click the", "click button", "and click")):
            btn_text = self._extract_quoted(s)
            if btn_text:
                try:
                    xpath = (
                        f"//button[normalize-space()='{btn_text}']"
                        f"|//input[@value='{btn_text}']"
                        f"|//a[normalize-space()='{btn_text}']"
                    )
                    el = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
                    el.click()
                    return
                except TimeoutException:
                    pass
            # Fallback — submit
            try:
                el = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "button[type='submit'],input[type='submit']")))
                el.click()
                return
            except TimeoutException:
                raise NoSuchElementException(
                    f"Button '{btn_text}' not found via text or submit selector")

        # assert URL
        if re.search(r"\b(assert|verify|check|confirm)\b", sl) and "url" in sl:
            expected = self._extract_quoted(s) or self._extract_url(s)
            if expected:
                cur = driver.current_url
                assert expected in cur, f"URL mismatch: '{expected}' not in '{cur}'"
            return

        # assert text
        if re.search(r"\b(assert|verify|check|confirm)\b", sl):
            expected = self._extract_quoted(s)
            if expected:
                src = driver.page_source
                assert expected.lower() in src.lower(), \
                    f"Text '{expected}' not found in page"
            return

        # select dropdown
        if "select" in sl and any(k in sl for k in ("option", "dropdown", "from")):
            opt = self._extract_quoted(s)
            if opt:
                selects = driver.find_elements(By.TAG_NAME, "select")
                if selects:
                    Select(selects[0]).select_by_visible_text(opt)
            return

        # checkbox
        if "checkbox" in sl or "check the" in sl:
            cbs = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
            if cbs and not cbs[0].is_selected():
                cbs[0].click()
            return

        # Unknown step — silently skip (descriptive step)

    # ── Element helpers ───────────────────────────────────────────────────

    def _find_input(self, wait: WebDriverWait, locator: str):
        for by in (By.NAME, By.ID):
            try:
                return wait.until(EC.presence_of_element_located((by, locator)))
            except TimeoutException:
                pass
        try:
            css = f"input[placeholder*='{locator}' i]"
            return wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))
        except TimeoutException:
            pass
        raise NoSuchElementException(
            f"Input '{locator}' not found by name, id, or placeholder")

    def _extract_quoted(self, s: str) -> Optional[str]:
        m = re.search(r"""['"]([^'"]+)['"]""", s)
        return m.group(1).strip() if m else None

    def _extract_url(self, s: str) -> Optional[str]:
        m = re.search(r"https?://[^\s\"']+", s)
        return m.group(0).rstrip(".,;") if m else None

    def _screenshot(self, driver: webdriver.Chrome,
                    tc_id: str, index: int, screenshots_dir: Path) -> Optional[str]:
        """
        Save a screenshot with a unique filename so that each test case
        (even those with similar tc_ids) gets its own image.
        """
        try:
            # Brief pause so the browser renders the failure state fully
            time.sleep(0.4)
            safe_id = re.sub(r"[^\w\-]", "_", tc_id)
            ts      = int(time.time() * 1000)  # millisecond timestamp
            fname   = f"{index:03d}_{safe_id}_{ts}_failure.png"
            path    = screenshots_dir / fname
            driver.save_screenshot(str(path))
            return f"screenshots/{fname}"
        except Exception:
            return None
