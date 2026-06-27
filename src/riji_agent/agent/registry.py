"""Agent runtime registry: the names accepted by ``RIJI_AGENT_RUNTIME``.

Hermes is the only runtime today; selection goes through this registry so the
config seam matches the model and IM layers and a future runtime is added by
registering its name here.
"""

from __future__ import annotations

from typing import FrozenSet

HERMES_RUNTIME = "hermes"

_SUPPORTED = {HERMES_RUNTIME}


def supported_agent_runtimes() -> FrozenSet[str]:
    return frozenset(_SUPPORTED)
