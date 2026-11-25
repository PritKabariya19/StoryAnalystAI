import logging

import requests
from django.conf import settings
from django.http import JsonResponse

from ai import mocks
from core import firebase
from core.http import api, error, is_blank, to_int, upstream_error

log = logging.getLogger(__name__)

PLAN_LIMITS = {"free": 10, "pro": 100, "premium": 9999}


def _check_and_increment_usage(uid):
    """Check and bump the user's usage count in Firestore."""
    try:
        firestore = firebase.db()
        if firestore is None:
            return {"allowed": True}  # mock mode

        ref = firestore.collection("users").document(uid)
        snapshot = ref.get()
        if not snapshot.exists:
            ref.set({"plan": "free", "usageCount": 0}, merge=True)
            return {"allowed": True}

        data = snapshot.to_dict()
        plan = data.get("plan", "free")
        count = data.get("usageCount") or 0
        limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        if count >= limit:
            return {"allowed": False, "plan": plan, "usageCount": count, "limit": limit}

        ref.update({"usageCount": count + 1})
        return {"allowed": True, "plan": plan, "usageCount": count + 1}
    except Exception as exc:
        log.warning("Firestore tracking disabled or failed (checking local fallback). %s", exc)
        return {"allowed": True}


def _proxy_to_python(path, body):
    """Return the Python service's JSON, or None if the call failed."""
    try:
        response = requests.post(
            f"{settings.PYTHON_AI_URL}{path}",
            json=body,
            headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET},
            timeout=120,
        )
        if not response.ok:
            log.warning("[AI] %s failed: %s", path, upstream_error(response) or response.status_code)
            return None
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("[AI] %s unavailable: %s", path, exc)
        return None


def _depth(request):
    return min(to_int(request.json.get("depth", 1), 1), 2)


def _limit_reached():
    return error("Usage limit reached.", 429, upgradeRequired=True)


@api("POST", auth=True)
def generate_story(request):
    requirement = request.json.get("requirement")
    if is_blank(requirement):
        return error("Requirement text is required.", 400)

    usage = _check_and_increment_usage(request.user_claims["uid"])
    if not usage["allowed"]:
        return error(
            f"Usage limit reached. You have used {usage['usageCount']}/{usage['limit']} generations "
            f"on the {usage['plan']} plan. Please upgrade.",
            429,
            upgradeRequired=True,
        )
    used = {"usageCount": usage["usageCount"]} if "usageCount" in usage else {}

    data = _proxy_to_python("/analyze", {"story": requirement})
    if data is not None:
        return JsonResponse({**data, "mock": False, **used})
    return JsonResponse({
        "analysis": mocks.user_stories(requirement),
        "mock": True,
        "mockNote": mocks.MOCK_NOTE,
        **used,
    })


@api("POST", auth=True)
def explore_website(request):
    url = request.json.get("url")
    if is_blank(url):
        return error("URL is required.", 400)
    if not _check_and_increment_usage(request.user_claims["uid"])["allowed"]:
        return _limit_reached()

    data = _proxy_to_python("/explore", {"url": url, "depth": _depth(request)})
    if data is not None:
        return JsonResponse({**data, "mock": False})
    return JsonResponse(mocks.exploration(url))


@api("POST", auth=True)
def combined(request):
    requirement, url = request.json.get("requirement"), request.json.get("url")
    if is_blank(requirement) or is_blank(url):
        return error("Both requirement and URL are required.", 400)
    if not _check_and_increment_usage(request.user_claims["uid"])["allowed"]:
        return _limit_reached()

    data = _proxy_to_python("/generate-combined", {"story": requirement, "url": url, "depth": _depth(request)})
    if data is not None:
        return JsonResponse({**data, "mock": False})
    return JsonResponse(mocks.combined(requirement, url))


@api("POST", auth=True)
def extract_info(request):
    url, query = request.json.get("url"), request.json.get("query", "")
    if is_blank(url):
        return error("URL is required.", 400)
    if not _check_and_increment_usage(request.user_claims["uid"])["allowed"]:
        return _limit_reached()

    data = _proxy_to_python("/extract", {"url": url, "query": query or "", "depth": _depth(request)})
    if data is not None:
        return JsonResponse({**data, "mock": False})
    return JsonResponse(mocks.extraction(url, query))


@api("POST", auth=True)
def smart_tests(request):
    url = request.json.get("url")
    query, user_story = request.json.get("query", ""), request.json.get("user_story", "")
    if is_blank(url):
        return error("URL is required.", 400)
    if not _check_and_increment_usage(request.user_claims["uid"])["allowed"]:
        return _limit_reached()

    data = _proxy_to_python("/generate-smart-tests", {
        "url": url, "query": query or "", "user_story": user_story or "", "depth": _depth(request),
    })
    if data is not None:
        return JsonResponse({**data, "mock": False})
    return JsonResponse(mocks.smart_tests(url, query, user_story))
