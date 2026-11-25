"""
Test Executor Agent — Parallel Edition (Specific Error Matching)

Negative test validation rules:
  PASS  → only when the SPECIFIC expected error message is found on page
  FAIL  → when a WRONG error message is found, or NO error message at all
  Every negative result logs: expected_error_message, error_message_found,
  message_match (bool), and a human-readable reason.
"""

import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.sync_api import (
    Browser, Error as PlaywrightError, Locator, Page,
    TimeoutError as PlaywrightTimeoutError, sync_playwright,
)

SCREENSHOTS_DIR   = Path(__file__).parent.parent / "screenshots"
WAIT_TIMEOUT      = 5   # seconds per element wait
PAGE_LOAD_TIMEOUT = 30  # seconds per page load (hard cap)
PAGE_SETTLE_MAX   = 6   # seconds max to wait for document ready + DOM quiet
CLICK_SETTLE_MAX  = 3   # seconds max to wait after a click
SHOT_SETTLE_MAX   = 1.5 # seconds max to wait before a screenshot
MAX_WORKERS       = 8
# In headed mode, how long to wait for a human to solve a CAPTCHA (seconds).
CAPTCHA_MANUAL_WAIT = int(os.environ.get("CAPTCHA_MANUAL_WAIT", "90"))

# Detects an UNSOLVED, user-facing CAPTCHA. Runs in the page.
# - ignores the invisible reCAPTCHA v3 / invisible-v2 badge (size=invisible)
# - ignores tiny / hidden widgets
# - a widget whose response token is already filled counts as solved
_CAPTCHA_DETECT_JS = r"""
(phrases) => {
function vis(el){
  var r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return r.width > 30 && r.height > 30 && s.visibility !== 'hidden'
         && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.1;
}
var solved = Array.prototype.some.call(
  document.querySelectorAll('[name="g-recaptcha-response"],[name="h-captcha-response"],[name="cf-turnstile-response"]'),
  function(t){ return t.value && t.value.length > 0; });
// An open image/puzzle challenge always blocks, solved token or not.
var challenge = ["iframe[src*='recaptcha'][src*='bframe']",
                 "iframe[src*='hcaptcha'][src*='frame=challenge']"];
for (var i = 0; i < challenge.length; i++) {
  var els = document.querySelectorAll(challenge[i]);
  for (var j = 0; j < els.length; j++) if (vis(els[j])) return 'CAPTCHA challenge open';
}
if (!solved) {
  var widgets = ["iframe[src*='recaptcha'][src*='anchor']:not([src*='size=invisible'])",
                 "iframe[src*='hcaptcha'][src*='frame=checkbox']:not([src*='size=invisible'])",
                 "iframe[src*='challenges.cloudflare.com']",
                 "#cf-challenge-running", "#challenge-form"];
  for (var k = 0; k < widgets.length; k++) {
    var w = document.querySelectorAll(widgets[k]);
    for (var m = 0; m < w.length; m++) if (vis(w[m])) return 'CAPTCHA widget (' + widgets[k].split('[')[0] + ')';
  }
}
var t = ((document.title || '') + ' ' +
         (document.body ? document.body.innerText.slice(0, 3000) : '')).toLowerCase();
for (var n = 0; n < phrases.length; n++) if (t.indexOf(phrases[n]) !== -1) return "Bot-check page ('" + phrases[n] + "')";
return null;
}
"""
_CAPTCHA_TEXT = (
    "verify you are human", "verifying you are human", "are you a robot",
    "unusual traffic from your computer", "checking your browser before",
    "complete the security check", "attention required! | cloudflare",
)

# Resolves when the DOM tree has had no node additions/removals for
# `quiet` ms, or after `maxMs`. Attribute/text changes (carousels, clocks,
# CSS animations) are ignored so busy pages still settle quickly.
_SETTLE_JS = r"""
([quiet, maxMs]) => new Promise(function(cb){
var start = Date.now(), last = Date.now();
function done(){ try { obs.disconnect(); } catch(e) {} cb(true); }
function isText(n){ return n.nodeType === 3 || n.nodeType === 8; }
var obs = new MutationObserver(function(muts){
  for (var i = 0; i < muts.length; i++) {
    var m = muts[i], nodes = [].concat([].slice.call(m.addedNodes), [].slice.call(m.removedNodes));
    if (!nodes.every(isText)) { last = Date.now(); return; }
  }
});
try { obs.observe(document.documentElement, {childList: true, subtree: true}); } catch(e) { return cb(false); }
(function tick(){
  var now = Date.now();
  if (now - last >= quiet || now - start >= maxMs) return done();
  setTimeout(tick, 50);
})();
})
"""


