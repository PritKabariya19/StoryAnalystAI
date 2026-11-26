"""
Flask Microservice — AI Backend
--------------------------------
Pure JSON API microservice called by the Django backend.
Authentication is handled by the Django backend.

Routes:
  GET  /health              → health check
  POST /analyze             → user story text → test cases / analysis
  POST /explore             → website URL → site structure
  POST /generate-combined   → story + site → combined test cases
  POST /execute             → combined test cases → execution results
  POST /report              → execution results → HTML report
  GET  /screenshots/<f>     → serve screenshot files
"""

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from orchestrator import Orchestrator
from agents.website_explorer import WebsiteExplorerAgent
from agents.website_extractor import WebsiteExtractorAgent
from agents.combined_generator import CombinedGeneratorAgent
from agents.smart_test_generator import SmartTestGeneratorAgent
from agents.test_executor import TestExecutorAgent
from agents.report_agent import ReportAgent
import hmac
import os
import threading
import time
import uuid

app = Flask(__name__)

# CORS — only allow the backend and the dev frontend
CORS(app, origins=[
    os.environ.get("ALLOWED_ORIGIN", "http://localhost:5000"),
    "http://localhost:5173",  # dev
])

# Internal secret — requests must include X-Internal-Secret header
# These values are public (they are in the source), so they protect nothing.
PUBLIC_SECRETS = {"", "dev_secret_change_me", "change_this_to_a_random_secret_string"}
INTERNAL_SECRET = os.environ.get("INTERNAL_API_SECRET") or "dev_secret_change_me"


def check_secret():
    secret = request.headers.get("X-Internal-Secret", "")
    if not hmac.compare_digest(secret.encode(), INTERNAL_SECRET.encode()):
        return jsonify({"error": "Unauthorized — missing internal secret."}), 403
    if INTERNAL_SECRET in PUBLIC_SECRETS and request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "Set INTERNAL_API_SECRET to accept remote callers."}), 403
    return None


class BadInput(ValueError):
    pass


@app.errorhandler(BadInput)
def bad_input(exc):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(Exception)
def unexpected_error(exc):
    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.description}), exc.code
    app.logger.exception("Unhandled error")
    return jsonify({"error": "Internal error in the AI service."}), 500


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadInput("The request body must be a JSON object.")
    return data


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BadInput(f"'{key}' must be text.")
    return value.strip()


def _int(data: dict, key: str, default):
    value = data.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise BadInput(f"'{key}' must be a whole number.")


def _test_cases(data: dict) -> list:
    cases = data.get("test_cases", [])
    if not isinstance(cases, list) or not all(isinstance(tc, dict) for tc in cases):
        raise BadInput("'test_cases' must be a list of objects.")
    return cases

orchestrator = Orchestrator()
explorer     = WebsiteExplorerAgent()
extractor    = WebsiteExtractorAgent()
combiner     = CombinedGeneratorAgent()
smart_gen    = SmartTestGeneratorAgent()
executor     = TestExecutorAgent()
reporter     = ReportAgent()


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "python-ai-microservice"})


@app.route("/analyze", methods=["POST"])
def analyze():
    err = check_secret()
    if err: return err

    data = _body()
    user_story = _text(data, "story")
    if not user_story:
        return jsonify({"error": "No user story provided."}), 400
    try:
        return jsonify(orchestrator.run(user_story))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/explore", methods=["POST"])
def explore():
    err = check_secret()
    if err: return err

    data  = _body()
    url   = _text(data, "url")
    depth = max(0, _int(data, "depth", 1))
    if not url:
        return jsonify({"error": "No URL provided."}), 400
    try:
        return jsonify(explorer.explore(url, depth=min(depth, 2)))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/generate-combined", methods=["POST"])
