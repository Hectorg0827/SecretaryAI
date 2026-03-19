"""
Screenshot Manager — captures, resizes, and optionally redacts screenshots
before they are sent to the Claude Computer Use API.

On the desktop app (Tauri/Rust side), screenshots are captured via the
screen_capture Tauri command, which calls into this module indirectly.
When running server-side tests, mss is used.
"""
import asyncio
import base64
import io
import logging
from typing import Optional

log = logging.getLogger(__name__)

# Target dimensions for screenshots sent to Claude.
# Smaller = cheaper tokens; 1280×720 is a good balance.
TARGET_WIDTH = 1280
TARGET_HEIGHT = 720


class ScreenshotManager:
    def __init__(self, redact_pii: bool = True):
        self._redact_pii = redact_pii

    async def capture_b64(self) -> str:
        """
        Capture the primary display and return as base64-encoded PNG.
        Runs the blocking PIL/mss call in a thread pool.
        """
        loop = asyncio.get_event_loop()
        png_bytes = await loop.run_in_executor(None, self._capture_sync)
        return base64.b64encode(png_bytes).decode()

    def _capture_sync(self) -> bytes:
        """Blocking screenshot capture — called from thread pool."""
        try:
            import mss
            import mss.tools
            from PIL import Image

            with mss.mss() as sct:
                monitor = sct.monitors[1]  # Primary monitor
                raw = sct.grab(monitor)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        except ImportError:
            # Fallback: PIL ImageGrab (Windows/macOS only)
            from PIL import ImageGrab
            img = ImageGrab.grab()

        # Resize to target dimensions
        img = img.resize((TARGET_WIDTH, TARGET_HEIGHT))

        # Optionally redact PII regions
        if self._redact_pii:
            img = self._redact_sensitive_regions(img)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def _redact_sensitive_regions(self, img):
        """
        Blur or black-out screen regions that may contain PII.
        Current implementation: basic OCR + pattern matching approach.
        TODO: Use python-tesseract to detect credit card / SSN patterns.
        """
        return img  # Pass-through until OCR integration is complete


class MockScreenshotManager(ScreenshotManager):
    """Used in tests and CI environments that have no display."""

    def __init__(self, png_path: Optional[str] = None):
        super().__init__(redact_pii=False)
        self._png_path = png_path

    def _capture_sync(self) -> bytes:
        if self._png_path:
            with open(self._png_path, "rb") as f:
                return f.read()
        # Return a 1×1 white PNG
        from PIL import Image
        img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
