"""IM provider registry: the names accepted by ``RIJI_IM_PROVIDER``.

Feishu is the only adapter today, but selection goes through this registry so a
future IM adapter is added by registering its platform name here, keeping the
config seam identical to the model and agent-runtime layers.
"""

from __future__ import annotations

from typing import FrozenSet

from riji_agent.im.feishu import FEISHU_PLATFORM

_SUPPORTED = {FEISHU_PLATFORM}


def supported_im_providers() -> FrozenSet[str]:
    return frozenset(_SUPPORTED)
