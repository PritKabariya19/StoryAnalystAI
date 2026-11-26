import json
import logging
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from core import firebase, middleware


class ApiTestCase(SimpleTestCase):
    """Runs against the real views with Firebase off and a temporary user file."""

    def setUp(self):
        assert not firebase.is_ready(), "Tests must not reach the real Firebase project."
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        override = override_settings(USERS_FILE=Path(folder.name) / "users.json")
        override.enable()
        self.addCleanup(override.disable)
        # Each test starts with fresh rate limits
        limits = mock.patch.object(middleware.time, "monotonic",
                                   side_effect=self._clock())
        limits.start()
        self.addCleanup(limits.stop)

    _offset = 0

    def _clock(self):
        ApiTestCase._offset += 10**6
        start = ApiTestCase._offset
        return lambda: start

    def post(self, path, body=None, token=None, **extra):
        if token:
            extra["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(path, json.dumps(body or {}), content_type="application/json", **extra)

    def get(self, path, token=None, **extra):
        if token:
            extra["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.get(path, **extra)

    def signup(self, email="a@x.test", password="secret123"):
        response = self.post("/api/auth/signup", {"email": email, "password": password, "name": "N"})
        assert response.status_code == 201, response.content
        data = response.json()
        return data["token"], data["user"]["uid"]


class FakeSnapshot:
    def __init__(self, data):
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class FakeRef:
    def __init__(self, docs, key):
        self.docs, self.key = docs, key

    def get(self, transaction=None):
        return FakeSnapshot(self.docs.get(self.key))

    def set(self, data, merge=False):
        self.docs[self.key] = {**(self.docs.get(self.key, {}) if merge else {}), **data}


class FakeTransaction:
    def set(self, ref, data, merge=False):
        ref.set(data, merge)


class FakeFirestore:
    """Just enough of Firestore for user documents."""

    def __init__(self):
        self.docs = {}

    def collection(self, name):
        docs = self.docs

        class Collection:
            def document(self, key):
                return FakeRef(docs, f"{name}/{key}")

        return Collection()

    def transaction(self):
        return FakeTransaction()
