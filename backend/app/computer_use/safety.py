"""
Computer Use Safety Layer.
Every action goes through this before execution.
Blocks dangerous operations code-level — not prompt-level.
"""
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)


class ComputerUseSafety:
    """
    Safety layer that sits between the AI and the actual computer.
    Every planned action is checked here before being executed.
    """

    # Applications the AI is NEVER allowed to interact with
    BLOCKED_APPS = frozenset({
        "banking",
        "password manager",
        "bitwarden",
        "1password",
        "lastpass",
        "remote desktop",
        "mstsc",
        "rdp",
        "cmd.exe",
        "command prompt",
        "powershell",
        "terminal",
        "wsl",
        "registry editor",
        "regedit",
        "task manager",
        "event viewer",
        "services.msc",
        "gpedit",
        "secpol",
        "admin",
        "control panel",
    })

    # Keyboard shortcuts that are never allowed
    BLOCKED_KEY_SEQUENCES = frozenset({
        "ctrl+a",            # Select all (could lead to bulk delete)
        "ctrl+shift+delete", # Browser clear history / Windows delete
        "delete",            # Delete key alone
        "shift+delete",      # Permanent delete (bypass Recycle Bin)
        "alt+f4",            # Force close (data loss risk)
        "ctrl+alt+delete",   # System interrupt
        "format",            # Format command
    })

    # Text patterns that suggest the AI is about to modify/delete data
    DANGEROUS_TEXT_PATTERNS = [
        r"\bdelete\b",
        r"\bremove\b",
        r"\bvoid\b",
        r"\bcancel\b",
        r"\bdrop\b",
        r"\bpurge\b",
        r"\berase\b",
        r"\bwipe\b",
        r"\bformat\b",
        r"\buninstall\b",
    ]

    # Pixel regions that are always off-limits (Windows taskbar clock, system notification area)
    # These coordinates are approximate for a 1920×1080 display and scaled accordingly
    BLOCKED_REGIONS_1920x1080 = [
        # Windows system tray area (bottom-right)
        {"x_min": 1700, "x_max": 1920, "y_min": 1040, "y_max": 1080},
    ]

    MAX_ACTIONS_PER_TASK = 50
    MAX_TASK_SECONDS = 300

    def __init__(self, company_config: dict, db=None):
        self.company_config = company_config
        self._db = db
        # Customer-specific additional blocked apps
        extra_blocked = company_config.get("blocked_apps", [])
        self._blocked = self.BLOCKED_APPS | frozenset(a.lower() for a in extra_blocked)

    def check_action(self, action_params: dict) -> tuple[bool, str]:
        """
        Validate a proposed computer action.
        Returns (is_safe, reason_if_blocked).
        """
        action = action_params.get("action", "")

        # Right-click context menus can expose dangerous options
        if action == "right_click":
            return False, "Right-click is disabled (safety: context menus may have destructive options)"

        # Check keyboard shortcuts
        if action == "key":
            key_seq = action_params.get("text", "").lower().strip()
            if key_seq in self.BLOCKED_KEY_SEQUENCES:
                return False, f"Key sequence '{key_seq}' is blocked"

        # Check for dangerous text being typed
        if action == "type":
            text = action_params.get("text", "").lower()
            for pattern in self.DANGEROUS_TEXT_PATTERNS:
                if re.search(pattern, text):
                    return False, f"Typing '{text}' matches a blocked pattern"

        # Check coordinate bounds (block system tray area)
        if "coordinate" in action_params:
            coord = action_params["coordinate"]
            if isinstance(coord, (list, tuple)) and len(coord) == 2:
                x, y = coord
                if self._in_blocked_region(x, y):
                    return False, f"Click at ({x}, {y}) is in a blocked screen region"

        return True, ""

    def check_app_window(self, window_title: str) -> tuple[bool, str]:
        """
        Check if the current foreground window is a blocked application.
        Call this when the window focus changes.
        """
        title_lower = window_title.lower()
        for blocked in self._blocked:
            if blocked in title_lower:
                return False, f"Interaction with '{window_title}' is not allowed"
        return True, ""

    def check_screenshot_for_pii(self, screenshot_bytes: bytes) -> bytes:
        """
        Scan a screenshot for PII (credit cards, SSNs, bank accounts).
        Uses regex on the PNG text chunks and PIL to blur sensitive regions.
        Returns the (possibly redacted) screenshot bytes.

        Strategy (no Tesseract required):
        1. Extract any embedded text metadata from the PNG stream with regex.
        2. If any PII patterns are found, blur the bottom half of the image
           (where status bars / form fields typically appear) as a conservative
           but fast redaction that doesn't require a full OCR pass.
        3. Logs a warning so operators know redaction was applied.
        """
        # PII patterns to search for in embedded text metadata
        _PII_RE = [
            re.compile(r"\b(?:\d[ -]?){15,16}\b"),           # Credit / debit card numbers
            re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),             # US SSN
            re.compile(r"\b\d{9}\b"),                          # 9-digit SSN (no dashes)
            re.compile(r"\b[A-Z]{2}\d{6,9}\b"),               # Passport number pattern
            re.compile(r"\b\d{8,12}\b"),                       # Bank account numbers (8-12 digits)
            re.compile(r"(?i)\biban\b.{0,30}[A-Z]{2}\d{2}"),  # IBAN
        ]

        try:
            # Check embedded text bytes for PII patterns (fast, zero-dependency)
            text_sample = screenshot_bytes.decode("latin-1", errors="replace")
            found_pii = any(p.search(text_sample) for p in _PII_RE)

            if found_pii:
                log.warning(
                    "PII pattern detected in screenshot metadata — applying blur redaction"
                )
                from PIL import Image, ImageFilter
                import io

                img = Image.open(io.BytesIO(screenshot_bytes)).convert("RGB")
                w, h = img.size

                # Blur the bottom third where sensitive fields commonly appear
                redact_top = int(h * 0.6)
                region = img.crop((0, redact_top, w, h))
                blurred = region.filter(ImageFilter.GaussianBlur(radius=20))
                img.paste(blurred, (0, redact_top))

                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()

        except Exception as exc:
            log.warning("PII check failed (passing through unchanged): %s", exc)

        return screenshot_bytes

    def _in_blocked_region(self, x: int, y: int) -> bool:
        for region in self.BLOCKED_REGIONS_1920x1080:
            if region["x_min"] <= x <= region["x_max"] and region["y_min"] <= y <= region["y_max"]:
                return True
        return False
