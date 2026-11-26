import logging

import requests
from django.conf import settings
from django.http import JsonResponse

from ai import mocks, usage
from core.http import api, error, is_blank, to_int, upstream_error

log = logging.getLogger(__name__)

AI_TIMEOUT = 300  # seconds; a large Gemini request can take several minutes


class UpstreamFailed(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _proxy_to_python(path, body):
    """Return the Python service's JSON, or raise UpstreamFailed."""
    try:
        response = requests.post(
            f"{settings.PYTHON_AI_URL}{path}",
            json=body,
            headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET},
            timeout=AI_TIMEOUT,
        )
        if response.ok:
            return response.json()
        detail = upstream_error(response) or f"status {response.status_code}"
        status = 400 if response.status_code == 400 else 502
        raise UpstreamFailed(f"The AI service could not complete the request: {detail}", status)
    except requests.Timeout:
        raise UpstreamFailed("The AI service took too long to respond. Please try again.", 504)
    except (requests.RequestException, ValueError) as exc:
        log.warning("[AI] %s unavailable: %s", path, exc)
        raise UpstreamFailed("The AI service is unavailable. Please try again.")


def _generate(request, path, body, demo_data):
    """Run one generation. It counts against the plan only if it succeeds."""
    uid = request.user_claims["uid"]
    try:
        reserved = usage.reserve(uid)
    except Exception:
        log.exception("[AI] Could not check usage")
        return error("Could not check your usage limit. Please try again.", 503)
    if not reserved["allowed"]:
        return error(
            f"Usage limit reached. You have used {reserved['usageCount']}/{reserved['limit']} generations "
            f"on the {reserved['plan']} plan. Please upgrade.",
            429,
            upgradeRequired=True,
        )

    try:
        data = _proxy_to_python(path, body)
    except UpstreamFailed as exc:
        log.warning("[AI] %s failed: %s", path, exc)
        try:
            usage.refund(uid)
        except Exception:
            log.exception("[AI] Could not refund usage")
        if settings.AI_DEMO_DATA:
            return JsonResponse({**demo_data(), "mock": True, "mockNote": mocks.MOCK_NOTE})
        return error(str(exc), exc.status)

    used = {"usageCount": reserved["usageCount"]} if "usageCount" in reserved else {}
    return JsonResponse({**data, "mock": False, **used})


def _depth(request):
    return min(to_int(request.json.get("depth", 1), 1), 2)


@api("POST", auth=True)
def generate_story(request):
    requirement = request.json.get("requirement")
    if is_blank(requirement):
        return error("Requirement text is required.", 400)
    return _generate(request, "/analyze", {"story": requirement},
                     lambda: mocks.story_analysis(requirement))


@api("POST", auth=True)
def explore_website(request):
    url = request.json.get("url")
    if is_blank(url):
        return error("URL is required.", 400)
    return _generate(request, "/explore", {"url": url, "depth": _depth(request)},
                     lambda: mocks.exploration(url))


@api("POST", auth=True)
def combined(request):
    requirement, url = request.json.get("requirement"), request.json.get("url")
    if is_blank(requirement) or is_blank(url):
        return error("Both requirement and URL are required.", 400)
    return _generate(request, "/generate-combined",
                     {"story": requirement, "url": url, "depth": _depth(request)},
                     lambda: mocks.combined(requirement, url))


@api("POST", auth=True)
def extract_info(request):
    url, query = request.json.get("url"), request.json.get("query") or ""
    if is_blank(url) or not isinstance(query, str):
        return error("URL is required.", 400)
    return _generate(request, "/extract", {"url": url, "query": query, "depth": _depth(request)},
                     lambda: mocks.extraction(url, query))


@api("POST", auth=True)
def smart_tests(request):
    url = request.json.get("url")
    query = request.json.get("query") or ""
    user_story = request.json.get("user_story") or ""
    if is_blank(url) or not all(isinstance(v, str) for v in (query, user_story)):
        return error("URL is required, and query and user_story must be text.", 400)
    return _generate(request, "/generate-smart-tests",
                     {"url": url, "query": query, "user_story": user_story, "depth": _depth(request)},
                     lambda: mocks.smart_tests(url, query, user_story))
