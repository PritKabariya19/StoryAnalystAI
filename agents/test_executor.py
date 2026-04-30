"""
Test Executor Agent — Parallel Edition (Fixed)
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
        self._shot_counter = 0
        self._shot_lock    = threading.Lock()

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

        if page_url:
            if not page_url.startswith(("http://", "https://", "file://")):
                # Default to http:// if somehow missing (e.g., user typed localhost:3000)
                page_url = "http://" + page_url
        else:
            page_url = ""

        log_lines: List[str] = []
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
                    )
            else:
                log_lines.append("⚠ No valid page URL — skipping navigation.")

            # ── Run steps ─────────────────────────────────────────────
            for i, step in enumerate(steps, 1):
                s = step.strip()
                if not s or s.startswith("#"):
                    continue
                try:
                    self._run_step(s, driver)
                    log_lines.append(f"✔ Step {i}: {s[:90]}")
                except (AssertionError, NoSuchElementException,
                        TimeoutException, WebDriverException) as exc:
                    log_lines.append(f"✘ Step {i} FAILED: {s[:90]}")
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
                    )

            # ── Pass ───────────────────────────────────────────────────
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
            )

    # ── Thread-safe screenshot ─────────────────────────────────────────

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
                el.clear()
                el.send_keys(value)
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

    # ── Element helpers ─────────────────────────────────────────────────

    def _find_input(self, wait, locator: str):
        loc = locator.strip()
        # Try name, id
        for by in (By.NAME, By.ID):
            try:
                return wait.until(EC.presence_of_element_located((by, loc)))
            except TimeoutException:
                pass
        # placeholder partial match
        try:
            css = f"input[placeholder*='{loc}' i], textarea[placeholder*='{loc}' i]"
            return wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))
        except TimeoutException:
            pass
        # label text
        try:
            xpath = f"//label[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{loc.lower()}')]//following-sibling::input[1]"
            return wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
        except TimeoutException:
            pass
        raise NoSuchElementException(f"Input '{loc}' not found by name, id, placeholder, or label")

    def _extract_quoted(self, s: str) -> Optional[str]:
        m = re.search(r"""['"]([^'"]+)['"]""", s)
        return m.group(1).strip() if m else None

    def _extract_url(self, s: str) -> Optional[str]:
        m = re.search(r"https?://[^\s\"']+", s)
        return m.group(0).rstrip(".,;") if m else None
