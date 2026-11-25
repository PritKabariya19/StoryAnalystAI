"""
Report Agent
-------------
Consumes test-execution results (from TestExecutorAgent) and produces a
self-contained HTML report with:

  - Summary section  (total / pass / fail / error / pass-rate / comment)
  - Detailed results  (grouped by feature, negative-test verdicts, fields
                       filled, step log and an inline screenshot per case)
  - Conclusion        (failure patterns + next-step recommendations)

Usage
-----
  from agents.report_agent import ReportAgent
  html = ReportAgent().generate(exec_data)
  # exec_data = {"results": [...], "summary": {...}}  (summary is ignored;
  # counts are always derived from results)
"""

from __future__ import annotations

import base64
import html as _html
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_SCREENSHOTS_DIR = (Path(__file__).parent.parent / "screenshots").resolve()
_MAX_SHOT_BYTES  = 3 * 1024 * 1024   # skip embedding absurdly large images


class ReportAgent:

    def generate(self, exec_data: dict) -> str:
        """Build and return a self-contained HTML report string."""
        results = [r for r in (exec_data.get("results") or []) if isinstance(r, dict)]

        # Always derive counts from the results — a client-supplied summary can
        # be stale or partial (batch runs, re-saved reports).
        total   = len(results)
        passed  = sum(1 for r in results if _status(r) == "Pass")
        failed  = sum(1 for r in results if _status(r) == "Fail")
        errored = total - passed - failed
        rate    = round(passed / total * 100) if total else 0

        by_feature: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            by_feature[str(r.get("feature") or "General")].append(r)

        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sections = [self._feature_section(f, c) for f, c in sorted(by_feature.items())]

        return _TEMPLATE.format(
            generated_at    = generated_at,
            total           = total,
            passed          = passed,
            failed          = failed,
            errored         = errored,
            rate            = rate,
            rate_color      = "#22c55e" if rate >= 80 else ("#f59e0b" if rate >= 50 else "#ef4444"),
            comment         = _esc(self._overall_comment(total, rate, failed, errored)),
            detail_sections = "\n".join(sections) or '<p class="empty">No results.</p>',
            patterns_html   = self._bullet_html(self._failure_patterns(results)),
            steps_html      = self._bullet_html(self._next_steps(results)),
        )

    # ── Section builders ──────────────────────────────────────────────────

    def _feature_section(self, feature: str, cases: list[dict]) -> str:
        rows = []
        for r in cases:
            status     = _status(r)
            status_cls = {"Pass": "pass", "Fail": "fail"}.get(status, "error")
            url        = str(r.get("page_url") or "")
            url_html   = (f'<a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(url)}</a>'
                          if re.match(r"^(https?|file)://", url, re.I) else _esc(url or "—"))
            log_text   = r.get("log") or ""
            rows.append(f"""
  <div class="tc-card {status_cls}-card">
    <div class="tc-header">
      <span class="tc-id">{_esc(r.get("tc_id") or r.get("testId") or "")}</span>
      <span class="tc-condition">{_esc(r.get("condition") or r.get("title") or "")}</span>
      <span class="status-badge {status_cls}">{_esc(status)}</span>
      <span class="duration">{_duration(r)} s</span>
    </div>
    <div class="tc-meta">
      <span>👤 {_esc(r.get("user_role") or "—")}</span>
      <span>🌐 {url_html}</span>
    </div>
    {self._error_block(r)}
    {self._negative_block(r)}
    {self._fields_block(r)}
    {f'<div class="log-block">{_esc(log_text)}</div>' if log_text else ""}
    {self._screenshot_block(r, status)}
  </div>""")

        return f"""
<section class="feature-section">
  <h2 class="feature-heading">📂 {_esc(feature)}</h2>
  {"".join(rows)}
</section>"""

    def _screenshot_block(self, r: dict, status: str) -> str:
        # screenshot_path ("screenshots/x.png") or the normalised URL
        # ("/api/tests/screenshots/x.png" / "http://host/.../x.png").
        ref = r.get("screenshot_path") or r.get("screenshot") or ""
        if not ref:
            return ""
        fname = Path(str(ref).split("?")[0].replace("\\", "/")).name
        abs_path = (_SCREENSHOTS_DIR / fname).resolve()
        label = "📸 Screenshot" if status == "Pass" else "📸 Failure Screenshot"
        # Guard against path traversal and missing / oversized files.
        if (abs_path.parent == _SCREENSHOTS_DIR and abs_path.is_file()
                and abs_path.stat().st_size <= _MAX_SHOT_BYTES):
            data = base64.b64encode(abs_path.read_bytes()).decode()
            return (
                '<div class="screenshot-block">'
                f'<p class="screenshot-label">{label}</p>'
                f'<img src="data:image/png;base64,{data}" alt="{_esc(label)}" loading="lazy" />'
                '</div>'
            )
        return f'<p class="missing-shot">⚠️ Screenshot not available: {_esc(fname)}</p>'

    def _error_block(self, r: dict) -> str:
        msg = r.get("error_message") or r.get("error")
        if not msg:
            return ""
        return f'<div class="error-block"><strong>Error:</strong> {_esc(msg)}</div>'

    def _negative_block(self, r: dict) -> str:
        """Expected vs. found error message + final verdict (negative tests,
        CAPTCHA blocks)."""
        verdict  = r.get("final_verdict")
        expected = r.get("expected_error_message")
        found    = r.get("error_message_found")
        if not (verdict or expected or found):
            return ""
        rows = []
        if expected:
            rows.append(f"<tr><th>Expected error</th><td>{_esc(expected)}</td></tr>")
        if found:
            rows.append(f"<tr><th>Error found</th><td>{_esc(found)}</td></tr>")
        if r.get("message_match") is not None:
            rows.append(f"<tr><th>Message match</th><td>{'✅ yes' if r.get('message_match') else '❌ no'}</td></tr>")
        if r.get("block_reason"):
            rows.append(f"<tr><th>Block reason</th><td>{_esc(r.get('block_reason'))}</td></tr>")
        if verdict:
            rows.append(f"<tr><th>Verdict</th><td><strong>{_esc(verdict)}</strong></td></tr>")
        return f'<table class="kv-table">{"".join(rows)}</table>'

    def _fields_block(self, r: dict) -> str:
        fills = r.get("fields_filled")
        if not isinstance(fills, list) or not fills:
            return ""
        body = "".join(
            f"<tr><td>{_esc(f.get('field'))}</td><td>{_esc(f.get('value'))}</td>"
            f"<td>{_esc(f.get('status'))}</td></tr>"
            for f in fills if isinstance(f, dict)
        )
        return ('<details class="fields-block"><summary>Fields filled '
                f'({len(fills)})</summary><table class="kv-table">'
                '<tr><th>Field</th><th>Value</th><th>Result</th></tr>'
                f'{body}</table></details>')

    # ── Text helpers ────────────────────────────────────────────────────────

    def _overall_comment(self, total: int, rate: int, failed: int, errored: int) -> str:
        if total == 0:
            return "No test results were provided."
        if rate == 100:
            return "All test cases passed. The feature appears stable and ready for review."
        if rate >= 80:
            return f"Most tests passed ({rate}%). {failed + errored} case(s) need attention before release."
        if rate >= 50:
            return f"Only {rate}% of tests passed. Several failures detected — investigate before proceeding."
        return f"Critical failure rate detected ({100 - rate}% failures). The feature requires immediate fixes."

    def _failure_patterns(self, results: list[dict]) -> list[str]:
        failures = [r for r in results if _status(r) != "Pass"]
        if not failures:
            return ["No failures detected — all test cases passed."]

        patterns: list[str] = []
        features = {str(r.get("feature") or "General") for r in failures}
        patterns.append(f"Failures observed in feature(s): {', '.join(sorted(features))}.")

        url_failures: dict[str, int] = defaultdict(int)
        for r in failures:
            url_failures[str(r.get("page_url") or "unknown")] += 1
        top_url, count = max(url_failures.items(), key=lambda x: x[1])
        if count > 1:
            patterns.append(f"Most failures originate from: {top_url} ({count} cases).")

        msgs = [str(r.get("error_message") or "").lower() for r in failures]
        captcha = sum(1 for r in failures if r.get("block_reason") == "captcha_detected")
        if captcha:
            patterns.append(f"{captcha} case(s) were blocked by a CAPTCHA and could not be automated.")
        if any("not found" in m for m in msgs):
            patterns.append("Several steps failed because expected UI elements were not found — possible selector mismatch or page structure change.")
        if any("url mismatch" in m for m in msgs):
            patterns.append("URL assertion failures detected — redirect or navigation behaviour may have changed.")
        if any("timeout" in m or "timed out" in m for m in msgs):
            patterns.append("Timeout errors present — page may be slow or elements not rendering in time.")
        if any(r.get("message_match") is False for r in failures):
            patterns.append("Negative tests showed no error or the wrong error message — validation messages may be missing or worded differently.")
        return patterns

    def _next_steps(self, results: list[dict]) -> list[str]:
        failures = [r for r in results if _status(r) != "Pass"]
        if not failures:
            return ["No action required — all tests pass. Consider expanding the test suite with more edge cases."]
        steps = [
            "Review failure screenshots and logs to pinpoint the root cause for each failing test.",
            "Fix identified bugs in the application and re-run the failing test cases.",
            "Check that all form selectors (name, id, label) in automation_steps match the current page HTML.",
        ]
        if any(r.get("block_reason") == "captcha_detected" for r in failures):
            steps.append("For CAPTCHA-blocked cases: test against a staging environment with CAPTCHA disabled or using test keys, or run with the browser visible and solve it manually.")
        if any(_status(r) == "Error" and r.get("block_reason") != "captcha_detected" for r in failures):
            steps.append("Investigate 'Error' status cases — these indicate navigation problems, unreachable hosts or browser crashes.")
        steps.append("Once fixes are applied, regenerate combined test cases and run the full execution again.")
        return steps

    def _bullet_html(self, items: list[str]) -> str:
        return "".join(f"<li>{_esc(i)}</li>" for i in items)