_TRANSPARENT_JS = r"""
el => { for (; el; el = el.parentElement)
         if (getComputedStyle(el).opacity === '0') return true;
       return false; }
"""


class ElementNotFound(PlaywrightError):
    """No element matched the step's locator."""


class ElementBlocked(PlaywrightError):
    """The element exists but could not be used."""


def _css_string(s: str) -> str:
    """Escape a string for use inside a quoted CSS attribute selector."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _is_shown(el: Locator) -> bool:
    return el.is_visible() and not el.evaluate(_TRANSPARENT_JS)


def _shown_text(el: Locator) -> str:
    """Text a user can see; empty for hidden elements."""
    return el.inner_text().strip() if _is_shown(el) else ""


def _xpath_literal(s: str) -> str:
    """Safely quote an arbitrary string for use inside an XPath expression."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


_XP_LOWER = "translate({}, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"

# Keywords that indicate a NEGATIVE test case based on its title/condition/scenario.
# If any of these appear in the test case name, it is treated as negative regardless
# of whether the 'type' field was set by the generator.
_NEGATIVE_KEYWORDS = (
    "without", "invalid", "missing", "wrong", "empty",
    "no password", "incorrect", "bad ", "no ", "blank",
    "fail", "error", "reject", "block", "deny", "exceed",
    "too long", "too short", "sql injection", "xss", "script",
    "special char", "unauth", "unauthorized", "forbidden",
    "expired", "duplicate", "already exist", "taken",
)


def _is_negative_tc(tc: dict) -> bool:
    """Return True if this test case is a NEGATIVE (or edge-case) test.

    Detection priority:
    1. Explicit 'type' field set to 'negative' or 'edge_case'  (AI generator)
    2. Explicit 'test_type' field set to 'Negative' or 'Security'
       (rule-based generator / _tc2 helper)
    3. Keyword heuristic on condition / test_scenario / title fields
       (catches cases where the generator forgot to tag the type)
    """
    # 1. Explicit 'type' field (AI generator format)
    explicit_type = tc.get("type", "").lower()
    if explicit_type in ("negative", "edge_case"):
        return True

    # 2. 'test_type' field (rule-based / _tc2 format)
    test_type_field = tc.get("test_type", "").lower()
    if test_type_field in ("negative", "security", "boundary", "edge_case"):
        return True

    # 3. Keyword heuristic — scan the human-readable fields
    candidate_text = " ".join([
        tc.get("condition", ""),
        tc.get("test_scenario", ""),
        tc.get("title", ""),
        tc.get("feature", ""),
    ]).lower()
    if any(kw in candidate_text for kw in _NEGATIVE_KEYWORDS):
        return True

    return False

