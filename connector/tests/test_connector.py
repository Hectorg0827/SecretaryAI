"""
Unit tests for the QuickBooks Desktop connector.

The connector had no tests, and the defect that mattered most was invisible
without one: `connector/executor.py` imported the Conductor adapter by the
wrong path, so every QuickBooks task failed at runtime with
"No module named 'app'" while the module itself imported fine from the
backend. Nothing exercised the connector, so nothing caught it.

These tests are offline — none of them touch Conductor or the cloud.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from connector import __version__                                  # noqa: E402
from connector.config import ConfigError, ConnectorConfig, default_state_dir  # noqa: E402
from connector.executor import TaskExecutor, TaskError             # noqa: E402
from connector.qb_adapter import load_qb_desktop_adapter           # noqa: E402


# ── The regression that shipped ───────────────────────────────────────────────

def test_qb_desktop_adapter_is_importable():
    """
    Regression test for the connector's central defect.

    `from backend.app.connectors.qb_desktop import QBDesktopAdapter` raises
    ModuleNotFoundError('app'), because that module does
    `from app.connectors.base import ...` internally and therefore needs
    backend/ — not the repository root — as its package root.
    """
    adapter = load_qb_desktop_adapter()
    assert adapter.__name__ == "QBDesktopAdapter"
    # Exact, not startswith: "https://api.conductor.is" is also a prefix of
    # https://api.conductor.is.example.net, so a prefix test would accept an
    # adapter pointed somewhere else entirely.
    assert adapter.BASE_URL == "https://api.conductor.is/v1"


def test_the_old_broken_import_path_is_still_broken():
    """
    Pins *why* the indirection in qb_adapter.py exists, so nobody 'simplifies'
    it back to `from backend.app.connectors.qb_desktop import ...`.

    Runs in a subprocess on purpose: importing the adapter the correct way puts
    backend/ on sys.path, which then makes the broken path accidentally work.
    Asserting this in-process would pass or fail depending on test order.
    """
    import subprocess
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "try:\n"
        "    from backend.app.connectors.qb_desktop import QBDesktopAdapter\n"
        "    print('IMPORTED')\n"
        "except ModuleNotFoundError as e:\n"
        "    print('MODULENOTFOUND', e.name)\n"
    ) % str(REPO_ROOT)
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT)
    ).stdout.strip()
    assert out == "MODULENOTFOUND app", (
        f"expected the legacy import path to fail on 'app', got {out!r}"
    )


# ── Executor behaviour ────────────────────────────────────────────────────────

def test_ping_needs_no_quickbooks():
    import asyncio
    ex = TaskExecutor(conductor_api_key="", conductor_end_user_id="")
    assert asyncio.run(ex.execute("ping", {})) == {"status": "pong", "task_type": "ping"}


def test_unsupported_task_type_is_unrecoverable():
    import asyncio
    ex = TaskExecutor(conductor_api_key="k", conductor_end_user_id="u")
    with pytest.raises(TaskError) as e:
        asyncio.run(ex.execute("drop_all_tables", {}))
    assert e.value.recoverable is False


@pytest.mark.parametrize("task_type", ["create_invoice", "create_po", "update_item"])
def test_write_task_types_are_refused_not_silently_accepted(task_type):
    """
    The backend can dispatch these write task types, but the connector
    implements none of them. Refusing them explicitly is the honest behaviour —
    the alternative is a task that is accepted and then never performed.
    (See defect #56 in docs/PRODUCTION_READINESS.md.)
    """
    import asyncio
    ex = TaskExecutor(conductor_api_key="k", conductor_end_user_id="u")
    with pytest.raises(TaskError) as e:
        asyncio.run(ex.execute(task_type, {}))
    assert e.value.recoverable is False
    assert "Unsupported task type" in str(e.value)


def test_missing_conductor_credentials_is_unrecoverable():
    """Without credentials, retrying forever is pointless — say so once."""
    import asyncio
    ex = TaskExecutor(conductor_api_key="", conductor_end_user_id="")
    with pytest.raises(TaskError) as e:
        asyncio.run(ex.execute("fetch_customers", {}))
    assert e.value.recoverable is False
    assert "CONDUCTOR_API_KEY" in str(e.value)


# ── Configuration and state ───────────────────────────────────────────────────

@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("SECRETARY_", "CONDUCTOR_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SECRETARY_STATE_DIR", str(tmp_path / "SecretaryAI"))
    monkeypatch.chdir(tmp_path)   # so a stray .env in the repo cannot leak in
    return tmp_path


def test_missing_required_config_raises_configerror_not_keyerror(clean_env):
    """A bare KeyError traceback tells a non-technical user nothing."""
    with pytest.raises(ConfigError) as e:
        ConnectorConfig()
    assert "SECRETARY_CLOUD_URL" in str(e.value)


def test_cloud_url_trailing_slash_is_stripped(clean_env, monkeypatch):
    monkeypatch.setenv("SECRETARY_CLOUD_URL", "https://api.example.test/")
    monkeypatch.setenv("SECRETARY_COMPANY_ID", "co-1")
    assert ConnectorConfig().cloud_url == "https://api.example.test"


def test_token_is_persisted_privately_and_atomically(clean_env, monkeypatch):
    monkeypatch.setenv("SECRETARY_CLOUD_URL", "https://api.example.test")
    monkeypatch.setenv("SECRETARY_COMPANY_ID", "co-1")
    cfg = ConnectorConfig()
    assert cfg.is_registered is False

    cfg.save_token("tok-abc", "reg-7")

    assert cfg.is_registered is True
    assert json.loads(cfg.state_file.read_text()) == {
        "connector_token": "tok-abc",
        "registration_id": "reg-7",
    }
    # The file holds a bearer token.
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(cfg.state_file).st_mode) == 0o600
    # No temp file left behind by the atomic replace.
    assert not list(cfg.state_file.parent.glob("*.tmp"))


def test_state_survives_a_new_process(clean_env, monkeypatch):
    """The old code wrote state to the CWD, so a connector launched from a
    different directory re-registered every time."""
    monkeypatch.setenv("SECRETARY_CLOUD_URL", "https://api.example.test")
    monkeypatch.setenv("SECRETARY_COMPANY_ID", "co-1")
    ConnectorConfig().save_token("tok-xyz", "reg-9")

    other_dir = clean_env / "somewhere-else"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    revived = ConnectorConfig()
    assert revived.is_registered is True
    assert revived.connector_token == "tok-xyz"


def test_corrupt_state_file_does_not_crash_the_connector(clean_env, monkeypatch):
    monkeypatch.setenv("SECRETARY_CLOUD_URL", "https://api.example.test")
    monkeypatch.setenv("SECRETARY_COMPANY_ID", "co-1")
    d = Path(os.environ["SECRETARY_STATE_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "connector_state.json").write_text("{ this is not json")

    cfg = ConnectorConfig()   # must fall back to "not registered", not raise
    assert cfg.is_registered is False


def test_state_dir_is_user_scoped_not_the_working_directory():
    d = default_state_dir()
    assert d.name == "SecretaryAI"
    assert d.is_absolute()


# ── Version ───────────────────────────────────────────────────────────────────

def test_connector_version_matches_the_repo_version():
    """The connector is a shipped artifact; it had drifted to 1.0.0 while the
    rest of the product moved to 1.0.6. scripts/check_versions.sh enforces this
    in CI too; this keeps the connector's own suite honest."""
    assert __version__ == (REPO_ROOT / "VERSION").read_text().strip()
