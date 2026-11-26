import os

# Tests never call Gemini; config.py only needs the variable to exist.
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["INTERNAL_API_SECRET"] = "test-only-internal-secret"
os.environ["BLOCK_PRIVATE_URLS"] = ""
