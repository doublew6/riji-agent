"""Registry for built-in journal capability packs."""

from __future__ import annotations

from importlib import resources
from typing import FrozenSet

import yaml

from riji_agent.packs.models import PackManifest

_BUILTIN_PACKS = frozenset({"personal-growth"})


class PackNotFoundError(ValueError):
    """Raised when a requested pack is not registered."""


def list_packs() -> tuple[str, ...]:
    return tuple(sorted(_BUILTIN_PACKS))


def get_pack(pack_id: str) -> PackManifest:
    normalized = pack_id.strip().lower()
    if normalized not in _BUILTIN_PACKS:
        raise PackNotFoundError(f"unknown pack: {pack_id}")
    return _load_builtin_pack(normalized)


def supported_packs() -> FrozenSet[str]:
    return _BUILTIN_PACKS


def _load_builtin_pack(pack_id: str) -> PackManifest:
    package = f"riji_agent.packs.{pack_id.replace('-', '_')}"
    manifest = resources.files(package).joinpath("pack.yaml")
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    return PackManifest.from_mapping(data)