# Regex: Enter 'value' in the 'fieldname' field
# Enter/type <value> in/into [the] <field name> field|input|box
# Value and name may be "double", 'single' quoted (apostrophes allowed inside
# the other quote style) or bare. Empty values ('') are allowed for
# empty-field negative tests.
_ENTER_FIELD_PAT = re.compile(
    r"""(?:enter|type|input|fill)\s+(?:"(?P<vd>[^"]*)"|'(?P<vs>[^']*)'|(?P<vb>\S+))"""
    r"""\s+(?:in|into)\s+(?:the\s+)?"""
    r"""(?:"(?P<nd>[^"]+)"|'(?P<ns>[^']+)'|(?P<nb>\w[\w\s\-]*?))"""
    r"""\s*(?:field|input|box|textbox|textarea)\b""",
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
        # Per-test-case state lives in thread-local storage: parallel workers
        # share this agent instance and must not clobber each other's logs.
        self._tls = threading.local()

    @property
    def _fill_log(self) -> List[dict]:
        if not hasattr(self._tls, "fill_log"):
            self._tls.fill_log = []
        return self._tls.fill_log

    @_fill_log.setter
    def _fill_log(self, value: List[dict]) -> None:
        self._tls.fill_log = value

    @property
    def _alert_texts(self) -> List[str]:
        if not hasattr(self._tls, "alert_texts"):
            self._tls.alert_texts = []
        return self._tls.alert_texts

    @_alert_texts.setter
    def _alert_texts(self, value: List[str]) -> None:
        self._tls.alert_texts = value

    @staticmethod
    def _resolve_headless(headless: bool) -> bool:
        """A visible browser needs a display. On a server without one
        (Linux, no DISPLAY / WAYLAND_DISPLAY), fall back to headless."""
        if headless:
            return True
        import sys
        if sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        ):
            print("[executor] headless=False requested but no display is "
                  "available — running headless instead.")
            return True
        return False

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

        if not test_cases:
            return []
        headless = self._resolve_headless(headless)
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
            headless:      Headless browser flag.
            workers:       Parallel browser workers per batch.
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
        results: List[Tuple[int, dict]] = []
        # Playwright's sync API is not thread-safe: each worker owns its own
        # instance and browser.
        with sync_playwright() as pw:
            browser: Optional[Browser] = None
            for pos, (original_idx, tc) in enumerate(indexed_cases):
                # (Re)start the browser if we don't have a live one.
                if browser is None or not browser.is_connected():
                    if browser is not None:
                        print("[executor] browser session died — restarting")
                    try:
                        browser = pw.chromium.launch(headless=headless)
                    except Exception as exc:
                        # Can't start the browser: mark this and all remaining
                        # TCs as Error instead of losing the worker's results.
                        msg = f"Could not start browser: {str(exc)[:250]}"
                        print(f"[executor] {msg}")
                        for idx2, tc2 in indexed_cases[pos:]:
                            results.append((idx2, self._error_result(tc2, msg).to_dict()))
                        break

                context = None
                try:
                    # A fresh context per test case: no shared cookies/storage.
                    context = browser.new_context(
                        viewport={"width": 1440, "height": 900},
                    )
                    page = self._new_page(context)
                    result = self._execute_one(tc, page, screenshots_dir, headless)
                except Exception as exc:
                    result = self._error_result(tc, f"Browser error: {str(exc)[:250]}")
                finally:
                    if context is not None:
                        try:
                            context.close()
                        except Exception:
                            pass
                results.append((original_idx, result.to_dict()))
        return results

    @staticmethod
    def _error_result(tc: dict, msg: str) -> "ExecutionResult":
        return ExecutionResult(
            tc_id=tc.get("tc_id", "TC-???"), feature=tc.get("feature", ""),
            user_role=tc.get("user_role", ""), condition=tc.get("condition", ""),
            page_url=tc.get("page_url", "") or "", status="Error",
            duration_seconds=0, error_message=msg, screenshot_path=None,
            log=f"💥 {msg}",
        )

    # ── Page setup ─────────────────────────────────────────────────────────

    def _new_page(self, context) -> Page:
        # Default for every element action; keeps a missing element from
        # blocking for Playwright's 30s default.
        context.set_default_timeout(WAIT_TIMEOUT * 1000)
        page = context.new_page()
        page.on("dialog", self._handle_dialog)
        return page

    def _handle_dialog(self, dialog) -> None:
        """Close a JS dialog and remember its text (so negative tests can match
        error messages shown via alert()). Dismiss is OK for alert() and CANCEL
        for confirm() — never auto-confirms a destructive action on the site
        under test. beforeunload is accepted so navigation isn't cancelled."""
        if dialog.type == "beforeunload":
            dialog.accept()
            return
        text = (dialog.message or "").strip()
        dialog.dismiss()
        if text:
            self._alert_texts.append(text)

    # ── Page readiness / navigation ────────────────────────────────────────

    def _wait_for_page(self, page: Page, max_wait: float = PAGE_SETTLE_MAX) -> None:
        """Wait for the load event and for the DOM to have had no nodes
        added/removed for 400ms (SPA finished rendering). Bounded by
        max_wait in total."""
        end = time.time() + max_wait
        try:
            page.wait_for_load_state("load", timeout=max(0.5, max_wait) * 1000)
        except PlaywrightError:
            pass
        remaining_ms = int(max(0.0, end - time.time()) * 1000)
        if remaining_ms < 100:
            return
        try:
            page.evaluate(_SETTLE_JS, [400, remaining_ms])
        except PlaywrightError:
            pass

    def _navigate(self, page: Page, url: str) -> bool:
        """Load url and wait for it to settle. Returns True if the page load
        hit PAGE_LOAD_TIMEOUT (we then continue with the partial page).
        Network failures raise."""
        timed_out = False
        try:
            # Don't block on slow third-party scripts/ads; readiness is
            # handled by _wait_for_page().
            page.goto(url, wait_until="domcontentloaded",
                      timeout=PAGE_LOAD_TIMEOUT * 1000)
        except PlaywrightTimeoutError:
            timed_out = True
            try:
                page.evaluate("window.stop()")
            except PlaywrightError:
                pass
        self._wait_for_page(page)
        return timed_out

    def _after_click(self, page: Page) -> None:
        """Called by every click handler: let the page react."""
        page.wait_for_timeout(100)
        self._wait_for_page(page, CLICK_SETTLE_MAX)

    @staticmethod
    def _click(el: Locator, timeout: float = 2000) -> None:
        """Click like a user; fall back to a DOM click if that isn't possible."""
        try:
            el.click(timeout=timeout)
        except PlaywrightError:
            el.evaluate("e => e.click()")

    # ── CAPTCHA detection ──────────────────────────────────────────────────

    def _detect_captcha(self, page) -> Optional[str]:
        """Return a short description if the page shows an unsolved,
        user-facing CAPTCHA, else None."""
        try:
            return page.evaluate(_CAPTCHA_DETECT_JS, list(_CAPTCHA_TEXT))
        except Exception:
            return None

    def _wait_out_captcha(self, page, headless: bool, log_lines: List[str]) -> Optional[str]:
        """If a CAPTCHA is showing: in headed mode, give a human
        CAPTCHA_MANUAL_WAIT seconds to solve it in the browser window.
        Returns the reason string if still blocked, else None."""
        reason = self._detect_captcha(page)
        if not reason:
            return None
        if headless:
            return reason
        log_lines.append(f"⚠ {reason} — waiting up to {CAPTCHA_MANUAL_WAIT}s for manual solve...")
        print(f"[executor] {reason}. Solve it in the browser window "
              f"(waiting up to {CAPTCHA_MANUAL_WAIT}s)...")
        deadline = time.time() + CAPTCHA_MANUAL_WAIT
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            if not self._detect_captcha(page):
                self._wait_for_page(page)
                log_lines.append("✔ CAPTCHA solved manually")
                return None
        return reason

    def _captcha_result(self, page, tc_id, feature, user_role, condition,
                        page_url, start, screenshots_dir, log_lines, reason):
        log_lines.append(
            "✘ Blocked by CAPTCHA — automated steps cannot continue. "
            "Test against a staging site with CAPTCHA disabled / test keys, "
            "or enable 'Show browser window' to solve it manually."
        )
        shot = self._screenshot(page, tc_id, screenshots_dir)
        return ExecutionResult(
            tc_id=tc_id, feature=feature, user_role=user_role,
            condition=condition, page_url=page_url,
            status="Error",
            duration_seconds=time.time() - start,
            error_message=f"Blocked by CAPTCHA: {reason}",
            screenshot_path=shot,
            log="\n".join(log_lines),
            block_reason="captcha_detected",
            final_verdict="BLOCKED — site showed a CAPTCHA",
            fields_filled=self._fill_log if self._fill_log else None,
        )

    # ── Execute one TC ──────────────────────────────────────────────────────

    def _execute_one(
        self,
        tc:              dict,
        page:            Page,
        screenshots_dir: Path,
        headless:        bool = True,
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
        self._fill_log = []     # reset per-TC state
        self._alert_texts = []
        start = time.time()

        try:
            # ── Initial navigation ─────────────────────────────────────
            if page_url:
                try:
                    if self._navigate(page, page_url):
                        log_lines.append(
                            f"⚠ Page load exceeded {PAGE_LOAD_TIMEOUT}s at {page_url} "
                            "— continuing with partially loaded page"
                        )
                    log_lines.append(f"✔ Navigated to {page_url}")
                    blocked = self._wait_out_captcha(page, headless, log_lines)
                    if blocked:
                        return self._captcha_result(
                            page, tc_id, feature, user_role, condition,
                            page_url, start, screenshots_dir, log_lines, blocked)
                except PlaywrightError as nav_err:
                    err_str = str(nav_err)[:200]
                    log_lines.append(f"✘ Navigation failed: {err_str}")
                    shot = self._screenshot(page, tc_id, screenshots_dir)
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
            # Determine test polarity using the unified helper that checks
            # explicit type fields AND keyword heuristics on the title/condition.
            is_negative = _is_negative_tc(tc)
            log_lines.append(
                f"ℹ Test polarity: {'NEGATIVE' if is_negative else 'POSITIVE'} "
                f"(type={tc.get('type','')!r}, test_type={tc.get('test_type','')!r})"
            )
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
                            cur_url = page.url.rstrip("/")
                            p_url = page_url.rstrip("/")

                            if cur_url != p_url and p_url not in cur_url:
                                log_lines.append(f"✘ Step {i} FAILED: URL changed to {cur_url}")
                                shot = self._screenshot(page, tc_id, screenshots_dir)
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
                                page, eff_expected, tc_id, feature,
                                user_role, condition, page_url, start,
                                screenshots_dir, log_lines, i,
                                pre_submit_source=pre_submit_source,
                            )
                            return result

                        # Capture page source before click (for silent reload detection)
                        if action == "click" and is_negative:
                            try:
                                pre_submit_source = page.content()
                            except Exception:
                                pass

                        self._run_json_step(step, page)
                        kind = action
                        action_log = step.get('action', 'unknown').upper()
                        val = step.get('url') or step.get('value') or step.get('expected') or step.get('locator', {}).get('value') or ''
                        log_lines.append(f"✔ Step {i}: [{action_log}] {val}")
                    else:
                        kind = None
                        s = step.strip()
                        if not s or s.startswith("#"):
                            continue
                        kind = self._run_step(s, page)
                        log_lines.append(f"✔ Step {i}: {s[:90]}")

                    # A click / navigation can land on a CAPTCHA (e.g. after submit)
                    if kind in ("click", "navigate"):
                        blocked = self._wait_out_captcha(page, headless, log_lines)
                        if blocked:
                            return self._captcha_result(
                                page, tc_id, feature, user_role, condition,
                                page_url, start, screenshots_dir, log_lines, blocked)
                except (AssertionError, PlaywrightError) as exc:

                    # 1. Blocked Action (e.g. Disabled submit button)
                    if is_negative and isinstance(exc, ElementBlocked):
                        # Button was disabled/blocked — still verify the correct error
                        eff_expected = expected_error
                        if not eff_expected:
                            # No expected error specified → structural block is enough
                            log_lines.append(f"✔ Step {i} BLOCKED as expected: {str(exc)[:60]}")
                            shot = self._screenshot(page, tc_id, screenshots_dir)
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
                            page, eff_expected, tc_id, feature,
                            user_role, condition, page_url, start,
                            screenshots_dir, log_lines, i,
                            block_reason_prefix="button_disabled",
                            pre_submit_source=pre_submit_source,
                        )

                    # 2. For negative tests with other exceptions, check error message
                    if is_negative and expected_error:
                        log_lines.append(f"⚠ Step {i} exception: {str(exc)[:80]} — checking error message...")
                        return self._evaluate_negative_result(
                            page, expected_error, tc_id, feature,
                            user_role, condition, page_url, start,
                            screenshots_dir, log_lines, i,
                            pre_submit_source=pre_submit_source,
                        )

                    # 3. Positive test or no expected error → normal failure
                    log_lines.append(f"✘ Step {i} FAILED")
                    log_lines.append(f"   Reason: {str(exc)[:120]}")
                    shot = self._screenshot(page, tc_id, screenshots_dir)
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
                    page, expected_error, tc_id, feature,
                    user_role, condition, page_url, start,
                    screenshots_dir, log_lines, len(steps),
                    pre_submit_source=pre_submit_source,
                )

            log_lines.append("✅ All steps passed.")
            pass_shot = self._screenshot(page, tc_id, screenshots_dir)
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
            shot = self._screenshot(page, tc_id, screenshots_dir)
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
        page,
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
            error_list = self._find_visible_error_text(page)
            if error_list:
                break
            page.wait_for_timeout(500)

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
            shot = self._screenshot(page, tc_id, screenshots_dir)
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
