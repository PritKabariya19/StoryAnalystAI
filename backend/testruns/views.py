import logging
from urllib.parse import quote

import requests
from django.conf import settings
from django.http import FileResponse, JsonResponse, StreamingHttpResponse

from core.http import api, error, to_int
from testruns import jobs

log = logging.getLogger(__name__)


def _run_options(request):
    """Return (test_cases, workers, headless) from the request body."""
    test_cases = request.json.get("test_cases")
    workers = max(1, min(to_int(request.json.get("workers", 1), 1), 8))
    headless = request.json.get("headless", True) is not False
    if not isinstance(test_cases, list) or not all(isinstance(tc, dict) for tc in test_cases):
        test_cases = []
    return test_cases, workers, headless


@api("POST", auth=True)
def execute(request):
    """Synchronous run, kept for backward compatibility."""
    test_cases, workers, headless = _run_options(request)
    if not test_cases:
        return error("No test cases provided.", 400)
    try:
        data = jobs.execute_on_python(test_cases, workers, headless)
    except (requests.RequestException, ValueError) as exc:
        log.error("[tests/execute] Python error: %s", exc)
        return error("Python executor failed: " + jobs.failure_message(exc), 500)
    return JsonResponse({**data, "mock": False})


@api("POST", auth=True)
def start(request):
    test_cases, workers, headless = _run_options(request)
    if not test_cases:
        return error("No test cases provided.", 400)
    job_id = jobs.create(len(test_cases), trickle=True, owner=request.user_claims["uid"])
    jobs.run_single_in_background(job_id, test_cases, workers, headless)
    return JsonResponse({"jobId": job_id, "status": "running", "total": len(test_cases)})


@api("POST", auth=True)
def start_batch(request):
    test_cases, workers, headless = _run_options(request)
    if not test_cases:
        return error("No test cases provided.", 400)

    count = len(test_cases)
    start_index = max(0, min(to_int(request.json.get("start_index", 0), 0), count))
    end_index = max(start_index, min(to_int(request.json.get("end_index"), count), count))
    total = end_index - start_index
    if not total:
        return error("The batch range contains no test cases.", 400)

    try:
        response = requests.post(
            f"{settings.PYTHON_AI_URL}/execute-batch",
            json={
                "test_cases": test_cases,
                "start_index": start_index,
                "end_index": end_index,
                "batch_size": max(1, to_int(request.json.get("batch_size", 5), 5)),
                "headless": headless,
                "workers": workers,
            },
            headers=jobs.headers(),
            timeout=15,
        )
        response.raise_for_status()
        python_job_id = response.json()["job_id"]
    except (requests.RequestException, ValueError, KeyError) as exc:
        log.error("[tests/start-batch] Could not start Python job: %s", exc)
        return error("Failed to start batch job: " + jobs.failure_message(exc), 500)

    job_id = jobs.create(total, owner=request.user_claims["uid"])
    jobs.poll_batch_in_background(job_id, python_job_id)
    return JsonResponse({"jobId": job_id, "status": "running", "total": total})


@api("GET", auth=True)
def status(request, job_id):
    job = jobs.jobs.get(job_id)
    if job is None or job["owner"] != request.user_claims["uid"]:
        return error("Job not found or expired.", 404)
    return JsonResponse({
        "status": job["status"],
        "progress": jobs.progress(job),
        "total": job["total"],
        "results": job["results"],
        "summary": job["summary"] or {"total": job["total"], **jobs.count_statuses(job["results"])},
        "error": job["error"],
    })


@api("GET")
def screenshot(request, filename):
    """Serve a screenshot from local disk, falling back to the Python service.
    <img> tags cannot send a bearer token, so the random part of the file name
    is what keeps a screenshot private."""
    if filename in (".", "..") or "\\" in filename or "\0" in filename:
        return error("Screenshot not found.", 404)

    local = settings.SCREENSHOTS_DIR / filename
    if local.is_file():
        return FileResponse(local.open("rb"), content_type="image/png")

    try:
        upstream = requests.get(
            f"{settings.PYTHON_AI_URL}/screenshots/{quote(filename)}",
            headers=jobs.headers(), stream=True, timeout=10
        )
        upstream.raise_for_status()
    except requests.RequestException as exc:
        log.error("[tests/screenshots] Image not found: %s %s", filename, exc)
        return error("Screenshot not found.", 404)
    return StreamingHttpResponse(upstream.iter_content(64 * 1024), content_type="image/png")
