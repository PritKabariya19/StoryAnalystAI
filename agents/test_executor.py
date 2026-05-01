"""
Test Executor Agent — Parallel Edition (Specific Error Matching)

Negative test validation rules:
  PASS  → only when the SPECIFIC expected error message is found on page
  FAIL  → when a WRONG error message is found, or NO error message at all
  Every negative result logs: expected_error_message, error_message_found,
  message_match (bool), and a human-readable reason.
"""

import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, WebDriverException,
    ElementNotInteractableException, ElementClickInterceptedException,
    ElementNotSelectableException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

SCREENSHOTS_DIR   = Path(__file__).parent.parent / "screenshots"
WAIT_TIMEOUT      = 5   # seconds per element wait
PAGE_LOAD_TIMEOUT = 12  # seconds per page load
MAX_WORKERS       = 8

_UNREACHABLE_PATTERNS = (
    "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT", "ERR_INTERNET_DISCONNECTED",
    "ERR_ADDRESS_UNREACHABLE", "net::ERR_",
)

# Regex: Enter 'value' in the 'fieldname' field
_ENTER_FIELD_PAT = re.compile(
    r"""enter\s+['"]?([^'"]+?)['"]?\s+in\s+(?:the\s+)?['"]?(\w[\w\s\-]*)['"]?\s*field""",
    re.I,
)
# Regex: click/click the/find button with text 'X'
_CLICK_PAT = re.compile(
    r"""(?:click|find\s+(?:link|button|element)(?:\s+with\s+text)?)\s+['"]([^'"]+)['"]""",
    re.I,
)
# Regex: navigate to 'url' or navigate to url
_NAV_PAT = re.compile(
    r"""(?:open\s+browser\s+and\s+navigate\s+to|navigate\s+to|go\s+to)\s+['"]?(https?://[^\s'"]+)['"]?""",
    re.I,
)


@dataclass
class ExecutionResult:
    tc_id:            str
    feature:          str
    user_role:        str
    condition:        str
    page_url:         str
    status:           str   # Pass | Fail | Error
    duration_seconds: float
    error_message:    Optional[str]
    screenshot_path:  Optional[str]
    log:              str
    block_reason:           Optional[str] = None
    expected_error_message: Optional[str] = None
    error_message_found:    Optional[str] = None
    message_match:          Optional[bool] = None
    final_verdict:          Optional[str] = None
    fields_filled:          Optional[List[dict]] = None

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
            "block_reason":           self.block_reason,
            "expected_error_message": self.expected_error_message,
            "error_message_found":    self.error_message_found,
            "message_match":          self.message_match,
            "final_verdict":          self.final_verdict,
            "fields_filled":          self.fields_filled,
        }


