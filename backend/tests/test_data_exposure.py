"""Tests for secret-at-rest redaction (#34) and screenshot redaction fail-safe (#35)."""
import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "k")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "k")
os.environ.setdefault("DATABASE_URL", "postgresql://t:t@localhost/t")
os.environ.setdefault("ANTHROPIC_API_KEY", "k")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest


class TestBrowserJobParamRedaction:
    def test_secrets_redacted_non_secrets_kept(self):
        from app.router.access_router import _redact_job_params
        out = _redact_job_params({
            "portal_url": "https://customs.example.gov",
            "username": "acme_ops",
            "password": "hunter2",
            "api_token": "abc123",
            "shipment_id": "SHP-9",
        })
        assert out["portal_url"] == "https://customs.example.gov"
        assert out["shipment_id"] == "SHP-9"
        assert out["password"] == "[redacted]"
        assert out["username"] == "[redacted]"
        assert out["api_token"] == "[redacted]"


class TestScreenshotRedactionFailSafe:
    def _img(self):
        from PIL import Image
        return Image.new("RGB", (32, 32), (255, 255, 255))

    def test_strict_raises_when_redaction_unavailable(self):
        # pytesseract is not installed in the test env, so redaction cannot run.
        from app.computer_use.screenshot_manager import ScreenshotManager
        mgr = ScreenshotManager(redact_pii=True, strict=True)
        with pytest.raises(RuntimeError):
            mgr._redact_sensitive_regions(self._img())

    def test_non_strict_returns_image(self):
        from app.computer_use.screenshot_manager import ScreenshotManager
        mgr = ScreenshotManager(redact_pii=True, strict=False)
        assert mgr._redact_sensitive_regions(self._img()) is not None
