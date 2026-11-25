"""Firebase Admin setup. Everything degrades to "mock mode" without credentials."""
import base64
import json
import logging
import os

import firebase_admin
from firebase_admin import credentials, firestore

log = logging.getLogger(__name__)


def _init():
    encoded = os.getenv("FIREBASE_SERVICE_ACCOUNT_BASE64")
    if not encoded:
        log.warning("FIREBASE_SERVICE_ACCOUNT_BASE64 not set — Firebase Admin bypassed.")
        return
    try:
        account = json.loads(base64.b64decode(encoded))
        firebase_admin.initialize_app(
            credentials.Certificate(account),
            {"projectId": os.getenv("FIREBASE_PROJECT_ID") or account.get("project_id")},
        )
        log.info("Firebase Admin initialized successfully.")
    except Exception as exc:
        log.error("Firebase Admin initialization failed. Invalid credential format or corrupted base64. %s", exc)


_init()


def is_ready():
    return bool(firebase_admin._apps)


def db():
    return firestore.client() if is_ready() else None