# ── Helpers ────────────────────────────────────────────────────────────────

def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _status(r: dict) -> str:
    s = str(r.get("status") or "").strip().capitalize()
    return s if s in ("Pass", "Fail", "Error") else "Error"


def _duration(r: dict) -> float:
    try:
        return round(float(r.get("duration_seconds") or r.get("execTime") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


# ── HTML Template ──────────────────────────────────────────────────────────

_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Test Execution Report</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0d0f1a; color: #e2e4f0; line-height: 1.6; }}
    a    {{ color: #5eead4; }}

    /* Header */
    .report-header {{ background: linear-gradient(135deg,#1a1d30,#12162a); padding: 2.5rem 3rem; border-bottom: 1px solid rgba(255,255,255,0.08); }}
    .report-title   {{ font-size: 1.8rem; font-weight: 800; margin-bottom: .25rem;
                       background: linear-gradient(90deg,#7c6fff,#5eead4); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }}
    .report-sub     {{ font-size: .85rem; color: #8b90a8; }}

    /* Summary cards */
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 1rem; margin: 2rem 3rem; }}
    .summary-card {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 1.2rem; text-align: center; }}
    .summary-card .value {{ font-size: 2.2rem; font-weight: 800; }}
    .summary-card .label {{ font-size: .78rem; color: #8b90a8; text-transform: uppercase; letter-spacing: .08em; margin-top: .25rem; }}
    .pass-value   {{ color: #22c55e; }}
    .fail-value   {{ color: #ef4444; }}
    .error-value  {{ color: #f59e0b; }}
    .rate-value   {{ color: {rate_color}; }}

    /* Comment box */
    .comment-box {{ margin: 0 3rem 2rem; background: rgba(124,111,255,0.08); border: 1px solid rgba(124,111,255,0.25); border-radius: 10px; padding: 1rem 1.4rem; font-size: .9rem; color: #c4c8e8; }}

    /* Section headings */
    .section-heading {{ font-size: 1.2rem; font-weight: 700; margin: 2.5rem 3rem 1rem; padding-bottom: .5rem; border-bottom: 1px solid rgba(255,255,255,0.08); color: #a5b4fc; }}

    /* Feature */
    .feature-section  {{ margin: 0 3rem 2rem; }}
    .feature-heading  {{ font-size: 1rem; font-weight: 700; margin-bottom: 1rem; color: #c4b5fd; background: rgba(255,255,255,0.03); padding: .5rem .9rem; border-radius: 6px; border-left: 3px solid #7c6fff; }}

    /* TC Card */
    .tc-card {{ border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; margin-bottom: .9rem; overflow: hidden; background: rgba(255,255,255,0.02); }}
    .pass-card  {{ border-left: 4px solid #22c55e; }}
    .fail-card  {{ border-left: 4px solid #ef4444; }}
    .error-card {{ border-left: 4px solid #f59e0b; }}

    .tc-header {{ display: flex; flex-wrap: wrap; align-items: center; gap: .7rem; padding: .75rem 1rem; background: rgba(255,255,255,0.025); }}
    .tc-id       {{ font-family: monospace; font-size: .78rem; color: #8b90a8; min-width: 60px; }}
    .tc-condition{{ font-size: .87rem; flex: 1; min-width: 160px; }}
    .duration    {{ font-size: .75rem; color: #8b90a8; margin-left: auto; }}

    .status-badge {{ font-size: .72rem; font-weight: 700; padding: .2rem .6rem; border-radius: 999px; }}
    .pass  {{ background: rgba(34,197,94,0.15);  color: #86efac; }}
    .fail  {{ background: rgba(239,68,68,0.15);  color: #fca5a5; }}
    .error {{ background: rgba(245,158,11,0.15); color: #fde68a; }}

    .tc-meta  {{ display: flex; gap: 1.5rem; flex-wrap: wrap; padding: .4rem 1rem; font-size: .75rem; color: #8b90a8; }}
    .error-block {{ margin: .5rem 1rem; padding: .6rem .9rem; background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.2); border-radius: 6px; font-size: .82rem; color: #fca5a5; }}
    .log-block {{ margin: .5rem 1rem 1rem; padding: .55rem .9rem; background: rgba(0,0,0,0.25); border-radius: 6px; font-family: 'Courier New',monospace; font-size: .72rem; color: #8b90a8; white-space: pre-wrap; max-height: 120px; overflow-y: auto; }}

    /* Screenshot */
    .screenshot-block {{ margin: .75rem 1rem 1rem; }}
    .screenshot-label {{ font-size: .75rem; color: #f59e0b; margin-bottom: .4rem; }}
    .screenshot-block img {{ max-width: 100%; border-radius: 6px; border: 1px solid rgba(255,255,255,0.1); }}
    .missing-shot {{ margin: .5rem 1rem; font-size: .78rem; color: #f59e0b; }}
    .kv-table {{ margin: .5rem 1rem; border-collapse: collapse; font-size: .78rem; }}
    .kv-table th, .kv-table td {{ text-align: left; padding: .25rem .7rem .25rem 0; vertical-align: top; }}
    .kv-table th {{ color: #8b90a8; font-weight: 600; white-space: nowrap; }}
    .fields-block {{ margin: .5rem 1rem; font-size: .78rem; color: #c4c8e8; }}
    .fields-block summary {{ cursor: pointer; color: #a5b4fc; }}
    .empty {{ margin: 0 3rem; color: #8b90a8; }}

    /* Conclusion */
    .conclusion-box {{ margin: 0 3rem; background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; padding: 1.5rem; }}
    .conclusion-box h3 {{ font-size: .9rem; color: #a5b4fc; margin: 0 0 .75rem; text-transform: uppercase; letter-spacing: .07em; }}
    .conclusion-box ul {{ padding-left: 1.3rem; }}
    .conclusion-box li {{ font-size: .87rem; margin-bottom: .4rem; color: #c4c8e8; }}

    footer {{ text-align: center; padding: 2rem; font-size: .75rem; color: #4b5180; margin-top: 3rem; border-top: 1px solid rgba(255,255,255,0.06); }}

    @media print {{
      body {{ background: #fff; color: #111; }}
      .log-block {{ max-height: none; }}
      .fields-block {{ display: block; }}
      .report-header {{ background: #f0f0f5; }}
      .tc-card, .comment-box, .conclusion-box {{ border-color: #ccc; }}
      .log-block {{ background: #f5f5f5; color: #333; }}
    }}
  </style>
</head>
<body>

<header class="report-header">
  <div class="report-title">🧪 Test Execution Report</div>
  <div class="report-sub">Generated by StoryAnalyst AI · {generated_at}</div>
</header>

<!-- ── SUMMARY ── -->
<h2 class="section-heading">📊 Summary</h2>
<div class="summary-grid">
  <div class="summary-card"><div class="value">{total}</div><div class="label">Total</div></div>
  <div class="summary-card"><div class="value pass-value">{passed}</div><div class="label">Passed</div></div>
  <div class="summary-card"><div class="value fail-value">{failed}</div><div class="label">Failed</div></div>
  <div class="summary-card"><div class="value error-value">{errored}</div><div class="label">Errors</div></div>
  <div class="summary-card"><div class="value rate-value">{rate}%</div><div class="label">Pass Rate</div></div>
</div>
<div class="comment-box">💬 {comment}</div>

<!-- ── DETAILED RESULTS ── -->
<h2 class="section-heading">🔍 Detailed Results</h2>
{detail_sections}

<!-- ── CONCLUSION ── -->
<h2 class="section-heading">📝 Conclusion &amp; Recommendations</h2>
<div class="conclusion-box">
  <h3>🔴 Failure Patterns</h3>
  <ul>{patterns_html}</ul>
  <h3 style="margin-top:1.2rem">✅ Recommended Next Steps</h3>
  <ul>{steps_html}</ul>
</div>

<footer>StoryAnalyst AI · Automated Test Report · {generated_at} · For internal use only</footer>
</body>
</html>
"""