def generate_combined():
    err = check_secret()
    if err: return err

    data       = _body()
    user_story = _text(data, "story")
    url        = _text(data, "url")
    depth      = max(0, _int(data, "depth", 1))
    if not user_story:
        return jsonify({"error": "No user story provided."}), 400
    if not url:
        return jsonify({"error": "No website URL provided."}), 400
    try:
        story_result = orchestrator.run(user_story)
        story_data   = story_result["analysis"]
        page_data    = explorer.explore(url, depth=min(depth, 2))
        test_cases   = combiner.generate(story_data, page_data)
        counts = {"Positive": 0, "Negative": 0, "Boundary": 0, "Edge Case": 0}
        mapped = sum(1 for tc in test_cases if tc.get("mapped", True))
        for tc in test_cases:
            t = tc.get("type", "Positive")
            if t in counts:
                counts[t] += 1
        return jsonify({
            "story_data": story_data,
            "page_data":  page_data,
            "test_cases": test_cases,
            "summary": {
                "total":    len(test_cases),
                "mapped":   mapped,
                "unmapped": len(test_cases) - mapped,
                "by_type":  counts,
            },
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/execute", methods=["POST"])
def execute():
    err = check_secret()
    if err: return err

    data       = _body()
    test_cases = _test_cases(data)
    headless   = data.get("headless", True) is not False
    workers    = max(1, min(_int(data, "workers", 1), 8))  # 1–8 parallel browser instances

    if not test_cases:
        return jsonify({"error": "No test cases provided."}), 400
    try:
        results = executor.execute_all(test_cases, headless=headless, workers=workers)
        passed  = sum(1 for r in results if r["status"] == "Pass")
        failed  = sum(1 for r in results if r["status"] == "Fail")
        errored = sum(1 for r in results if r["status"] == "Error")
        return jsonify({
            "results": results,
            "summary": {
                "total":   len(results),
                "passed":  passed,
                "failed":  failed,
                "errored": errored,
            },
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# In-memory batch-job store  { job_id -> { status, progress, total, results, summary, error } }
_batch_jobs: dict = {}
BATCH_JOB_TTL = 60 * 60  # seconds a finished job is kept


def _purge_batch_jobs():
    cutoff = time.time() - BATCH_JOB_TTL
    for job_id, job in list(_batch_jobs.items()):
        if job.get("finished_at", cutoff + 1) < cutoff:
            _batch_jobs.pop(job_id, None)


@app.route("/execute-batch", methods=["POST"])
def execute_batch_start():
    """Start a batched test run in background and return a job_id for polling."""
    err = check_secret()
    if err: return err

    data        = _body()
    test_cases  = _test_cases(data)
    start_index = _int(data, "start_index", 0)
    end_index   = _int(data, "end_index", None)
    batch_size  = max(1, _int(data, "batch_size", 5))
    headless    = data.get("headless", True) is not False
    workers     = max(1, min(_int(data, "workers", 1), 8))

    if not test_cases:
        return jsonify({"error": "No test cases provided."}), 400

    start_index = max(0, min(start_index, len(test_cases)))
    end_index = len(test_cases) if end_index is None else max(start_index, min(end_index, len(test_cases)))
    total_in_range = end_index - start_index

    _purge_batch_jobs()

    job_id = str(uuid.uuid4())
    _batch_jobs[job_id] = {
        "status":   "running",
        "progress": 0,
        "total":    total_in_range,
        "results":  [],
        "summary":  None,
        "error":    None,
    }

    def _run():
        try:
            def _on_batch(batch_results, _abs_start, abs_done, _total):
                job = _batch_jobs[job_id]
                job["results"].extend(batch_results)
                job["progress"] = abs_done - start_index
                _batch_jobs[job_id] = job

            all_results = executor.execute_batch(
                test_cases,
                start_index=start_index,
                end_index=end_index,
                batch_size=batch_size,
                headless=headless,
                workers=workers,
                on_batch_done=_on_batch,
            )

            passed  = sum(1 for r in all_results if r["status"] == "Pass")
            failed  = sum(1 for r in all_results if r["status"] == "Fail")
            errored = sum(1 for r in all_results if r["status"] == "Error")

            _batch_jobs[job_id].update({
                "status":   "done",
                "finished_at": time.time(),
                "progress": total_in_range,
                "results":  all_results,
                "summary":  {
                    "total":   len(all_results),
                    "passed":  passed,
                    "failed":  failed,
                    "errored": errored,
                },
            })
        except Exception as exc:
            _batch_jobs[job_id].update({
                "status": "failed",
                "finished_at": time.time(),
                "error":  str(exc),
            })

    threading.Thread(target=_run, daemon=True).start()

    return jsonify({"job_id": job_id, "status": "running", "total": total_in_range})


@app.route("/batch-status/<job_id>", methods=["GET"])
def batch_status(job_id):
    """Poll the status of a batch job."""
    err = check_secret()
    if err: return err

    job = _batch_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job)


@app.route("/report", methods=["POST"])
def generate_report():
    err = check_secret()
    if err: return err

    data = _body()
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return jsonify({"error": "No execution results provided."}), 400
    try:
        html = reporter.generate(data)
        return Response(html, mimetype="text/html")
    except Exception as exc:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Report generation failed: {exc}"}), 500


@app.route("/generate-smart-tests", methods=["POST"])
def generate_smart_tests():
    err = check_secret()
    if err: return err

    data  = _body()
    url   = _text(data, "url")
    query = _text(data, "query")
    user_story = _text(data, "user_story")
    depth = max(0, _int(data, "depth", 1))
    if not url:
        return jsonify({"error": "No URL provided."}), 400
    try:
        result = smart_gen.generate(url, query=query, user_story=user_story, depth=min(depth, 2))
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/extract", methods=["POST"])
def extract_info():
    err = check_secret()
    if err: return err

    data  = _body()
    url   = _text(data, "url")
    query = _text(data, "query")
    depth = max(0, _int(data, "depth", 1))
    if not url:
        return jsonify({"error": "No URL provided."}), 400
    try:
        result = extractor.extract(url, query=query, depth=min(depth, 2))
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/screenshots/<path:filename>")
def serve_screenshot(filename):
    err = check_secret()
    if err: return err

    screenshots_dir = os.path.join(os.path.dirname(__file__), "screenshots")
    return send_from_directory(screenshots_dir, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    # Local only by default; set HOST=0.0.0.0 to accept other machines.
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=False)
