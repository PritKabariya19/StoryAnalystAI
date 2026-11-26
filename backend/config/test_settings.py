"""Settings for the test suite. Outside services are switched off first, because
load_dotenv() never overrides a variable that is already set."""
import os

for _name in ("FIREBASE_SERVICE_ACCOUNT_BASE64", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET",
              "RAZORPAY_WEBHOOK_SECRET", "STRIPE_SECRET_KEY", "PAYMENTS_MOCK",
              "AI_DEMO_DATA", "TRUSTED_PROXY_COUNT"):
    os.environ[_name] = ""
os.environ["JWT_SECRET"] = "test-only-jwt-secret-0123456789-abcdefgh"
os.environ["INTERNAL_API_SECRET"] = "test-only-internal-secret"
os.environ["PYTHON_AI_URL"] = "http://ai.invalid"

from config.settings import *  # noqa: E402,F401,F403
