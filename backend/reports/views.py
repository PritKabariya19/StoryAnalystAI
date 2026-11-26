import json
import logging
import os
import secrets
import time

import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from google.cloud.firestore_v1.base_query import FieldFilter

from core import firebase
from core.http import api, error, iso_now, upstream_error

log = logging.getLogger(__name__)

# In-memory report store (always works, even without Firestore).
mem_reports = {}

# Firestore rejects documents over 1 MiB.
MAX_DOC_BYTES = 900_000


def _fit_for_firestore(report):
    """Drop the bulky step logs if the report is too large for one document."""
    if len(json.dumps(report).encode()) <= MAX_DOC_BYTES or not isinstance(report["results"], list):
        return report
    return {**report, "results": [
        {k: v for k, v in r.items() if k != "log"} if isinstance(r, dict) else r
        for r in report["results"]
    ]}


def _has_firestore():
    return firebase.is_ready() and bool(os.getenv("FIREBASE_SERVICE_ACCOUNT_BASE64"))


@api("POST", auth=True)
def save(request):
    body = request.json
    if body.get("results") in (None, False, 0, "") or not body.get("type"):
        return error("Results and type are required.", 400)

    report = {
        "userId": request.user_claims["uid"],
        "userEmail": request.user_claims.get("email") or "",
        "type": body["type"],
        "results": body["results"],
        "summary": body.get("summary") or {},
        "metadata": body.get("metadata", {}),
        "createdAt": iso_now(),
        "timestamp": int(time.time() * 1000),
    }

    if _has_firestore():
        try:
            _, ref = firebase.db().collection("reports").add(_fit_for_firestore(report))
            return JsonResponse({"id": ref.id, "saved": True})
        except Exception as exc:
            log.warning("[reports/save] Firestore failed, falling back to memory: %s", exc)

    report_id = f"rep_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    mem_reports[report_id] = {"id": report_id, **report}
    return JsonResponse({
        "id": report_id, "saved": True, "storage": "memory",
        "warning": "Report is only kept in the server's memory and will be lost when it restarts.",
    })


@api("GET", auth=True)
def list_reports(request):
    uid = request.user_claims["uid"]
    in_memory = sorted(
        (r for r in list(mem_reports.values()) if r["userId"] == uid),
        key=lambda r: r["timestamp"],
        reverse=True,
    )

    if not _has_firestore():
        return JsonResponse({"reports": in_memory})
    try:
        query = firebase.db().collection("reports").where(filter=FieldFilter("userId", "==", uid))
        try:
            docs = list(query.order_by("timestamp", direction="DESCENDING").limit(50).stream())
            stored = [{"id": doc.id, **doc.to_dict()} for doc in docs]
        except Exception as exc:
            # Usually the composite index is missing (firestore.indexes.json).
            log.warning("[reports/list] Ordered query failed, sorting here instead: %s", exc)
            stored = sorted(
                ({"id": doc.id, **doc.to_dict()} for doc in query.stream()),
                key=lambda r: r.get("timestamp") or 0, reverse=True,
            )[:50]
        return JsonResponse({"reports": in_memory + stored})
    except Exception as exc:
        log.warning("[reports/list] Firestore failed, returning memory store: %s", exc)
        return JsonResponse({
            "reports": in_memory,
            "warning": "Saved reports could not be loaded. Only reports held in memory are shown.",
        })


@api("DELETE", auth=True)
def delete(request, report_id):
    uid = request.user_claims["uid"]

    report = mem_reports.get(report_id)
    if report is not None:
        if report["userId"] != uid:
            return error("Not authorized.", 403)
        mem_reports.pop(report_id, None)
        return JsonResponse({"deleted": True})

    if not firebase.is_ready():
        return error("Report not found.", 404)

    try:
        ref = firebase.db().collection("reports").document(report_id)
        snapshot = ref.get()
        if not snapshot.exists:
            return error("Report not found.", 404)
        if snapshot.to_dict().get("userId") != uid:
            return error("Not authorized.", 403)
        ref.delete()
    except Exception as exc:
        log.error("[reports/delete] %s", exc)
        return error(f"Failed to delete report: {exc}", 500)
    return JsonResponse({"deleted": True})


@api("POST", auth=True)
def generate_html(request):
    """Proxy to the Python service's /report."""
    results = request.json.get("results")
    if not isinstance(results, list) or not results:
        return error("No results provided to build a report from.", 400)
    try:
        response = requests.post(
            f"{settings.PYTHON_AI_URL}/report",
            json={"results": results, "summary": request.json.get("summary")},
            headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET},
            timeout=120,
        )
    except requests.RequestException as exc:
        log.error("[reports/generate-html] %s", exc)
        return error(f"Failed to generate HTML report: {exc}", 500)

    if not response.ok:
        detail = upstream_error(response) or response.text or f"status {response.status_code}"
        log.error("[reports/generate-html] %s", detail)
        return error(f"Failed to generate HTML report: {detail}", response.status_code)
    return HttpResponse(response.content, content_type="text/html; charset=utf-8")
