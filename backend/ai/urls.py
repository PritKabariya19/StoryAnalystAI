from django.urls import re_path

from ai import views

urlpatterns = [
    re_path(r"^generate-story/?$", views.generate_story),
    re_path(r"^explore-website/?$", views.explore_website),
    re_path(r"^combined/?$", views.combined),
    re_path(r"^extract-info/?$", views.extract_info),
    re_path(r"^smart-tests/?$", views.smart_tests),
]
