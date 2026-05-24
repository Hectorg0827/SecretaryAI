"""
SecretaryAI · Feature Factory
=============================
A safe, self-extending feature subsystem: users describe a feature in plain
English; the system generates, reviews (human-approved), previews, and runs it
under eight layers of defense. See README.md for the full design and wiring.

Public surface:
"""
from .service import FeatureFactoryService, PolicyError  # noqa: F401
from .router import router  # noqa: F401

__all__ = ["FeatureFactoryService", "PolicyError", "router"]
