"""Dual authentication: local JWT first, then Firebase ID token."""
import logging

import jwt
from django.conf import settings
from firebase_admin import auth as firebase_auth

from core import firebase

log = logging.getLogger(__name__)


def verify_token(token):
    """Return the token's claims, or None if neither scheme accepts it."""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256", "HS384", "HS512"])
    except jwt.PyJWTError:
        pass  # expired, or a Firebase token

    if firebase.is_ready():
        try:
            return firebase_auth.verify_id_token(token)
        except Exception as exc:
            log.error("[AUTH] Firebase verification failed: %s", exc)
    return None
