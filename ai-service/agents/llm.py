"""The one place that calls Gemini."""
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL

TIMEOUT_MS = 240_000  # below the backend's 300s limit

_client = None


def generate(prompt: str, system: str = None, as_json: bool = False) -> str:
    global _client
    if _client is None:
        # v1beta: system instructions are rejected on v1
        _client = genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(
            api_version="v1beta", timeout=TIMEOUT_MS))
    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if as_json else None,
        ),
    )
    return (response.text or "").strip()
