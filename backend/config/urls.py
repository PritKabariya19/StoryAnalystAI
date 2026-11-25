import time

from django.http import JsonResponse
from django.urls import include, path, re_path

from contact.views import contact
from core.http import api


@api("GET")
def health(request):
    return JsonResponse({"status": "ok", "ts": int(time.time() * 1000)})


urlpatterns = [
    path("api/auth/", include("accounts.urls")),
    path("api/ai/", include("ai.urls")),
    path("api/tests/", include("testruns.urls")),
    path("api/payments/", include("payments.urls")),
    path("api/reports/", include("reports.urls")),
    re_path(r"^api/contact/?$", contact),
    re_path(r"^health/?$", health),
]

handler404 = "core.http.not_found"
handler500 = "core.http.server_error"
