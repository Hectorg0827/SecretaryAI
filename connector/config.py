"""
Connector configuration — read from environment variables or a .env file.

Required on first run:
  SECRETARY_CLOUD_URL       e.g. https://your-backend-host
  SECRETARY_INSTALL_SECRET  one-time secret from Settings > Connectors
  SECRETARY_COMPANY_ID      company UUID from the web app

Optional:
  SECRETARY_CONNECTOR_ID    stable machine identifier (defaults to hostname)
  SECRETARY_STATE_DIR       override where state is persisted
  CONDUCTOR_API_KEY         Conductor/QB Desktop API key
  CONDUCTOR_END_USER_ID     Conductor end-user ID

State (the connector token issued at registration) is persisted to
`connector_state.json` in a per-user application data directory — NOT the
working directory. A packaged connector is launched by the desktop app, by a
shortcut, or by a scheduled task, each with a different and unpredictable
working directory; writing state to the CWD meant the connector re-registered
every time it was started from somewhere new, and could land a file holding a
bearer token in whatever folder the user happened to be in.
"""
import json
import logging
import os
import socket
import sys
from pathlib import Path

from connector import __version__

log = logging.getLogger(__name__)

APP_DIR_NAME = "SecretaryAI"
STATE_FILENAME = "connector_state.json"


def default_state_dir() -> Path:
    """Per-user application data directory, following each platform's convention."""
    override = os.environ.get("SECRETARY_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        return Path(base or Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_DIR_NAME


class ConfigError(RuntimeError):
    """Raised when required configuration is missing, with an actionable message."""


class ConnectorConfig:
    def __init__(self):
        # Load .env if present (best-effort)
        _load_dotenv()

        self.cloud_url: str = _require("SECRETARY_CLOUD_URL").rstrip("/")
        self.company_id: str = _require("SECRETARY_COMPANY_ID")
        self.install_secret: str = os.environ.get("SECRETARY_INSTALL_SECRET", "")
        self.connector_id: str = os.environ.get(
            "SECRETARY_CONNECTOR_ID", socket.gethostname()
        )
        self.connector_type: str = "qb_desktop"
        # Single source of truth: connector/__init__.py, which scripts/check_versions.sh
        # pins to /VERSION so a shipped connector cannot report a stale version.
        self.version: str = __version__
        self.conductor_api_key: str = os.environ.get("CONDUCTOR_API_KEY", "")
        self.conductor_end_user_id: str = os.environ.get("CONDUCTOR_END_USER_ID", "")

        self.state_dir: Path = default_state_dir()
        self.state_file: Path = self.state_dir / STATE_FILENAME

        state = self._load_state()
        self.connector_token: str = state.get("connector_token", "")
        self.registration_id: str = state.get("registration_id", "")

    def save_token(self, token: str, registration_id: str) -> None:
        state = self._load_state()
        state["connector_token"] = token
        state["registration_id"] = registration_id

        self.state_dir.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file in the same directory and replace atomically,
        # so an interrupted write cannot leave a truncated state file that forces
        # a re-registration. Created 0600 because the file holds a bearer token.
        tmp = self.state_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(state, fh, indent=2)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self.state_file)
        try:
            os.chmod(self.state_file, 0o600)
        except OSError:
            pass  # best-effort; Windows ACLs do not map onto POSIX modes

        self.connector_token = token
        self.registration_id = registration_id
        log.info("Connector token saved to %s", self.state_file)

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except (OSError, ValueError) as exc:
                log.warning(
                    "Ignoring unreadable state file %s (%s); will re-register.",
                    self.state_file, exc,
                )
        return {}

    @property
    def is_registered(self) -> bool:
        return bool(self.connector_token)


def _require(name: str) -> str:
    """Read a required variable, failing with a message a human can act on."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. The connector needs SECRETARY_CLOUD_URL (the "
            "address of your SecretaryAI backend) and SECRETARY_COMPANY_ID "
            "(shown in the web app under Settings > Connectors). Set them in "
            "the environment or in a .env file next to the connector."
        )
    return value


def _load_dotenv() -> None:
    """Load .env from the CWD if present (no dependency on python-dotenv)."""
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
