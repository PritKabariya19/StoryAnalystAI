from django.urls import re_path

from accounts import views

urlpatterns = [
    re_path(r"^signup/?$", views.signup),
    re_path(r"^login/?$", views.login),
    re_path(r"^me/?$", views.me),
]
