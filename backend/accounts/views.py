import logging
import time

import bcrypt
import jwt
from django.conf import settings
from django.http import JsonResponse

from accounts import profiles, store
from core.http import EMAIL_PATTERN, api, error, is_blank

log = logging.getLogger(__name__)

TOKEN_LIFETIME = 7 * 24 * 60 * 60
MIN_PASSWORD_LENGTH = 6  # same as the signup form


def _password_bytes(password):
    return password.encode()[:72]  # bcrypt's limit; bcryptjs truncates the same way


def _session(user, status=200):
    claims = {key: user.get(key) for key in ("uid", "email", "name", "plan")}
    now = int(time.time())
    token = jwt.encode({**claims, "iat": now, "exp": now + TOKEN_LIFETIME}, settings.JWT_SECRET, algorithm="HS256")
    return JsonResponse({"user": claims, "token": token}, status=status)


@api("POST")
def signup(request):
    email, password, name = (request.json.get(k) for k in ("email", "password", "name"))
    if any(is_blank(v) for v in (email, password, name)):
        return error("Name, email, and password are required.", 400)
    email, name = email.strip().lower(), name.strip()
    if not EMAIL_PATTERN.fullmatch(email):
        return error("Invalid email address.", 400)
    if len(password) < MIN_PASSWORD_LENGTH:
        return error(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", 400)
    try:
        hashed = bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt(10)).decode()
        user = store.create_user(email, name, hashed)
        if user is None:
            return error("Email is already in use.", 400)
        return _session(user, status=201)
    except Exception:
        log.exception("[Auth Signup Error]")
        return error("Failed to create account.", 500)


@api("POST")
def login(request):
    email, password = (request.json.get(k) for k in ("email", "password"))
    if not all(isinstance(v, str) and v for v in (email, password)):
        return error("Email and password are required.", 400)
    try:
        user = store.get_user_by_email(email.strip())
        if not user or not bcrypt.checkpw(_password_bytes(password), user["password"].encode()):
            return error("Invalid email or password.", 401)  # ambiguous for security
        return _session(user)
    except Exception:
        log.exception("[Auth Login Error]")
        return error("Failed to login properly.", 500)


@api("GET", auth=True)
def me(request):
    return JsonResponse({"user": profiles.get_or_create(request.user_claims)})
