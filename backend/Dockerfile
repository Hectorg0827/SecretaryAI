# Pin the Debian release: unqualified python:3.12-slim now resolves to Debian 13,
# whose fonts break the Playwright 1.48 browser dependency installer.
FROM python:3.12-bookworm

WORKDIR /app

# System deps for Playwright (Chromium) and cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libnss3 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the browser and its native libraries for the pinned Playwright version.
# Build MUST fail on a missing Chromium executable or missing apt dependency:
# a healthy /health endpoint alone does not certify browser automation.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium

# Application code
COPY app/       ./app/
COPY tasks/     ./tasks/
COPY celery_app.py .
COPY scripts/verify_playwright_runtime.py ./scripts/verify_playwright_runtime.py

# Run as non-root user — prevents container-escape privilege escalation
RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup --no-create-home appuser \
    && chown -R appuser:appgroup /app \
    && { [ -d /ms-playwright ] && chown -R appuser:appgroup /ms-playwright || true; }

USER appuser

# Verify that the *runtime, non-root user* can launch headless Chromium,
# render a local-only page, and produce a PNG; never access the network.
RUN python scripts/verify_playwright_runtime.py

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

# Production: multiple uvicorn workers; override CMD in docker-compose for dev.
# --proxy-headers/--forwarded-allow-ips make request.client.host the REAL client
# behind nginx/Railway (default trusts only 127.0.0.1, so every request looked
# like it came from the proxy and the per-IP login limiter throttled everyone
# together). This container must only be reachable through the proxy.
# Bind to $PORT when the platform injects one. Railway, Render, Heroku and
# Cloud Run all assign a port and route only to that port; an app hardcoded to
# 8000 is unreachable there and the deploy fails its health check with no
# obvious cause. Falls back to 8000 for local use. docker-compose.prod.yml
# overrides this command with an explicit port and is unaffected.
# Shell form with `exec` so uvicorn replaces the shell as PID 1 and still
# receives SIGTERM for graceful shutdown.
CMD ["sh", "-c", "exec uvicorn app.main:app \
     --host 0.0.0.0 \
     --port ${PORT:-8000} \
     --workers 2 \
     --proxy-headers \
     --forwarded-allow-ips '*' \
     --log-level info"]
