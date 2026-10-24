# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the SecretaryAI QuickBooks Desktop connector.

Build:
    pyinstaller --clean --noconfirm connector/connector.spec

Produces a single self-contained executable in dist/. QuickBooks Desktop is
Windows-only, so the artifact that ships is the Windows build (see
.github/workflows/release-connector.yml); the Linux/macOS builds exist only so
CI can prove the spec still works without a Windows runner.

Why `hiddenimports` and the extra `pathex` entry:
    The Conductor adapter is `backend/app/connectors/qb_desktop.py` and is NOT
    duplicated into connector/ — one copy means the connector and the cloud
    cannot drift in how they talk to QuickBooks. That module imports
    `app.connectors.base` internally, so the package root PyInstaller must
    search is `backend/`, and the module is reached dynamically (inside
    connector/qb_adapter.py) rather than by a static top-level import, so the
    analyser cannot find it on its own. Without both of these the executable
    builds happily and then fails every QuickBooks task at runtime with
    "No module named 'app'" — exactly the bug this packaging work fixes.

    The frozen subtree is deliberately tiny: app/__init__.py is a version
    string and app/connectors/base.py is stdlib dataclasses, so nothing of the
    FastAPI backend is pulled onto a customer machine.
"""
import os

# SPECPATH is the directory containing this spec file (connector/).
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
BACKEND = os.path.join(REPO_ROOT, "backend")

a = Analysis(
    [os.path.join(SPECPATH, "main.py")],
    pathex=[REPO_ROOT, BACKEND],
    binaries=[],
    datas=[],
    hiddenimports=[
        "app",
        "app.connectors",
        "app.connectors.base",
        "app.connectors.qb_desktop",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing from the web backend belongs in a desktop agent. Excluding these
    # keeps the executable small and makes an accidental dependency on backend
    # code a build failure rather than a 90 MB download.
    excludes=[
        "fastapi",
        "uvicorn",
        "celery",
        "supabase",
        "pydantic_settings",
        "stripe",
        "sendgrid",
        "playwright",
        "tkinter",
        "test",
        "unittest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SecretaryAIConnector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed binaries trip Windows Defender heuristics
    # A console app: the connector is a long-running agent and its output is
    # how a customer or support diagnoses a failing sync. It also writes a
    # rotating log to per-user appdata, so a hidden launch still leaves a
    # trail. Keep this True — in a windowed build sys.stdout is None.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
