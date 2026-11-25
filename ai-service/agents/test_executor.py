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
