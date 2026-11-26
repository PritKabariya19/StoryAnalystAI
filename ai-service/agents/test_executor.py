"""
Test Executor Agent — Parallel Edition (Specific Error Matching)

Negative test validation rules:
  PASS  → only when the SPECIFIC expected error message is found on page
  FAIL  → when a WRONG error message is found, or NO error message at all
  Every negative result logs: expected_error_message, error_message_found,
  message_match (bool), and a human-readable reason.
"""

import math
import mimetypes
import os
import re
import secrets
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

from agents.text_match import has_word
from agents.url_guard import BlockedURL, browser_options, check_url

SCREENSHOTS_DIR   = Path(__file__).parent.parent / "screenshots"
WAIT_TIMEOUT      = 5   # seconds per element wait
PAGE_LOAD_TIMEOUT = 30  # seconds per page load (hard cap)
PAGE_SETTLE_MAX   = 6   # seconds max to wait for document ready + DOM quiet
CLICK_SETTLE_MAX  = 3   # seconds max to wait after a click
SHOT_SETTLE_MAX   = 1.5 # seconds max to wait before a screenshot
MAX_WORKERS       = 8
SCREENSHOT_TTL    = 7 * 24 * 60 * 60  # seconds a screenshot is kept
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


class StepNotVerified(Exception):
    """The step could not be performed or checked, so the test cannot Pass."""


# Generator placeholders that mean "some error is shown": verified by
# requiring visible error text.
_GENERIC_ERROR_PAT = re.compile(
    r"validation error|error message shown|appropriate error|error/validation message"
    r"|^error for .+| is required$"
)
# Generator placeholders that describe behaviour the executor cannot check.
_UNVERIFIABLE_PHRASES = (
    "the page/response reflects", "system responds correctly",
    "edge case safely", "boundary value", "handles the edge",
    "operation completes", "accepts or rejects",
    "action is rejected", "confirmation is shown",
    "form is visible", "form submitted successfully",
    "max length enforced or accepted", "input rejected or sanitized",
    "script not executed, sanitized", "response within 3 seconds",
    "tab order is logical", "page loaded", "navigation menu visible",
)


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
    "incorrect", "bad", "no", "blank",
    "fail", "error", "reject", "block", "deny", "denied", "exceed",
    "too long", "too short", "sql injection", "xss", "script",
    "special char", "special character", "unauthorized", "unauthorised",
    "unauthenticated", "forbidden", "expired", "duplicate", "already exist", "taken",
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
    if explicit_type in ("negative", "edge_case", "edge case"):
        return True

    # 2. 'test_type' field (rule-based / _tc2 format)
    test_type_field = tc.get("test_type", "").lower()
    if test_type_field in ("negative", "security", "boundary", "edge_case"):
        return True

    if explicit_type == "positive":
        return False

    # 3. Keyword heuristic — only for tests with no polarity of their own
    candidate_text = " ".join([
        tc.get("condition", ""),
        tc.get("test_scenario", ""),
        tc.get("title", ""),
        tc.get("feature", ""),
    ]).lower()
    if has_word(candidate_text, _NEGATIVE_KEYWORDS):
        return True

    return False


