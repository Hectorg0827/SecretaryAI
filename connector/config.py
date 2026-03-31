"""
Connector configuration — read from environment variables or .env file.

Required on first run:
  SECRETARY_CLOUD_URL      e.g. https://api.secretaryai.com
  SECRETARY_INSTALL_SECRET  one-time secret from Settings > Connectors
  SECRETARY_COMPANY_ID     company UUID from the web app

Optional:
  SECRETARY_CONNECTOR_ID   stable machine identifier (defaults to hostname)
  SECRETARY_CONNECTOR_TOKEN persisted connector JWT (written after first register)
  CONDUCTOR_API_KEY         Conductor/QB Desktop API key
  CONDUCTOR_END_USER_ID     Conductor end-user ID

State is persisted to connector_state.json in the working directory.
"""
import os
import json
import socket
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_STATE_FILE = Path("connector_state.json")


class ConnectorConfig:
    def __init__(self):
        # Load .env if present (best-effort)
        _load_dotenv()

        self.cloud_url: str = os.environ["SECRETARY_CLOUD_URL"].rstrip("/")
        self.install_secret: str = os.environ.get("SECRETARY_INSTALL_SECRET", "")
        self.company_id: str = os.environ["SECRETARY_COMPANY_ID"]
        self.connector_id: str = os.environ.get(
            "SECRETARY_CONNECTOR_ID", socket.gethostname()
        )
        self.connector_type: str = "qb_desktop"
        self.version: str = "1.0.0"
        self.conductor_api_key: str = os.environ.get("CONDUCTOR_API_KEY", "")
        self.conductor_end_user_id: str = os.environ.get("CONDUCTOR_END_USER_ID", "")

        # Persisted connector token (written after successful registration)
        self.connector_token: str = self._load_state().get("connector_token", "")
        self.registration_id: str = self._load_state().get("registration_id", "")

    def save_token(self, token: str, registration_id: str) -> None:
        state = self._load_state()
        state["connector_token"] = token
        state["registration_id"] = registration_id
        _STATE_FILE.write_text(json.dumps(state, indent=2))
        self.connector_token = token
        self.registration_id = registration_id
        log.info("Connector token saved to %s", _STATE_FILE)

    def _load_state(self) -> dict:
        if _STATE_FILE.exists():
            try:
                return json.loads(_STATE_FILE.read_text())
            except Exception:
                return {}
        return {}

    @property
    def is_registered(self) -> bool:
        return bool(self.connector_token)


def _load_dotenv() -> None:
    """Load .env file from CWD if it exists (no dependency on python-dotenv)."""
    env_file = Path(".env")
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
