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
    def __init__(self, redact_pii: bool = True, strict: bool = False):
        self._redact_pii = redact_pii
        # Fail-closed: when strict, refuse to return a screenshot if PII
        # redaction could not actually run (e.g. pytesseract missing), rather
        # than silently sending an unredacted image to the model.
        self._strict = strict

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
        Convert image to text via pytesseract (if available) and black-out any
        bounding boxes that match SSN, credit-card, or bank-account patterns.
        Falls back to pass-through if pytesseract or its data files are absent.
        """
        import re
        PII_PATTERNS = [
            re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),      # SSN  xxx-xx-xxxx
            re.compile(r'\b(?:\d[ -]?){13,16}\b'),      # Credit card 13-16 digits
            re.compile(r'\b\d{9,18}\b'),                 # Bank account / routing
        ]
        try:
            import pytesseract
            from PIL import ImageDraw

            data = pytesseract.image_to_data(
                img, output_type=pytesseract.Output.DICT, config='--psm 11'
            )
            draw = ImageDraw.Draw(img)
            n = len(data['text'])
            for i in range(n):
                word = data['text'][i].strip()
                if not word:
                    continue
                if any(pat.search(word) for pat in PII_PATTERNS):
                    x, y, w, h = (
                        data['left'][i], data['top'][i],
                        data['width'][i], data['height'][i],
                    )
                    draw.rectangle([x, y, x + w, y + h], fill='black')
            log.debug("Screenshot PII scan complete")
        except Exception as exc:
            # pytesseract not installed / data files missing / other error.
            if self._strict:
                # Fail closed — do not hand an unredacted screenshot to the model.
                raise RuntimeError(
                    f"Screenshot PII redaction unavailable and strict mode is on: {exc}"
                ) from exc
            log.warning(
                "PII redaction skipped (%s) — screenshot may contain sensitive data. "
                "Install pytesseract or enable strict mode to fail closed.",
                exc,
            )
        return img


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
