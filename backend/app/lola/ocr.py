"""
Optional text recognition (OCR) for scanned PDFs and photos of documents.

Off unless ``LOLA_OCR=1`` AND the two system programs are installed
(``pdftoppm`` from poppler-utils and ``tesseract`` with the English and
Spanish language packs). Without them a scanned PDF is refused exactly as
before, so turning OCR on can never make extraction less safe.

How it stays safe:
* the programs are run directly (no shell), with a scrubbed environment and a
  hard time budget for the whole file; a slow file is stopped, not waited for;
* pages are rendered at a capped size (``-scale-to``) so a crafted page size
  can't produce a giant image; photos are checked by Pillow for size before
  anything else touches them (decompression-bomb guard) and re-encoded;
* at most ``MAX_OCR_PAGES`` pages and ``MAX_CONCURRENT`` OCR jobs per worker
  (a busy worker refuses instead of queueing CPU-heavy work);
* the bytes live only in a private temporary folder (mode 0700) that is
  deleted as soon as the text is read; nothing is stored or logged;
* the text is still untrusted data: it goes through the normal attachment
  rules, and the result is flagged ``ocr=True`` so clients warn the user that
  recognised numbers must be checked against the original.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

MAX_OCR_PAGES = 10
MAX_CONCURRENT = 2
TIME_BUDGET_SECONDS = 60.0
RENDER_MAX_PX = 2400               # longest side of a rendered page / photo
MAX_IMAGE_PIXELS = 40_000_000      # photos larger than this are refused
LANGUAGES = "eng+spa"

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class OCRUnavailable(RuntimeError):
    """OCR is switched off or the programs are not installed."""


class OCRFailed(RuntimeError):
    """OCR ran but could not produce text; ``code`` is client-facing."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def enabled() -> bool:
    return os.environ.get("LOLA_OCR", "").strip() == "1"


def programs_installed() -> bool:
    return bool(shutil.which("pdftoppm") and shutil.which("tesseract"))


def available() -> bool:
    return enabled() and programs_installed()


def _env() -> dict:
    # Only what the programs need: no credentials or proxy settings leak in.
    keep = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TESSDATA_PREFIX") if k in os.environ}
    keep["OMP_THREAD_LIMIT"] = "1"   # one core per page; MAX_CONCURRENT bounds the rest
    return keep


def _run(args: list[str], deadline: float) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OCRFailed("too_slow")
    try:
        done = subprocess.run(args, capture_output=True, timeout=remaining, env=_env(),
                              stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        raise OCRFailed("too_slow") from None
    except OSError:
        raise OCRUnavailable("program could not start") from None
    if done.returncode != 0:
        raise OCRFailed("ocr_failed")
    return done.stdout


def _recognise(image: Path, deadline: float) -> str:
    out = _run(["tesseract", str(image), "stdout", "-l", LANGUAGES, "--psm", "3"], deadline)
    return out.decode("utf-8", errors="replace")


def _with_slot(fn):
    if not _slots.acquire(blocking=False):
        raise OCRFailed("ocr_busy")
    try:
        return fn()
    finally:
        _slots.release()


def pdf_text(data: bytes, total_pages: int) -> tuple[str, int, bool]:
    """Text of the first pages of a scanned PDF → (text, pages_read, truncated)."""
    if not available():
        raise OCRUnavailable("off")
    pages = max(1, min(total_pages or 1, MAX_OCR_PAGES))

    def work():
        deadline = time.monotonic() + TIME_BUDGET_SECONDS
        with tempfile.TemporaryDirectory(prefix="lola-ocr-") as tmp:
            os.chmod(tmp, 0o700)
            src = Path(tmp) / "in.pdf"
            src.write_bytes(data)
            _run(["pdftoppm", "-f", "1", "-l", str(pages), "-gray", "-png",
                  "-scale-to", str(RENDER_MAX_PX), str(src), str(Path(tmp) / "p")], deadline)
            src.unlink()
            images = sorted(Path(tmp).glob("p*.png"))
            if not images:
                raise OCRFailed("ocr_no_text")
            parts = [_recognise(img, deadline) for img in images]
        return parts

    parts = _with_slot(work)
    return "\n".join(parts), pages, (total_pages or 0) > pages


def image_text(data: bytes) -> str:
    """Text of a photo or scan (PNG / JPEG)."""
    if not available():
        raise OCRUnavailable("off")
    from PIL import Image, ImageOps, UnidentifiedImageError

    # Cheap header check first (no pixels decoded), so oversized images are
    # refused without taking a slot.
    try:
        with Image.open(io.BytesIO(data)) as probe:
            w, h = probe.size
            fmt = probe.format
    except Image.DecompressionBombError:
        raise OCRFailed("image_too_large") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise OCRFailed("unreadable_image") from None
    if w * h > MAX_IMAGE_PIXELS:
        raise OCRFailed("image_too_large")
    if w < 16 or h < 16 or fmt not in ("PNG", "JPEG"):
        raise OCRFailed("unreadable_image")

    def work():
        # The memory-heavy decode / resize happens only while holding a slot,
        # so a busy worker refuses before allocating any full-size image.
        try:
            with Image.open(io.BytesIO(data)) as src:
                img = ImageOps.exif_transpose(src).convert("L")
                img.thumbnail((RENDER_MAX_PX, RENDER_MAX_PX))
                clean = io.BytesIO()
                img.save(clean, format="PNG")      # re-encoded: no metadata, one known format
        except Image.DecompressionBombError:
            raise OCRFailed("image_too_large") from None
        except (UnidentifiedImageError, OSError, ValueError):
            raise OCRFailed("unreadable_image") from None
        deadline = time.monotonic() + TIME_BUDGET_SECONDS
        with tempfile.TemporaryDirectory(prefix="lola-ocr-") as tmp:
            os.chmod(tmp, 0o700)
            path = Path(tmp) / "in.png"
            path.write_bytes(clean.getvalue())
            return _recognise(path, deadline)

    return _with_slot(work)