def _purge_screenshots(directory: Path) -> None:
    """Delete screenshots older than SCREENSHOT_TTL."""
    cutoff = time.time() - SCREENSHOT_TTL
    for path in directory.glob("*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _labels(tc: dict) -> tuple:
    """(feature, condition) for a result. Smart tests name these
    component and test_scenario or title."""
    return (
        tc.get("feature") or tc.get("component") or "",
        tc.get("condition") or tc.get("test_scenario") or tc.get("title") or "",
    )


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

    @property
    def _unverified(self) -> List[str]:
        if not hasattr(self._tls, "unverified"):
            self._tls.unverified = []
        return self._tls.unverified

    @_unverified.setter
    def _unverified(self, value: List[str]) -> None:
        self._tls.unverified = value

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
        _purge_screenshots(screenshots_dir)

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
            futures = {pool.submit(worker_task, chunk): chunk for chunk in chunks}
            for f in as_completed(futures):
                exc = f.exception()
                if exc:
                    print(f"[executor] Worker error: {exc}")
                    # Report the lost tests instead of dropping them
                    message = f"Browser worker crashed: {str(exc)[:200]}"
                    with lock:
                        all_indexed.extend(
                            (index, self._error_result(tc, message).to_dict())
                            for index, tc in futures[f])

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
                        browser = pw.chromium.launch(headless=headless, **browser_options())
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
                    self._downgrade_unverified(result)
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

    def _downgrade_unverified(self, result: "ExecutionResult") -> None:
        """With a step not performed or checked, neither Pass nor Fail can be
        trusted: report Error."""
        if result.status == "Error" or not self._unverified:
            return
        reason = "Not verified: " + "; ".join(self._unverified)
        if result.error_message:
            reason += f" | then failed: {result.error_message}"
        result.status = "Error"
        result.error_message = reason[:600]
        result.final_verdict = f"NOT VERIFIED — {reason[:200]}"
        result.message_match = None
        result.log += f"\n⚠ {reason}"

    @staticmethod
    def _error_result(tc: dict, msg: str) -> "ExecutionResult":
        feature, condition = _labels(tc)
        return ExecutionResult(
            tc_id=tc.get("tc_id", "TC-???"), feature=feature,
            user_role=tc.get("user_role", ""), condition=condition,
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
        try:
            check_url(url)
        except BlockedURL as exc:
            raise StepNotVerified(str(exc))
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
        feature, condition = _labels(tc)
        user_role = tc.get("user_role", "")
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
        self._unverified = []
        executed = 0
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
                            locator_value = (step.get("locator") or {}).get("value")
                            if eff_expected and not locator_value and step.get("type", "url") != "url":
                                return self._evaluate_negative_result(
                                    page, eff_expected, tc_id, feature,
                                    user_role, condition, page_url, start,
                                    screenshots_dir, log_lines, i,
                                    pre_submit_source=pre_submit_source,
                                )
                            if locator_value and eff_expected:
                                # The named element must show the expected error
                                self._run_json_step({**step, "expected": eff_expected}, page)
                                expected_error = ""  # verified here
                            else:
                                self._run_json_step(step, page)
                            executed += 1
                            log_lines.append(f"✔ Step {i}: [ASSERT] {locator_value or step_expected}")
                            continue

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
                    executed += 1

                    # A click / navigation can land on a CAPTCHA (e.g. after submit)
                    if kind in ("click", "navigate"):
                        blocked = self._wait_out_captcha(page, headless, log_lines)
                        if blocked:
                            return self._captcha_result(
                                page, tc_id, feature, user_role, condition,
                                page_url, start, screenshots_dir, log_lines, blocked)
                except StepNotVerified as exc:
                    self._unverified.append(f"step {i}: {exc}")
                    log_lines.append(f"⚠ Step {i} NOT VERIFIED: {exc}")
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
                    if is_negative and expected_error and not isinstance(exc, AssertionError):
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

            if not executed and not self._unverified:
                self._unverified.append("no steps were run")
            if not self._unverified:
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
            )
            shot = self._screenshot(page, tc_id, screenshots_dir)
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
                current_source = page.content() or ""
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
        shot = self._screenshot(page, tc_id, screenshots_dir)
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

    def _find_visible_error_text(self, page) -> List[str]:
        """Scan the page for visible error/validation text.

        Searches (in order):
          1. Elements whose class contains error/invalid/alert/danger/warning
          2. Elements with role="alert"
          3. Toast/snackbar containers
          4. HTML5 validation messages via JS

        Returns a list of individual error texts found (empty list = none).
        """
        found_texts: List[str] = []

        # 0. Messages shown via JS alert() during this test case
        for t in self._alert_texts:
            if t not in found_texts:
                found_texts.append(t)

        def collect(selector: str) -> None:
            try:
                elements = page.locator(selector).all()
            except PlaywrightError:
                return
            for el in elements:
                try:
                    text = _shown_text(el)
                except PlaywrightError:
                    continue
                if text and text not in found_texts:
                    found_texts.append(text)

        # 1. Class-based error elements
        _TR = "translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
        keywords = ("error", "invalid", "alert", "danger", "warning",
                    "validation", "feedback")
        collect("xpath=//*[" + " or ".join(
            f"contains({_TR}, '{k}')" for k in keywords) + "]")

        # 2. role="alert" elements
        collect('[role="alert"]')

        # 3. Toast / snackbar containers
        for sel in ['[class*="toast"]', '[class*="snackbar"]', '[class*="notification"]']:
            collect(sel)

        # 4. HTML5 constraint-validation messages via JS (individual msgs)
        try:
            js_msgs = page.evaluate("""() => {
                var msgs = [];
                document.querySelectorAll('input,select,textarea').forEach(function(el){
                    // :user-invalid = the user edited the field or tried to submit
                    if (el.matches(':user-invalid') && el.checkVisibility() && el.validationMessage) {
                        msgs.push(el.validationMessage);
                    }
                });
                return msgs;
            }""")
            if js_msgs:
                for msg in js_msgs:
                    msg = msg.strip()
                    if msg and msg not in found_texts:
                        found_texts.append(msg)
        except Exception:
            pass

        return found_texts

    def _screenshot(self, page, tc_id, screenshots_dir):
        """Capture the browser viewport (what a user would see, including the
        area scrolled to for the last interaction) after the page settles.
        Returns the relative path, or None if capture failed."""
        try:
            self._wait_for_page(page, SHOT_SETTLE_MAX)
            with self._shot_lock:
                self._shot_counter += 1
                idx = self._shot_counter
            safe_id = re.sub(r"[^\w\-]", "_", tc_id)
            ts    = int(time.time() * 1000)
            # The random part keeps the image private: its URL needs no sign-in.
            fname = f"{idx:04d}_{safe_id[:40]}_{ts}_{secrets.token_hex(16)}.png"
            abs_dir = Path(screenshots_dir).resolve()
            abs_dir.mkdir(parents=True, exist_ok=True)
            png = page.screenshot(timeout=15000)
            if not png or len(png) < 100:
                print(f"[executor] empty screenshot for {tc_id}")
                return None
            (abs_dir / fname).write_bytes(png)
            return f"screenshots/{fname}"
        except Exception as e:
            print(f"[executor] Screenshot error for {tc_id}: {e}")
            return None

    # ── Step interpreter ───────────────────────────────────────────────

    def _run_json_step(self, step: dict, page: Page) -> None:
        action = step.get("action", "").lower()

        if action == "navigate":
            url = step.get("url")
            if not url:
                raise StepNotVerified("navigate step has no URL")
            self._navigate(page, url)
            return

        locator = step.get("locator", {})
        ltype = locator.get("type", "").lower()
        lval = locator.get("value", "")

        def _get_element() -> Optional[Locator]:
            if not lval: return None
            selectors = {
                "id":    f'[id="{_css_string(lval)}"]',
                "name":  f'[name="{_css_string(lval)}"]',
                "css":   f"css={lval}",
                "xpath": f"xpath={lval}",
            }
            if ltype not in selectors:
                return self._find_input(page, lval)
            el = page.locator(selectors[ltype]).first
            el.wait_for(state="attached")
            return el

        if action == "input":
            value = step.get("value", "")
            el = _get_element()
            if not el:
                raise StepNotVerified("input step has no locator")
            fill_result = self._smart_fill_element(page, el, lval or "field", value)
            # Propagate hard failures (element not interactable) as exceptions
            if fill_result["status"].startswith("error"):
                raise ElementBlocked(
                    f"smart_fill failed on '{lval}': {fill_result['status']}"
                )
            if fill_result["status"].startswith("skipped"):
                raise StepNotVerified(f"'{lval}' not filled: {fill_result['status']}")
            return

        if action == "click":
            el = _get_element()
            if el:
                self._click(el, WAIT_TIMEOUT * 1000)
            else:
                # If no strict locator, try smart click with 'value'
                self._smart_click(page, step.get("value", ""))
            self._after_click(page)
            return

        if action == "assert":
            atype = step.get("type", "url")
            expected = (step.get("expected") or "").strip()
            if atype == "url":
                if not expected:
                    raise StepNotVerified("URL assertion has no expected value")
                cur = page.url
                assert expected in cur, f"URL mismatch: '{expected}' not in '{cur}'"
            elif lval:
                try:
                    el = _get_element()
                except PlaywrightError:
                    raise AssertionError(f"Element '{lval}' not found")
                self._assert_element(page, el, lval, expected)
            elif expected:
                src = self._visible_text(page)
                assert expected.lower() in src, f"Text '{expected}' not found on page"
            else:
                raise StepNotVerified("assertion has nothing to check")
            return

        raise StepNotVerified(f"unsupported action '{action}'")

    @staticmethod
    def _assert_element(page: Page, el: Locator, name: str, expected: str) -> None:
        """The element must be visible and, if given, contain the expected text."""
        deadline = time.time() + WAIT_TIMEOUT
        while True:
            shown = _is_shown(el)
            text = ""
            if shown:
                text = el.evaluate(
                    "e => e.type === 'password' ? '' : (e.value || e.innerText || '')")
                if expected.lower() in str(text).lower():
                    return
            if time.time() >= deadline:
                break
            page.wait_for_timeout(250)
        assert shown, f"Element '{name}' is not visible"
        assert False, f"Text '{expected}' not found in element '{name}'"

    def _run_step(self, step: str, page: Page) -> Optional[str]:
        """Run one natural-language step. Returns the kind of action that was
        performed ('navigate', 'click', 'input', 'assert', ...) or None."""
        s  = step.strip()
        sl = s.lower()
        if not s or sl.startswith("#"):
            return None

        # ── Navigate ────────────────────────────────────────────────────
        nav_m = _NAV_PAT.search(s)
        if nav_m:
            url = nav_m.group(1).rstrip("'\".,;")
            self._navigate(page, url)
            return "navigate"

        # ── Clear field ─────────────────────────────────────────────────
        if re.search(r"\bclear\b.*\bfield\b", sl):
            fname = self._extract_quoted(s)
            if not fname:
                raise StepNotVerified("no field name in clear step")
            el = self._find_input(page, fname)
            if el.evaluate("e => e.type") == "checkbox":
                self._smart_fill_element(page, el, fname, "false")
                return "input"
            try:
                el.fill("")
            except PlaywrightError:
                el.evaluate("e => { e.value = ''; }")
            return "input"

        # ── Enter value in field ────────────────────────────────────────
        m = _ENTER_FIELD_PAT.search(s)
        if m:
            value = next((v for v in (m.group("vd"), m.group("vs"), m.group("vb")) if v is not None), "")
            name  = next((v for v in (m.group("nd"), m.group("ns"), m.group("nb")) if v), "").strip()
            try:
                el = self._find_input(page, name)
                fill_result = self._smart_fill_element(page, el, name, value)
                if fill_result["status"].startswith("error"):
                    raise ElementNotFound(
                        f"Could not fill field '{name}': {fill_result['status']}"
                    )
            except ElementNotFound:
                raise
            except Exception as e:
                raise ElementNotFound(f"Could not fill field '{name}': {e}")
            if fill_result["status"].startswith("skipped"):
                raise StepNotVerified(f"'{name}' not filled: {fill_result['status']}")
            return "input"

        # ── Click ───────────────────────────────────────────────────────
        click_m = _CLICK_PAT.search(s)
        if click_m:
            target = click_m.group(1).strip()
            self._smart_click(page, target)
            self._after_click(page)
            return "click"

        # ── Assert URL ──────────────────────────────────────────────────
        if re.search(r"\b(assert|verify|check|confirm)\b", sl) and "url" in sl:
            expected = self._extract_quoted(s) or self._extract_url(s)
            if not expected:
                raise StepNotVerified("URL assertion has no expected value")
            cur = page.url
            assert expected in cur, f"URL mismatch: '{expected}' not in '{cur}'"
            return "assert"

        # ── Assert text on page ─────────────────────────────────────────
        if re.search(r"\b(assert|verify|check|confirm)\b", sl) and \
                re.search(r"\b(page|response|text|message|show|display|reflect)\b", sl):
            expected = self._extract_quoted(s)
            if not expected:
                raise StepNotVerified("assertion has no quoted expected text")
            exp_l = expected.lower()
            if exp_l in self._visible_text(page):
                return "assert"
            if _GENERIC_ERROR_PAT.search(exp_l):
                for _ in range(7):  # allow up to 3s for async errors
                    if self._find_visible_error_text(page):
                        return "assert"
                    page.wait_for_timeout(500)
                assert False, f"Expected '{expected}', but no error message is shown"
            if any(p in exp_l for p in _UNVERIFIABLE_PHRASES):
                raise StepNotVerified(f"cannot check '{expected}' automatically")
            assert False, f"Text '{expected}' not found on page"

        # ── Dropdown ────────────────────────────────────────────────────
        if "select" in sl and any(k in sl for k in ("option", "dropdown", "from")):
            opt = self._extract_quoted(s)
            selects = page.locator("select")
            if opt and selects.count():
                try:
                    index = self._option_index(selects.first, opt, by_value=False)
                    if index is not None:
                        selects.first.select_option(index=index)
                        return
                except PlaywrightError:
                    pass
            raise StepNotVerified("dropdown option was not selected")

        # ── Checkbox ────────────────────────────────────────────────────
        if "checkbox" in sl or "check the" in sl:
            cbs = page.locator("input[type='checkbox']")
            if not cbs.count():
                raise StepNotVerified("no checkbox on the page")
            if not cbs.first.is_checked():
                self._click(cbs.first)
            return

        raise StepNotVerified(f"unrecognised step '{s[:60]}'")

    # ── Smart click (buttons + links + inputs) ─────────────────────────

    def _smart_click(self, page: Page, target: str):
        tl = target.lower()
        clicked = False

        # 1. input[type=submit]
        for el in page.locator("input[type='submit'], input[type='button']").all():
            val = (el.evaluate("e => e.value") or "").strip().lower()
            if val and (tl in val or val in tl):
                self._click(el)
                clicked = True
                break

        # 2. button elements, 3. anchor/link elements
        for tag in ("button", "a"):
            if clicked:
                break
            for el in page.locator(tag).all():
                if tl in _shown_text(el).lower():
                    self._click(el)
                    if tag == "a":
                        page.wait_for_timeout(500)  # wait for navigation
                    clicked = True
                    break

        # 4. XPath fallback with case-insensitive translate
        if not clicked:
            lit = _xpath_literal(tl)
            txt = _XP_LOWER.format("normalize-space()")
            val = _XP_LOWER.format("@value")
            xpath = (
                f"//button[contains({txt},{lit})]"
                f"|//a[contains({txt},{lit})]"
                f"|//*[@role='button'][contains({txt},{lit})]"
                f"|//input[contains({val},{lit}) and (@type='submit' or @type='button')]"
            )
            try:
                el = page.locator(f"xpath={xpath}").first
                el.wait_for(state="visible")
                el.evaluate("e => e.click()")
                clicked = True
            except PlaywrightError:
                pass

        # 5. Any submit
        if not clicked:
            try:
                el = page.locator("button[type='submit'], input[type='submit']")
                if el.count():
                    el.first.evaluate("e => e.click()")
                    clicked = True
            except PlaywrightError:
                pass

        if not clicked:
            raise ElementNotFound(f"Clickable element '{target}' not found")

    # ── Universal Smart Fill ────────────────────────────────────────────

    def _smart_fill_element(
        self,
        page: Page,
        element: Locator,
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
            tag = element.evaluate("e => e.tagName.toLowerCase()")
            input_type = (element.evaluate("e => e.type || e.getAttribute('type')") or "text").lower()
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
                self._type_into(element, str(value))
                result["status"] = f"filled - {input_type or 'text'} input"

            # ── TYPE 2: Select / Dropdown ─────────────────────────────
            elif tag == "select":
                index = self._option_index(element, str(value), by_value=False)
                if index is not None:
                    result["status"] = "filled - dropdown by text"
                else:
                    index = self._option_index(element, str(value), by_value=True)
                    if index is not None:
                        result["status"] = "filled - dropdown by value"
                    else:
                        result["status"] = f"skipped - dropdown has no option '{value}'"
                if index is not None:
                    element.select_option(index=index)

            # ── TYPE 3: Checkbox ──────────────────────────────────────
            elif tag == "input" and input_type == "checkbox":
                wanted = str(value).strip().lower()
                should_check = wanted in ("true", "yes", "1", "on", "check", "checked")
                if not should_check and wanted not in (
                        "false", "no", "0", "off", "uncheck", "unchecked", ""):
                    result["status"] = f"skipped - unclear checkbox value '{value}'"
                else:
                    if element.is_checked() != should_check:
                        self._click(element)
                    result["status"] = (
                        f"filled - checkbox set to {should_check}"
                        if element.is_checked() == should_check
                        else f"error - checkbox could not be set to {should_check}"
                    )

            # ── TYPE 4: Radio Button ──────────────────────────────────
            elif tag == "input" and input_type == "radio":
                name_attr = element.get_attribute("name") or field_name
                clicked = False
                for radio in page.locator(f'[name="{_css_string(name_attr)}"]').all():
                    if (radio.evaluate("e => e.value") or "").lower() == str(value).lower():
                        self._click(radio)
                        clicked = True
                        break
                result["status"] = (
                    "filled - radio selected" if clicked
                    else f"skipped - radio value '{value}' not found"
                )

            # ── TYPE 5: Textarea ──────────────────────────────────────
            elif tag == "textarea":
                self._type_into(element, str(value))
                result["status"] = "filled - textarea"

            # ── TYPE 6: File Upload ───────────────────────────────────
            elif tag == "input" and input_type == "file":
                # Never read a path from the server's disk: upload a
                # placeholder file with the requested name.
                name = str(value).replace("\\", "/").rsplit("/", 1)[-1]
                element.set_input_files({
                    "name": name,
                    "mimeType": mimetypes.guess_type(name)[0] or "application/octet-stream",
                    "buffer": b"StoryAnalystAI test file\n",
                } if name else [])
                result["status"] = "filled - file upload"

            # ── TYPE 7: Date / Time ───────────────────────────────────
            elif tag == "input" and input_type in ("date", "time", "datetime-local", "month", "week"):
                element.evaluate(
                    "(e, v) => { e.value = v;"
                    "e.dispatchEvent(new Event('change', {bubbles:true})); }",
                    str(value),
                )
                result["status"] = f"filled - {input_type} via script"

            # ── TYPE 8: Range / Slider ────────────────────────────────
            elif tag == "input" and input_type == "range":
                element.evaluate(
                    "(e, v) => { e.value = v;"
                    "e.dispatchEvent(new Event('input', {bubbles:true}));"
                    "e.dispatchEvent(new Event('change', {bubbles:true})); }",
                    str(value),
                )
                result["status"] = "filled - range slider via script"

            # ── TYPE 9: Hidden Field ──────────────────────────────────
            elif tag == "input" and input_type == "hidden":
                element.evaluate("(e, v) => { e.value = v; }", str(value))
                result["status"] = "filled - hidden field via script"
                result["warning"] = "field is hidden - filled via JavaScript"

            # ── TYPE 11: Unknown / Unsupported ────────────────────────
            else:
                # Last-resort: try typing anyway
                try:
                    self._type_into(element, str(value))
                    result["status"] = f"filled - fallback send_keys on {tag}[{input_type}]"
                    result["warning"] = "used fallback send_keys for unsupported type"
                except Exception:
                    result["status"] = f"skipped - unsupported type: {tag}[{input_type}]"

        except PlaywrightTimeoutError:
            # Playwright waited for the element to become usable and it never did
            result["status"] = "skipped - element not interactable"
        except Exception as e:
            result["status"] = f"error - {str(e)[:150]}"

        self._fill_log.append(result)
        return result

    # ── Element helpers ─────────────────────────────────────────────────

    @staticmethod
    def _type_into(el: Locator, value: str) -> None:
        """Clear, then type key by key so the page sees real keyboard events
        and invalid characters are rejected the way they are for a user."""
        el.fill("")
        el.press_sequentially(value, timeout=60000)

    @staticmethod
    def _option_index(select: Locator, wanted: str, by_value: bool) -> Optional[int]:
        """Index of the <option> whose value / visible text equals wanted."""
        options = select.evaluate(
            "(s, byValue) => Array.from(s.options, o => byValue ? o.value"
            " : o.text.replace(/\\s+/g, ' ').trim())",
            by_value,
        )
        return options.index(wanted) if wanted in options else None

    def _find_input(self, page: Page, locator: str) -> Locator:
        """Find a form element (input, textarea, or select) by name, id,
        associated label, placeholder or aria-label.

        Strategies are tried in priority order on every poll, so the most
        precise match wins. Visible elements are preferred; a hidden match is
        only returned once half the wait time has passed without a visible one.
        """
        loc = locator.strip()
        css_q = loc.replace("\\", "\\\\").replace("'", "\\'")
        lit = _xpath_literal(loc.lower())
        label_txt = _XP_LOWER.format("normalize-space()")
        field = "self::input or self::select or self::textarea"
        label = f"//label[contains({label_txt},{lit})]"
        strategies = [
            f'[name="{_css_string(loc)}"]',
            f'[id="{_css_string(loc)}"]',
            None,                                                  # <label for="x">
            f"xpath={label}//*[{field}]",                          # <label>Text <input></label>
            f"input[placeholder*='{css_q}' i], textarea[placeholder*='{css_q}' i]",
            f"input[aria-label*='{css_q}' i], textarea[aria-label*='{css_q}' i], select[aria-label*='{css_q}' i]",
            f"xpath={label}/following::*[{field}][1]",             # label then sibling input
        ]

        def _candidates(selector) -> List[Locator]:
            if selector is not None:
                return page.locator(selector).all()
            out: List[Locator] = []
            for lab in page.locator(f"xpath={label}[@for]").all():
                fid = lab.get_attribute("for")
                if fid:
                    out.extend(page.locator(f'[id="{_css_string(fid)}"]').all())
            return out

        started = time.time()
        while time.time() - started < WAIT_TIMEOUT:
            fallback = None
            for selector in strategies:
                try:
                    els = _candidates(selector)
                except PlaywrightError:
                    continue
                for el in els:
                    try:
                        if _is_shown(el):
                            return el
                    except PlaywrightError:
                        continue
                    if fallback is None:
                        fallback = el
            if fallback is not None and time.time() - started >= WAIT_TIMEOUT / 2:
                return fallback
            page.wait_for_timeout(250)

        raise ElementNotFound(
            f"Input '{loc}' not found by name, id, label, placeholder, or aria-label"
        )

    @staticmethod
    def _visible_text(page: Page) -> str:
        """Lower-cased text a user can actually see (plus form field values),
        excluding scripts, attributes and hidden markup."""
        try:
            return (page.evaluate("""() => {
                var t = document.body ? document.body.innerText : '';
                document.querySelectorAll('input,textarea').forEach(function(e){
                  if (e.type !== 'password' && e.value) t += '\\n' + e.value; });
                return t;
            }""") or "").lower()
        except PlaywrightError:
            return (page.content() or "").lower()

    def _extract_quoted(self, s: str) -> Optional[str]:
        m = re.search(r"""['"]([^'"]+)['"]""", s)
        return m.group(1).strip() if m else None

    def _extract_url(self, s: str) -> Optional[str]:
        m = re.search(r"https?://[^\s\"']+", s)
        return m.group(0).rstrip(".,;") if m else None
