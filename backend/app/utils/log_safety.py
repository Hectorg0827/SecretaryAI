"""Conservative log levels for libraries that include credential-bearing URLs."""

import logging


def configure_safe_transport_logging() -> None:
    """Keep HTTP transport failures without logging full success request URLs.

    Supabase PostgREST filter URLs can contain encrypted OAuth refresh tokens;
    encryption does not make credential ciphertext safe to retain in logs.
    """
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
