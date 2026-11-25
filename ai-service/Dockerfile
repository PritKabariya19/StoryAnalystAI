FROM python:3.13-slim

WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONUNBUFFERED=1

COPY requirements.txt .
# Playwright's bundled Node needs libatomic1, which the slim image lacks.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1
RUN pip install --no-cache-dir -r requirements.txt gunicorn \
    && playwright install --with-deps chromium

COPY . .

# One worker: the latest report is held in memory.
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 8 --timeout 0 --access-logfile -
