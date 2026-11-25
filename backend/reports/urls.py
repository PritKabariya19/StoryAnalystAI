from django.urls import re_path

from reports import views

urlpatterns = [
    re_path(r"^save/?$", views.save),
    re_path(r"^list/?$", views.list_reports),
    re_path(r"^generate-html/?$", views.generate_html),
    re_path(r"^(?P<report_id>[^/]+)/?$", views.delete),
]
