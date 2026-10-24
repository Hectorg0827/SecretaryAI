"""
Resolves the QuickBooks Desktop adapter for the connector.

The adapter is `backend/app/connectors/qb_desktop.py` and it is deliberately
NOT duplicated here: the connector and the cloud must talk to Conductor in
exactly the same way, and two copies drift apart silently.

That module imports `app.connectors.base` internally, so the package root it
needs on `sys.path` is `backend/`, not the repository root. The connector used
to do `from backend.app.connectors.qb_desktop import QBDesktopAdapter`, which
raises `ModuleNotFoundError: No module named 'app'` as soon as Python reaches
that internal import — so every fetch_* task failed with a TaskError even when
Conductor credentials were correct. Only `ping` ever worked.

Two environments have to work:

* **Source checkout** — `backend/` is found relative to this file and put on
  `sys.path`.
* **PyInstaller bundle** — `app.connectors.qb_desktop` is frozen into the
  executable (see `connector.spec`, which adds `backend/` to `pathex` and names
  the module in `hiddenimports`). The import then resolves normally and the
  path lookup below is skipped. The frozen subtree is small and dependency-free
  beyond httpx: `app/__init__.py` holds only `__version__`, and
  `app/connectors/base.py` is stdlib dataclasses.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _ensure_importable() -> None:
    """Put the backend package root on sys.path when running from source."""
    if getattr(sys, "frozen", False):
        return  # PyInstaller: the module is already inside the bundle
    backend = Path(__file__).resolve().parent.parent / "backend"
    if backend.is_dir():
        p = str(backend)
        if p not in sys.path:
            sys.path.insert(0, p)


def load_qb_desktop_adapter():
    """
    Return the QBDesktopAdapter class.

    Raises ImportError with an actionable message rather than letting a bare
    ModuleNotFoundError surface as "fetch_customers failed: No module named
    'app'", which says nothing useful to whoever is reading connector logs.
    """
    _ensure_importable()
    try:
        from app.connectors.qb_desktop import QBDesktopAdapter
    except ImportError as exc:
        raise ImportError(
            "Could not load the QuickBooks Desktop adapter "
            f"(app.connectors.qb_desktop): {exc}. "
            "Running from a source checkout requires the sibling backend/ "
            "directory and httpx installed (pip install -r "
            "connector/requirements.txt). A packaged connector should never "
            "reach this path — if it does, the PyInstaller build dropped the "
            "hidden import."
        ) from exc
    return QBDesktopAdapter
