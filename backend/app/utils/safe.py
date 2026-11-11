"""
Small input-safety helpers shared across the API.

* is_valid_email — a linear-time shape check for an email address. The regex
  used previously (`^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$`) backtracks quadratically
  on long crafted input, so one request could tie up a worker.
* log_safe — makes a value safe to put in a log line. Without it, a value
  containing line breaks could forge extra log entries.
"""
from __future__ import annotations

MAX_EMAIL_LENGTH = 254  # RFC 5321 path limit


def is_valid_email(value: object) -> bool:
    """True if `value` looks like local@domain.tld. No regex, no backtracking."""
    if not isinstance(value, str) or not value or len(value) > MAX_EMAIL_LENGTH:
        return False
    if any(ch.isspace() for ch in value):
        return False
    local, sep, domain = value.partition("@")
    if not sep or not local or "@" in domain:
        return False
    name, dot, tld = domain.rpartition(".")
    return bool(dot and name and tld)


def log_safe(value: object, max_length: int = 200) -> str:
    """Render a value for a log line: one line, bounded length."""
    text = str(value)
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text[:max_length]
