"""Advisory insight endpoints, one submodule per capability.

Every public submodule of this package must define ``router`` (an APIRouter)
and ``PREFIX`` (for example ``"/churn"``); it is mounted under
``/api/insights`` automatically, so a capability is added without editing
shared files. Endpoints here are analysis only: they read the tenant's own
data, compute recommendations and never write to providers, send messages or
execute actions. Shared conventions live in ``_common``.
"""
from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter

router = APIRouter()
MOUNTED: list[str] = []

for _info in sorted(pkgutil.iter_modules(__path__), key=lambda info: info.name):
    if _info.name.startswith("_"):
        continue
    _module = importlib.import_module(f"{__name__}.{_info.name}")
    _sub = getattr(_module, "router", None)
    _prefix = getattr(_module, "PREFIX", None)
    if not isinstance(_sub, APIRouter) or not isinstance(_prefix, str) or not _prefix.startswith("/"):
        raise RuntimeError(f"insights.{_info.name} must define router and a PREFIX starting with '/'")
    router.include_router(_sub, prefix=_prefix, tags=[getattr(_module, "TAG", _info.name)])
    MOUNTED.append(_info.name)
