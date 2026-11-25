"""In-memory job store and background runners. Jobs are lost on restart."""
import logging
import math
import re
import secrets
import threading
import time

import requests
from django.conf import settings

from core.http import upstream_error

log = logging.getLogger(__name__)

jobs = {}


def headers():
    return {"X-Internal-Secret": settings.INTERNAL_API_SECRET}


def exec_timeout(count, workers, headless):
    """Seconds to wait for a synchronous run. Scales with the number of test
    cases (and allows for manual CAPTCHA solving when the browser is shown)."""
    per_test = 90 if headless else 180
    return max(600, math.ceil(count / max(1, workers)) * per_test)


def failure_message(exc):
    response = getattr(exc, "response", None)
    return (upstream_error(response) if response is not None else None) or str(exc)


def normalise_results(results):
    """Unify the id field and route screenshot paths through this server."""
    for index, result in enumerate(results):
        result["testId"] = (
            result.get("tc_id") or result.get("id") or result.get("testId") or f"TC-{index + 1:03d}"
        )
        raw = result.get("screenshot_path") or result.get("screenshot") or ""
        if raw:
            name = re.sub(r"^[/\\]?screenshots[/\\]", "", raw).replace("\\", "/")
            result["screenshot"] = f"/api/tests/screenshots/{name}"
    return results


def count_statuses(results):
    return {
        key: sum(1 for r in results if r.get("status") == value)
        for key, value in (("passed", "Pass"), ("failed", "Fail"), ("errored", "Error"))
    }


def execute_on_python(test_cases, workers, headless):
    response = requests.post(
        f"{settings.PYTHON_AI_URL}/execute",
        json={"test_cases": test_cases, "headless": headless, "workers": workers},
        headers=headers(),
        timeout=exec_timeout(len(test_cases), workers, headless),
    )
    response.raise_for_status()
    data = response.json()
    data["results"] = normalise_results(data.get("results") or [])
    return data


def create(total, **extra):
    job_id = f"job_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    jobs[job_id] = {
        "status": "running", "progress": 0, "total": total, "results": [],
        "summary": None, "error": None, "startedAt": time.monotonic(), **extra,
    }
    return job_id


def progress(job):
    """Current progress. Single-call jobs get a synthetic trickle (a step every
    3s, capped at 85%) so the UI does not look stuck."""
    if job["status"] != "running" or not job.get("trickle"):
        return job["progress"]
    ticks = int((time.monotonic() - job["startedAt"]) // 3)
    return min(ticks * math.ceil(job["total"] * 0.05), int(job["total"] * 0.85))


def _run_single(job_id, test_cases, workers, headless):
    total = len(test_cases)
    try:
        data = execute_on_python(test_cases, workers, headless)
        results = data["results"]
        jobs[job_id] = {
            "status": "done", "progress": total, "total": total, "results": results,
            "summary": data.get("summary") or {"total": len(results), **count_statuses(results)},
            "error": None,
        }
    except Exception as exc:
        log.error("[tests/start] Python failed: %s", exc)
        jobs[job_id] = {
            "status": "failed", "progress": 0, "total": total, "results": [],
            "summary": {"total": total, "passed": 0, "failed": total, "errored": total},
            "error": failure_message(exc),
        }


def _poll_batch(job_id, python_job_id):
    """Every 3s, copy the Python batch job's state into the local job."""
    while jobs[job_id]["status"] == "running":
        time.sleep(3)
        try:
            response = requests.get(
                f"{settings.PYTHON_AI_URL}/batch-status/{python_job_id}", headers=headers(), timeout=8
            )
            response.raise_for_status()
            data = response.json()
            job = jobs[job_id]
            total = job["total"]
            results = normalise_results(data.get("results") or [])
            update = {
                **job,
                "progress": data["progress"] if data.get("progress") is not None else job["progress"],
                "results": results,
                "summary": data.get("summary") or {"total": total, **count_statuses(results)},
            }
            if data.get("status") == "done":
                update.update(status="done", progress=total)
            elif data.get("status") == "failed":
                update.update(status="failed", error=data.get("error") or "Unknown batch error")
            jobs[job_id] = update
        except Exception as exc:
            log.warning("[tests/start-batch] Poll error: %s", exc)


def run_single_in_background(job_id, test_cases, workers, headless):
    threading.Thread(target=_run_single, args=(job_id, test_cases, workers, headless), daemon=True).start()


def poll_batch_in_background(job_id, python_job_id):
    threading.Thread(target=_poll_batch, args=(job_id, python_job_id), daemon=True).start()