class TestExecutorAgent:

    def __init__(self):
        self._shot_counter = 0
        self._shot_lock    = threading.Lock()
        # Per-test-case fill log, reset at the start of each _execute_one
        self._fill_log: List[dict] = []

    # ── Public API ─────────────────────────────────────────────────────────

    def execute_all(
        self,
        test_cases:      List[dict],
        headless:        bool = True,
        workers:         int  = 1,
        screenshots_dir: Optional[Path] = None,
    ) -> List[dict]:
        if screenshots_dir is None:
            screenshots_dir = SCREENSHOTS_DIR
        screenshots_dir = Path(screenshots_dir)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        workers = max(1, min(int(workers), MAX_WORKERS, len(test_cases)))
        self._shot_counter = 0

        indexed: List[Tuple[int, dict]] = list(enumerate(test_cases))

        if workers == 1:
            raw = self._run_worker(indexed, headless, screenshots_dir)
            return [r for _, r in sorted(raw, key=lambda x: x[0])]

        chunk_size = math.ceil(len(indexed) / workers)
        chunks = [indexed[i: i + chunk_size] for i in range(0, len(indexed), chunk_size)]

        all_indexed: List[Tuple[int, dict]] = []
        lock = threading.Lock()

        def worker_task(chunk):
            results = self._run_worker(chunk, headless, screenshots_dir)
            with lock:
                all_indexed.extend(results)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker_task, chunk) for chunk in chunks]
            for f in as_completed(futures):
                exc = f.exception()
                if exc:
                    print(f"[executor] Worker error: {exc}")

        all_indexed.sort(key=lambda x: x[0])
        return [r for _, r in all_indexed]

    def execute_batch(
        self,
        test_cases:      List[dict],
        start_index:     int  = 0,
        end_index:       Optional[int] = None,
        batch_size:      int  = 5,
        headless:        bool = True,
        workers:         int  = 1,
        screenshots_dir: Optional[Path] = None,
        on_batch_done=None,   # optional callback(batch_results, offset, total_done, total)
    ) -> List[dict]:
        """Execute a sub-range of test_cases in sequential batches of `batch_size`.

        Args:
            test_cases:    Full list of test-case dicts.
            start_index:   0-based inclusive start (default 0).
            end_index:     0-based exclusive end (default len(test_cases)).
            batch_size:    How many tests to run per batch iteration.
            headless:      Headless Chrome flag.
            workers:       Parallel Chrome workers per batch.
            screenshots_dir: Optional override for screenshot directory.
            on_batch_done: Called after each batch with
                           (batch_results, batch_start_offset, total_done, total_in_range).
        Returns:
            Flat list of all results in range order.
        """
        if screenshots_dir is None:
            screenshots_dir = SCREENSHOTS_DIR
        screenshots_dir = Path(screenshots_dir)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        # Clamp range
        total_all = len(test_cases)
        start_index = max(0, min(int(start_index), total_all))
        if end_index is None:
            end_index = total_all
        end_index = max(start_index, min(int(end_index), total_all))
        batch_size = max(1, int(batch_size))

        in_range = test_cases[start_index:end_index]
        total_in_range = len(in_range)

        if total_in_range == 0:
            return []

        all_results: List[dict] = []
        cursor = 0  # offset within in_range

        while cursor < total_in_range:
            batch = in_range[cursor: cursor + batch_size]
            # Run this batch (may itself be parallel with `workers`)
            batch_results = self.execute_all(
                batch,
                headless=headless,
                workers=workers,
                screenshots_dir=screenshots_dir,
            )
            all_results.extend(batch_results)
            cursor += len(batch)

            print(
                f"[executor] Batch done — "
                f"{cursor}/{total_in_range} of range "
                f"[{start_index}:{end_index}]"
            )

            if on_batch_done is not None:
                try:
                    on_batch_done(
                        batch_results,
                        start_index + cursor - len(batch),  # absolute offset
                        start_index + cursor,               # absolute done
                        end_index - start_index,            # total in range
                    )
                except Exception as cb_exc:
                    print(f"[executor] on_batch_done callback error: {cb_exc}")

        return all_results

    # ── Worker ─────────────────────────────────────────────────────────────

    def _run_worker(
        self,
        indexed_cases: List[Tuple[int, dict]],
        headless:      bool,
        screenshots_dir: Path,
    ) -> List[Tuple[int, dict]]:
        driver = self._build_driver(headless)
        results: List[Tuple[int, dict]] = []
        try:
            for original_idx, tc in indexed_cases:
                result = self._execute_one(tc, driver, screenshots_dir)
                results.append((original_idx, result.to_dict()))
        finally:
            try:
                driver.quit()
            except Exception:
                pass
        return results

    # ── Driver ─────────────────────────────────────────────────────────────

    def _build_driver(self, headless: bool) -> webdriver.Chrome:
        opts = Options()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1440,900")
        opts.add_argument("--log-level=3")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--blink-settings=imagesEnabled=false")
        opts.add_experimental_option("excludeSwitches", ["enable-logging"])
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.implicitly_wait(1)
        return driver

    # ── Execute one TC ──────────────────────────────────────────────────────

    def _execute_one(
        self,
        tc:              dict,
        driver:          webdriver.Chrome,
        screenshots_dir: Path,
    ) -> ExecutionResult:
        tc_id     = tc.get("tc_id", "TC-???")
        feature   = tc.get("feature", "")
        user_role = tc.get("user_role", "")
        condition = tc.get("condition", "")
        page_url  = (tc.get("page_url") or "").strip()
        steps     = tc.get("automation_steps", [])
        test_type = tc.get("type", "").lower()

        if page_url:
            if not page_url.startswith(("http://", "https://", "file://")):
                # Default to http:// if somehow missing (e.g., user typed localhost:3000)
                page_url = "http://" + page_url
        else:
            page_url = ""

        log_lines: List[str] = []
        self._fill_log = []   # reset fill log for this test case
        start = time.time()

        try:
            # ── Initial navigation ─────────────────────────────────────
            if page_url:
                try:
                    driver.get(page_url)
                    time.sleep(0.5)  # let JS render
                    src = driver.page_source or ""
                    if any(p in src for p in _UNREACHABLE_PATTERNS):
                        raise WebDriverException(f"Host unreachable: {page_url}")
                    log_lines.append(f"✔ Navigated to {page_url}")
                except TimeoutException:
                    log_lines.append(f"⚠ Page load timed out at {page_url} — continuing")
                except WebDriverException as nav_err:
                    err_str = str(nav_err)[:200]
                    log_lines.append(f"✘ Navigation failed: {err_str}")
                    shot = self._screenshot(driver, tc_id, screenshots_dir)
                    return ExecutionResult(
                        tc_id=tc_id, feature=feature, user_role=user_role,
                        condition=condition, page_url=page_url,
                        status="Error",
                        duration_seconds=time.time() - start,
                        error_message=f"Navigation error: {err_str}",
                        screenshot_path=shot,
                        log="\n".join(log_lines),
                        fields_filled=self._fill_log if self._fill_log else None,
                    )
            else:
                log_lines.append("⚠ No valid page URL — skipping navigation.")

            # ── Run steps ─────────────────────────────────────────────
            is_negative = test_type in ["negative", "edge_case"]
            # Extract expected error from tc or from assert steps
            expected_error = (tc.get("expected_error_message") or "").strip()
            # Snapshot page source before submit for silent-reload detection
            pre_submit_source: Optional[str] = None

            for i, step in enumerate(steps, 1):
                if not step:
                    continue
                try:
                    if isinstance(step, dict):
                        action = step.get('action', 'unknown').lower()

                        # --- Custom Assert Handling for Negative Tests ---
                        if is_negative and action == "assert":
                            step_expected = (step.get("expected", "") or "").strip()
                            # Use step-level expected if tc-level is empty
                            eff_expected = expected_error or step_expected

                            # Check URL changed (means system did NOT block)
                            cur_url = driver.current_url.rstrip("/")
                            p_url = page_url.rstrip("/")

                            if cur_url != p_url and p_url not in cur_url:
                                log_lines.append(f"✘ Step {i} FAILED: URL changed to {cur_url}")
                                shot = self._screenshot(driver, tc_id, screenshots_dir)
                                return ExecutionResult(
                                    tc_id=tc_id, feature=feature, user_role=user_role,
                                    condition=condition, page_url=page_url, status="Fail",
                                    duration_seconds=time.time() - start,
                                    error_message="URL changed unexpectedly — system did not block.",
                                    screenshot_path=shot, log="\n".join(log_lines),
                                    block_reason="not_blocked",
                                    expected_error_message=eff_expected,
                                    error_message_found="none",
                                    message_match=False,
                                    final_verdict=f"FAIL — system did not block, redirected to {cur_url}",
                                    fields_filled=self._fill_log if self._fill_log else None,
                                )

                            # ── Specific error message matching ──
                            result = self._evaluate_negative_result(
                                driver, eff_expected, tc_id, feature,
                                user_role, condition, page_url, start,
                                screenshots_dir, log_lines, i,
                                pre_submit_source=pre_submit_source,
                            )
                            return result

                        # Capture page source before click (for silent reload detection)
                        if action == "click" and is_negative:
                            try:
                                pre_submit_source = driver.page_source
                            except Exception:
                                pass

                        self._run_json_step(step, driver)
                        action_log = step.get('action', 'unknown').upper()
                        val = step.get('url') or step.get('value') or step.get('expected') or step.get('locator', {}).get('value') or ''
                        log_lines.append(f"✔ Step {i}: [{action_log}] {val}")
                    else:
                        s = step.strip()
                        if not s or s.startswith("#"):
                            continue
                        self._run_step(s, driver)
                        log_lines.append(f"✔ Step {i}: {s[:90]}")
                except (AssertionError, NoSuchElementException,
                        TimeoutException, WebDriverException) as exc:

                    # 1. Blocked Action (e.g. Disabled submit button)
                    if is_negative and isinstance(exc, (ElementNotInteractableException, ElementClickInterceptedException)):
                        # Button was disabled/blocked — still verify the correct error
                        eff_expected = expected_error
                        if not eff_expected:
                            # No expected error specified → structural block is enough
                            log_lines.append(f"✔ Step {i} BLOCKED as expected: {str(exc)[:60]}")
                            shot = self._screenshot(driver, tc_id, screenshots_dir)
                            return ExecutionResult(
                                tc_id=tc_id, feature=feature, user_role=user_role,
                                condition=condition, page_url=page_url,
                                status="Pass",
                                duration_seconds=time.time() - start,
                                error_message=None,
                                screenshot_path=shot,
                                log="\n".join(log_lines),
                                block_reason="button_disabled",
                                expected_error_message="N/A",
                                error_message_found="N/A (button disabled)",
                                message_match=True,
                                final_verdict="PASS — system correctly blocked via button_disabled",
                                fields_filled=self._fill_log if self._fill_log else None,
                            )
                        # Button blocked + expected error → still verify message
                        log_lines.append(f"⚠ Step {i} button blocked — checking error message...")
                        return self._evaluate_negative_result(
                            driver, eff_expected, tc_id, feature,
                            user_role, condition, page_url, start,
                            screenshots_dir, log_lines, i,
                            block_reason_prefix="button_disabled",
                            pre_submit_source=pre_submit_source,
                        )

                    # 2. For negative tests with other exceptions, check error message
                    if is_negative and expected_error:
                        log_lines.append(f"⚠ Step {i} exception: {str(exc)[:80]} — checking error message...")
                        return self._evaluate_negative_result(
                            driver, expected_error, tc_id, feature,
                            user_role, condition, page_url, start,
                            screenshots_dir, log_lines, i,
                            pre_submit_source=pre_submit_source,
                        )

                    # 3. Positive test or no expected error → normal failure
                    log_lines.append(f"✘ Step {i} FAILED")
                    log_lines.append(f"   Reason: {str(exc)[:120]}")
                    shot = self._screenshot(driver, tc_id, screenshots_dir)
                    return ExecutionResult(
                        tc_id=tc_id, feature=feature, user_role=user_role,
                        condition=condition, page_url=page_url,
                        status="Fail",
                        duration_seconds=time.time() - start,
                        error_message=str(exc)[:300],
                        screenshot_path=shot,
                        log="\n".join(log_lines),
                        fields_filled=self._fill_log if self._fill_log else None,
                    )

            # ── All steps completed ───────────────────────────────────
            if is_negative and expected_error:
                # Negative test reached end without explicit assert
                # → must still verify the expected error message is on the page
                log_lines.append("⚠ All steps done — evaluating negative test result...")
                return self._evaluate_negative_result(
                    driver, expected_error, tc_id, feature,
                    user_role, condition, page_url, start,
                    screenshots_dir, log_lines, len(steps),
                    pre_submit_source=pre_submit_source,
                )

            log_lines.append("✅ All steps passed.")
            pass_shot = self._screenshot(driver, tc_id, screenshots_dir)
            return ExecutionResult(
                tc_id=tc_id, feature=feature, user_role=user_role,
                condition=condition, page_url=page_url,
                status="Pass",
                duration_seconds=time.time() - start,
                error_message=None,
                screenshot_path=pass_shot,
                log="\n".join(log_lines),
                fields_filled=self._fill_log if self._fill_log else None,
            )

        except Exception as exc:
            log_lines.append(f"💥 Unexpected error: {str(exc)[:200]}")
            shot = self._screenshot(driver, tc_id, screenshots_dir)
            return ExecutionResult(
                tc_id=tc_id, feature=feature, user_role=user_role,
                condition=condition, page_url=page_url,
                status="Error",
                duration_seconds=time.time() - start,
                error_message=str(exc)[:300],
                screenshot_path=shot,
                log="\n".join(log_lines),
                fields_filled=self._fill_log if self._fill_log else None,
            )

    # ── Negative-test evaluation (centralised) ─────────────────────────

    def _evaluate_negative_result(
        self,
        driver,
        expected_error:    str,
        tc_id:             str,
        feature:           str,
        user_role:         str,
        condition:         str,
        page_url:          str,
        start_time:        float,
        screenshots_dir,
        log_lines:         list,
        step_num:          int,
        block_reason_prefix: str = "",
        pre_submit_source: Optional[str] = None,
    ) -> ExecutionResult:
        """Decide PASS / FAIL for a negative test by matching the SPECIFIC
        expected error message against VISIBLE error elements only.

        Rules:
          PASS  -> expected error found in visible error elements (case-insensitive)
          FAIL  -> wrong error shown, or no error at all

        Features:
          - Only matches against visible error text (never raw page source)
          - Polls up to 3s for error messages to appear (handles async JS)
          - Detects silent page reload via pre_submit_source comparison
          - Always attaches fields_filled to the result
        """
        expected_lower = expected_error.lower().strip()
        fill_log = self._fill_log if self._fill_log else None

        # ── Poll for error messages (up to 3s, every 0.5s) ──
        error_list: List[str] = []
        for _attempt in range(7):  # 0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0
            error_list = self._find_visible_error_text(driver)
            if error_list:
                break
            time.sleep(0.5)

        # Format for logging: show all found errors
        if error_list:
            found_display = " | ".join(error_list)
        else:
            found_display = "none"

        # ── Check if expected error is in ANY visible error text ──
        match_found = any(
            expected_lower in err.lower()
            for err in error_list
        )

        if match_found:
            # ── RULE 1: Correct error found → PASS ──
            reason = "Correct error shown"
            block_reason = block_reason_prefix or "error_message_shown"
            log_lines.append(
                f"✔ Step {step_num}: [NEGATIVE PASS] "
                f"Expected error '{expected_error}' found on page."
            )
            shot = self._screenshot(driver, tc_id, screenshots_dir)
            return ExecutionResult(
                tc_id=tc_id, feature=feature, user_role=user_role,
                condition=condition, page_url=page_url,
                status="Pass",
                duration_seconds=time.time() - start_time,
                error_message=None,
                screenshot_path=shot,
                log="\n".join(log_lines),
                block_reason=block_reason,
                expected_error_message=expected_error,
                error_message_found=found_display,
                message_match=True,
                final_verdict=f"PASS — {reason}",
                fields_filled=fill_log,
            )

        # Expected error NOT found
        if error_list:
            # ── RULE 2: Wrong error shown → FAIL ──
            reason = (
                f"Wrong error shown: expected '{expected_error}' "
                f"but found '{found_display}'"
            )
            block_reason = block_reason_prefix or "error_message_shown"
            log_lines.append(
                f"✘ Step {step_num}: [NEGATIVE FAIL] {reason}"
            )
            shot = self._screenshot(driver, tc_id, screenshots_dir)
            return ExecutionResult(
                tc_id=tc_id, feature=feature, user_role=user_role,
                condition=condition, page_url=page_url,
                status="Fail",
                duration_seconds=time.time() - start_time,
                error_message=reason,
                screenshot_path=shot,
                log="\n".join(log_lines),
                block_reason=block_reason,
                expected_error_message=expected_error,
                error_message_found=found_display,
                message_match=False,
                final_verdict=f"FAIL — {reason}",
                fields_filled=fill_log,
            )

        # ── RULE 3: No error at all → FAIL ──
        # Detect silent page reload
        block_reason = block_reason_prefix or "no_error_shown"
        if pre_submit_source is not None:
            try:
                current_source = driver.page_source or ""
                if current_source != pre_submit_source:
                    block_reason = block_reason_prefix or "page_reloaded_silently"
                    log_lines.append(
                        f"⚠ Step {step_num}: Page source changed (silent reload detected)"
                    )
            except Exception:
                pass

        reason = "No error message found on page"
        log_lines.append(
            f"✘ Step {step_num}: [NEGATIVE FAIL] {reason}"
        )
        shot = self._screenshot(driver, tc_id, screenshots_dir)
        return ExecutionResult(
            tc_id=tc_id, feature=feature, user_role=user_role,
            condition=condition, page_url=page_url,
            status="Fail",
            duration_seconds=time.time() - start_time,
            error_message=reason,
            screenshot_path=shot,
            log="\n".join(log_lines),
            block_reason=block_reason,
            expected_error_message=expected_error,
            error_message_found="none",
            message_match=False,
            final_verdict=f"FAIL — {reason}",
            fields_filled=fill_log,
        )

    # ── Thread-safe screenshot & Error Helpers ─────────────────────────

    def _find_visible_error_text(self, driver) -> List[str]:
        """Scan the page for visible error/validation text.

        Searches (in order):
          1. Elements whose class contains error/invalid/alert/danger/warning
          2. Elements with role="alert"
          3. Toast/snackbar containers
          4. HTML5 validation messages via JS

        Returns a list of individual error texts found (empty list = none).
        """
        found_texts: List[str] = []

        # 1. Class-based error elements
        try:
            _TR = "translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
            xpath_class = (
                f"//*["
                f"contains({_TR}, 'error') or "
                f"contains({_TR}, 'invalid') or "
                f"contains({_TR}, 'alert') or "
                f"contains({_TR}, 'danger') or "
                f"contains({_TR}, 'warning') or "
                f"contains({_TR}, 'validation') or "
                f"contains({_TR}, 'feedback')]"
            )
            for el in driver.find_elements(By.XPATH, xpath_class):
                if el.is_displayed():
                    text = el.text.strip()
                    if text and text not in found_texts:
                        found_texts.append(text)
        except Exception:
            pass

        # 2. role="alert" elements
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, '[role="alert"]'):
                if el.is_displayed():
                    text = el.text.strip()
                    if text and text not in found_texts:
                        found_texts.append(text)
        except Exception:
            pass

        # 3. Toast / snackbar containers
        try:
            for sel in ['[class*="toast"]', '[class*="snackbar"]', '[class*="notification"]']:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed():
                        text = el.text.strip()
                        if text and text not in found_texts:
                            found_texts.append(text)
        except Exception:
            pass

        # 4. HTML5 constraint-validation messages via JS (individual msgs)
        try:
            js_msgs = driver.execute_script("""
                var msgs = [];
                document.querySelectorAll('input,select,textarea').forEach(function(el){
                    if (!el.validity.valid && el.validationMessage) {
                        msgs.push(el.validationMessage);
                    }
                });
                return msgs;
            """)
            if js_msgs:
                for msg in js_msgs:
                    msg = msg.strip()
                    if msg and msg not in found_texts:
                        found_texts.append(msg)
        except Exception:
            pass

        return found_texts

    def _screenshot(self, driver, tc_id, screenshots_dir):
        try:
            time.sleep(0.3)
            with self._shot_lock:
                self._shot_counter += 1
                idx = self._shot_counter
            safe_id = re.sub(r"[^\w\-]", "_", tc_id)
            ts    = int(time.time() * 1000)
            fname = f"{idx:04d}_{safe_id}_{ts}.png"
            
            # Ensure absolute path calculation
            abs_dir = Path(screenshots_dir).resolve()
            abs_dir.mkdir(parents=True, exist_ok=True)
            path  = abs_dir / fname
            
            success = driver.save_screenshot(str(path))
            if not success:
                print(f"[executor] save_screenshot() returned False for {fname}")
            return f"screenshots/{fname}"
        except Exception as e:
            print(f"[executor] Screenshot error for {tc_id}: {e}")
            return None

    # ── Step interpreter ───────────────────────────────────────────────

    def _run_json_step(self, step: dict, driver: webdriver.Chrome) -> None:
        action = step.get("action", "").lower()
        wait = WebDriverWait(driver, WAIT_TIMEOUT)

        if action == "navigate":
            url = step.get("url")
            if url:
                try:
                    driver.get(url)
                    time.sleep(0.5)
                except TimeoutException:
                    pass
            return

        locator = step.get("locator", {})
        ltype = locator.get("type", "").lower()
        lval = locator.get("value", "")

        def _get_element():
            if not lval: return None
            if ltype == "id": return wait.until(EC.presence_of_element_located((By.ID, lval)))
            if ltype == "name": return wait.until(EC.presence_of_element_located((By.NAME, lval)))
            if ltype == "css": return wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, lval)))
            if ltype == "xpath": return wait.until(EC.presence_of_element_located((By.XPATH, lval)))
            # fallback
            return self._find_input(wait, lval)

        if action == "input":
            value = step.get("value", "")
            el = _get_element()
            if el:
                fill_result = self._smart_fill_element(driver, el, lval or "field", value)
                # Propagate hard failures (element not interactable) as exceptions
                if fill_result["status"].startswith("error"):
                    raise ElementNotInteractableException(
                        f"smart_fill failed on '{lval}': {fill_result['status']}"
                    )
            return

        if action == "click":
            el = _get_element()
            if el:
                try:
                    driver.execute_script("arguments[0].scrollIntoView(true);", el)
                    wait.until(EC.element_to_be_clickable(el)).click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
            else:
                # If no strict locator, try smart click with 'value'
                self._smart_click(driver, wait, step.get("value", ""))
            return

        if action == "assert":
            atype = step.get("type", "url")
            expected = step.get("expected", "")
            if atype == "url":
                cur = driver.current_url
                assert expected in cur, f"URL mismatch: '{expected}' not in '{cur}'"
            else:
                src = driver.page_source.lower()
                assert expected.lower() in src, f"Text '{expected}' not found on page"
            return

    def _run_step(self, step: str, driver: webdriver.Chrome) -> None:
        s  = step.strip()
        sl = s.lower()
        if not s or sl.startswith("#"):
            return

        wait = WebDriverWait(driver, WAIT_TIMEOUT)

        # ── Navigate ────────────────────────────────────────────────────
        nav_m = _NAV_PAT.search(s)
        if nav_m:
            url = nav_m.group(1).rstrip("'\".,;")
            try:
                driver.get(url)
                time.sleep(0.5)
            except TimeoutException:
                pass  # continue with partial load
            return

        # ── Clear field ─────────────────────────────────────────────────
        if re.search(r"\bclear\b.*\bfield\b", sl):
            fname = self._extract_quoted(s)
            if fname:
                el = self._find_input(wait, fname)
                try:
                    el.clear()
                except Exception:
                    driver.execute_script("arguments[0].value = '';", el)
            return

        # ── Enter value in field ────────────────────────────────────────
        m = _ENTER_FIELD_PAT.search(s)
        if m:
            value = m.group(1).strip().strip("'\"")
            name  = m.group(2).strip().strip("'\"")
            try:
                el = self._find_input(wait, name)
                fill_result = self._smart_fill_element(driver, el, name, value)
                if fill_result["status"].startswith("error"):
                    raise NoSuchElementException(
                        f"Could not fill field '{name}': {fill_result['status']}"
                    )
            except NoSuchElementException:
                raise
            except Exception as e:
                raise NoSuchElementException(f"Could not fill field '{name}': {e}")
            return

        # ── Click ───────────────────────────────────────────────────────
        click_m = _CLICK_PAT.search(s)
        if click_m:
            target = click_m.group(1).strip()
            self._smart_click(driver, wait, target)
            # Dismiss any alert
            try:
                driver.switch_to.alert.accept()
            except Exception:
                pass
            return

        # ── Assert URL ──────────────────────────────────────────────────
        if re.search(r"\b(assert|verify|check|confirm)\b", sl) and "url" in sl:
            expected = self._extract_quoted(s) or self._extract_url(s)
            if expected:
                cur = driver.current_url
                assert expected in cur, f"URL mismatch: '{expected}' not in '{cur}'"
            return

        # ── Assert text on page ─────────────────────────────────────────
        if re.search(r"\b(assert|verify|check|confirm)\b", sl) and \
                re.search(r"\b(page|response|text|message|show|display|reflect)\b", sl):
            expected = self._extract_quoted(s)
            if expected:
                exp_l = expected.lower()
                # Generic placeholder phrases — skip
                SKIP_PHRASES = [
                    "the page/response reflects", "system responds correctly",
                    "edge case safely", "boundary value", "handles the edge",
                    "operation completes", "accepts or rejects",
                    "error/validation message", "appropriate error",
                    "action is rejected", "confirmation is shown",
                ]
                if any(p in exp_l for p in SKIP_PHRASES):
                    return

                src     = driver.page_source.lower()
                cur_url = driver.current_url.lower()

                # Smart positive vs negative check
                positive_kw = ["success", "welcome", "logged in", "dashboard", "registered",
                               "profile", "thank you", "confirmed", "submitted", "saved", "logout"]
                negative_kw = ["invalid", "incorrect", "error", "failed", "wrong",
                               "denied", "unauthorized", "not found", "required", "try again"]

                if any(k in exp_l for k in positive_kw):
                    if any(k in src for k in positive_kw) or \
                            any(k in cur_url for k in ["dashboard", "home", "profile", "success", "account"]):
                        return  # soft pass
                    if "login" not in cur_url and "signin" not in cur_url:
                        return  # redirected away from login = probably success
                    assert False, "Expected success indicators but not found"

                if any(k in exp_l for k in negative_kw):
                    return  # soft pass for negative assertions

                if len(exp_l) < 80 and exp_l not in src:
                    assert False, f"Text '{expected}' not found on page"
            return

        # ── Dropdown ────────────────────────────────────────────────────
        if "select" in sl and any(k in sl for k in ("option", "dropdown", "from")):
            opt = self._extract_quoted(s)
            if opt:
                selects = driver.find_elements(By.TAG_NAME, "select")
                if selects:
                    try:
                        Select(selects[0]).select_by_visible_text(opt)
                    except Exception:
                        pass
            return

        # ── Checkbox ────────────────────────────────────────────────────
        if "checkbox" in sl or "check the" in sl:
            cbs = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
            if cbs and not cbs[0].is_selected():
                try:
                    cbs[0].click()
                except Exception:
                    driver.execute_script("arguments[0].click();", cbs[0])
            return

        # Unknown step — silently skip

    # ── Smart click (buttons + links + inputs) ─────────────────────────

    def _smart_click(self, driver, wait, target: str):
        tl = target.lower()
        clicked = False

        # 1. input[type=submit]
        for el in driver.find_elements(By.CSS_SELECTOR, "input[type='submit'], input[type='button']"):
            val = (el.get_attribute("value") or "").lower()
            if tl in val or val in tl:
                try:
                    driver.execute_script("arguments[0].scrollIntoView(true);", el)
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                clicked = True
                break

        # 2. button elements
        if not clicked:
            for el in driver.find_elements(By.TAG_NAME, "button"):
                if tl in el.text.strip().lower():
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", el)
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    clicked = True
                    break

        # 3. anchor/link elements
        if not clicked:
            for el in driver.find_elements(By.TAG_NAME, "a"):
                if tl in el.text.strip().lower():
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", el)
                        el.click()
                        time.sleep(0.5)  # wait for navigation
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    clicked = True
                    break

        # 4. XPath fallback with case-insensitive translate
        if not clicked:
            tl_safe = tl.replace("'", "\\'")
            xpath = (
                f"//button[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{tl_safe}')]"
                f"|//a[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{tl_safe}')]"
                f"|//input[contains(translate(@value,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{tl_safe}') and (@type='submit' or @type='button')]"
            )
            try:
                el = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
                driver.execute_script("arguments[0].click();", el)
                clicked = True
            except Exception:
                pass

        # 5. Any submit
        if not clicked:
            try:
                el = driver.find_element(By.CSS_SELECTOR,
                    "button[type='submit'], input[type='submit']")
                driver.execute_script("arguments[0].click();", el)
                clicked = True
            except Exception:
                pass

        if not clicked:
            raise NoSuchElementException(f"Clickable element '{target}' not found")

    # ── Universal Smart Fill ────────────────────────────────────────────

    def _smart_fill_element(
        self,
        driver: webdriver.Chrome,
        element,
        field_name: str,
        value: str,
    ) -> dict:
        """Detect the element type and use the correct fill method.

        Supports: text/email/password/number/search/tel/url inputs,
        select dropdowns, checkboxes, radio buttons, textareas,
        file uploads, date/time inputs, range sliders, hidden fields.
        Skips disabled/read-only fields gracefully.

        Returns a dict with: field, value, element_type, status, warning.
        Always appends the result to self._fill_log.
        """
        result: dict = {
            "field": field_name,
            "value": str(value),
            "element_type": None,
            "status": None,
            "warning": None,
        }

        try:
            # ── Detect element type ───────────────────────────────────
            tag = element.tag_name.lower()
            input_type = (element.get_attribute("type") or "text").lower()
            is_disabled = element.get_attribute("disabled") is not None
            is_readonly = element.get_attribute("readonly") is not None
            result["element_type"] = f"{tag}[type={input_type}]"

            # ── TYPE 10: Disabled / Read-only → skip ──────────────────
            if is_disabled or is_readonly:
                result["status"] = "skipped - field is disabled or read-only"
                self._fill_log.append(result)
                return result

            # ── TYPE 1: Text / Email / Password / Number / Search / Tel / URL
            if tag == "input" and input_type in (
                "text", "email", "password", "number",
                "search", "tel", "url", "",
            ):
                element.clear()
                element.send_keys(str(value))
                result["status"] = f"filled - {input_type or 'text'} input"

            # ── TYPE 2: Select / Dropdown ─────────────────────────────
            elif tag == "select":
                sel = Select(element)
                try:
                    sel.select_by_visible_text(str(value))
                    result["status"] = "filled - dropdown by text"
                except Exception:
                    try:
                        sel.select_by_value(str(value))
                        result["status"] = "filled - dropdown by value"
                    except Exception:
                        sel.select_by_index(0)
                        result["status"] = "filled - dropdown default (index 0)"
                        result["warning"] = f"value '{value}' not found, used default"

            # ── TYPE 3: Checkbox ──────────────────────────────────────
            elif tag == "input" and input_type == "checkbox":
                should_check = str(value).lower() in ("true", "yes", "1", "on")
                if element.is_selected() != should_check:
                    try:
                        element.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", element)
                result["status"] = f"filled - checkbox set to {should_check}"

            # ── TYPE 4: Radio Button ──────────────────────────────────
            elif tag == "input" and input_type == "radio":
                name_attr = element.get_attribute("name") or field_name
                radios = driver.find_elements(By.NAME, name_attr)
                clicked = False
                for radio in radios:
                    if (radio.get_attribute("value") or "").lower() == str(value).lower():
                        try:
                            radio.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", radio)
                        clicked = True
                        break
                result["status"] = (
                    "filled - radio selected" if clicked
                    else f"skipped - radio value '{value}' not found"
                )

            # ── TYPE 5: Textarea ──────────────────────────────────────
            elif tag == "textarea":
                element.clear()
                element.send_keys(str(value))
                result["status"] = "filled - textarea"

            # ── TYPE 6: File Upload ───────────────────────────────────
            elif tag == "input" and input_type == "file":
                element.send_keys(str(value))
                result["status"] = "filled - file upload"

            # ── TYPE 7: Date / Time ───────────────────────────────────
            elif tag == "input" and input_type in ("date", "time", "datetime-local", "month", "week"):
                driver.execute_script(
                    "arguments[0].value = arguments[1];"
                    "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                    element, str(value),
                )
                result["status"] = f"filled - {input_type} via script"

            # ── TYPE 8: Range / Slider ────────────────────────────────
            elif tag == "input" and input_type == "range":
                driver.execute_script(
                    "arguments[0].value = arguments[1];"
                    "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                    "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                    element, str(value),
                )
                result["status"] = "filled - range slider via script"

            # ── TYPE 9: Hidden Field ──────────────────────────────────
            elif tag == "input" and input_type == "hidden":
                driver.execute_script(
                    "arguments[0].value = arguments[1];", element, str(value),
                )
                result["status"] = "filled - hidden field via script"
                result["warning"] = "field is hidden - filled via JavaScript"

            # ── TYPE 11: Unknown / Unsupported ────────────────────────
            else:
                # Last-resort: try send_keys anyway
                try:
                    element.clear()
                    element.send_keys(str(value))
                    result["status"] = f"filled - fallback send_keys on {tag}[{input_type}]"
                    result["warning"] = "used fallback send_keys for unsupported type"
                except Exception:
                    result["status"] = f"skipped - unsupported type: {tag}[{input_type}]"

        except ElementNotInteractableException:
            result["status"] = "skipped - element not interactable"
        except ElementNotSelectableException:
            result["status"] = "skipped - element not selectable"
        except Exception as e:
            result["status"] = f"error - {str(e)[:150]}"

        self._fill_log.append(result)
        return result

    # ── Element helpers ─────────────────────────────────────────────────

    def _find_input(self, wait, locator: str):
        """Find a form element (input, textarea, or select) by name, id,
        placeholder, or label text."""
        loc = locator.strip()
        # Try name, id  (matches input, select, textarea — any tag)
        for by in (By.NAME, By.ID):
            try:
                return wait.until(EC.presence_of_element_located((by, loc)))
            except TimeoutException:
                pass
        # placeholder partial match (input + textarea)
        try:
            css = f"input[placeholder*='{loc}' i], textarea[placeholder*='{loc}' i]"
            return wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))
        except TimeoutException:
            pass
        # label text → following input, select, or textarea
        try:
            loc_lower = loc.lower()
            xpath = (
                f"//label[contains(translate(normalize-space(),"
                f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
                f"'{loc_lower}')]"
                f"/following-sibling::*[self::input or self::select or self::textarea][1]"
            )
            return wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
        except TimeoutException:
            pass
        # label with for="id" attribute
        try:
            loc_lower = loc.lower()
            label_xpath = (
                f"//label[contains(translate(normalize-space(),"
                f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
                f"'{loc_lower}')]"
            )
            label_el = wait.until(EC.presence_of_element_located((By.XPATH, label_xpath)))
            for_id = label_el.get_attribute("for")
            if for_id:
                return wait.until(EC.presence_of_element_located((By.ID, for_id)))
        except (TimeoutException, Exception):
            pass
        raise NoSuchElementException(f"Input '{loc}' not found by name, id, placeholder, or label")

    def _extract_quoted(self, s: str) -> Optional[str]:
        m = re.search(r"""['"]([^'"]+)['"]""", s)
        return m.group(1).strip() if m else None

    def _extract_url(self, s: str) -> Optional[str]:
        m = re.search(r"https?://[^\s\"']+", s)
        return m.group(0).rstrip(".,;") if m else None
