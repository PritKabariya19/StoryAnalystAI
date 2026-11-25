from django.urls import re_path

from testruns import views

urlpatterns = [
    re_path(r"^execute/?$", views.execute),
    re_path(r"^start/?$", views.start),
    re_path(r"^start-batch/?$", views.start_batch),
    re_path(r"^status/(?P<job_id>[^/]+)/?$", views.status),
    re_path(r"^screenshots/(?P<filename>[^/]+)/?$", views.screenshot),
]
