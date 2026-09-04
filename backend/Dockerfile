FROM python:3.12-slim

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

# Install Playwright Chromium into a shared, non-root-readable location.
# Without PLAYWRIGHT_BROWSERS_PATH the browser lands in /root/.cache, which the
# `appuser` below cannot read — headless workflows then fail at runtime while
# the `|| true` hid the install error at build time.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium --with-deps \
    || echo "WARNING: Playwright Chromium install failed; browser automation will be unavailable"

# Application code
COPY app/       ./app/
COPY tasks/     ./tasks/
COPY celery_app.py .

# Run as non-root user — prevents container-escape privilege escalation
RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup --no-create-home appuser \
    && chown -R appuser:appgroup /app \
    && { [ -d /ms-playwright ] && chown -R appuser:appgroup /ms-playwright || true; }

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Production: multiple uvicorn workers; override CMD in docker-compose for dev.
# --proxy-headers/--forwarded-allow-ips make request.client.host the REAL client
# behind nginx/Railway (default trusts only 127.0.0.1, so every request looked
# like it came from the proxy and the per-IP login limiter throttled everyone
# together). This container must only be reachable through the proxy.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--log-level", "info"]
