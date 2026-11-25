from django.urls import re_path

from payments import views

urlpatterns = [
    re_path(r"^razorpay/create-order/?$", views.create_order),
    re_path(r"^razorpay/verify/?$", views.verify),
    re_path(r"^razorpay/webhook/?$", views.webhook),
    re_path(r"^stripe/charge/?$", views.stripe_charge),
]
