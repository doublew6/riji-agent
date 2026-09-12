"""Opt-in deployment mapping; private credentials are resolved only from env vars."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal, Mapping

from dotenv import dotenv_values
from pydantic import Field, SecretStr

from riji_agent.mentors.models import Account, MentorError, Record


class ApplicationConfig(Record):
    external_id: str
    platform: str = "feishu"
    tenant: str
    persona_id: str
    transport_token_env: str = ""
    secret_env: str = ""
    receiver: Literal["dedicated", "hermes"] = "dedicated"


class UserConfig(Record):
    account: Account
    legacy_owner_key: str
    review_token_env: str


class MentorConfig(Record):
    applications: tuple[ApplicationConfig, ...] = Field(min_length=1, max_length=10)
    users: tuple[UserConfig, ...] = Field(min_length=1, max_length=10)
    feishu_receiver_ownership: str = "unconfigured"
    credentials_env_file: Path | None = None

    @classmethod
    def load(cls, path: Path, journal_root: Path):
        path = path.expanduser()
        if path.is_symlink() or path.resolve().is_relative_to(journal_root.resolve()):
            raise MentorError("mentor_config_path_invalid")
        if path.stat().st_mode & 0o077 or path.stat().st_size > 64000:
            raise MentorError("mentor_config_permissions_invalid")
        try:
            config = cls.model_validate_json(path.read_text())
        except Exception:
            raise MentorError("mentor_config_invalid") from None
        if any(item.platform not in {"feishu", "local"} for item in config.applications):
            raise MentorError("mentor_channel_unsupported")
        if any(item.platform == "feishu" for item in config.applications) and config.feishu_receiver_ownership != "dedicated_apps":
            raise MentorError("feishu_receiver_ownership_required")
        actors = [(item.platform, item.tenant, item.persona_id) for item in config.applications]
        if len(actors) != len(set(actors)):
            raise MentorError("duplicate_persona_application")
        if len({(item.platform, item.external_id) for item in config.applications}) != len(config.applications):
            raise MentorError("duplicate_external_application")
        if any(item.receiver == "hermes" and (item.platform != "feishu" or item.persona_id != "host")
               for item in config.applications):
            raise MentorError("hermes_host_application_required")
        return config


def load_credentials(config: MentorConfig, journal_root: Path) -> dict[str, str]:
    """Read an explicitly configured private env file without interpolation."""
    path = config.credentials_env_file
    if path is None:
        return {}
    path = path.expanduser()
    try:
        if path.is_symlink() or path.resolve().is_relative_to(journal_root.resolve()):
            raise MentorError("mentor_credentials_path_invalid")
        if path.stat().st_mode & 0o077 or path.stat().st_size > 64000:
            raise MentorError("mentor_credentials_permissions_invalid")
        return {key: value for key, value in dotenv_values(path, interpolate=False).items()
                if value is not None}
    except OSError:
        raise MentorError("mentor_credentials_unavailable") from None


def credential(name: str, values: Mapping[str, str] | None = None) -> SecretStr:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", name):
        raise MentorError("credential_reference_invalid")
    value = os.environ.get(name, (values or {}).get(name, ""))
    if len(value) < 32 or not value.isascii() or any(character.isspace() for character in value):
        raise MentorError("mentor_credential_required")
    return SecretStr(value)
