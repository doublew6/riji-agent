"""Configuration loading with a deliberately small and local-only surface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, FrozenSet, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """A safe startup error which never serializes configuration values."""


def _default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "riji-agent"


class Settings(BaseSettings):
    """Runtime configuration supplied only through local environment settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    journal_root: Path = Field(alias="RIJI_JOURNAL_ROOT")
    data_dir: Path = Field(default_factory=_default_data_dir, alias="RIJI_DATA_DIR")
    database_path: Optional[Path] = Field(default=None, alias="RIJI_DATABASE_PATH")
    deepseek_api_key: SecretStr = Field(alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-reasoner", alias="DEEPSEEK_MODEL")
    im_provider: str = Field(default="feishu", alias="RIJI_IM_PROVIDER")
    agent_runtime: str = Field(default="hermes", alias="RIJI_AGENT_RUNTIME")
    model_provider: str = Field(default="deepseek", alias="RIJI_MODEL_PROVIDER")
    semantic_search_enabled: bool = Field(default=False, alias="RIJI_SEMANTIC_SEARCH")
    index_schedule_enabled: bool = Field(default=True, alias="RIJI_INDEX_SCHEDULE_ENABLED")
    index_interval_seconds: int = Field(default=600, alias="RIJI_INDEX_INTERVAL_SECONDS", ge=1)
    index_startup_timeout_seconds: float = Field(
        default=10.0, alias="RIJI_INDEX_STARTUP_TIMEOUT_SECONDS", ge=0
    )
    index_file_timeout_seconds: float = Field(
        default=5.0, alias="RIJI_INDEX_FILE_TIMEOUT_SECONDS", ge=0
    )
    allowed_feishu_user_ids: Annotated[FrozenSet[str], NoDecode] = Field(
        alias="RIJI_ALLOWED_FEISHU_USER_IDS"
    )
    hermes_shared_secret: SecretStr = Field(alias="HERMES_SHARED_SECRET")
    hermes_base_url: str = Field(default="http://127.0.0.1:3000", alias="HERMES_BASE_URL")
    port: int = Field(default=8765, alias="RIJI_PORT", ge=1, le=65535)

    @field_validator("allowed_feishu_user_ids", mode="before")
    @classmethod
    def parse_allowed_users(cls, value: object) -> FrozenSet[str]:
        if isinstance(value, str):
            users = frozenset(item.strip() for item in value.split(",") if item.strip())
        elif isinstance(value, (list, tuple, set, frozenset)):
            users = frozenset(str(item).strip() for item in value if str(item).strip())
        else:
            raise ValueError("must be a comma-separated list")
        if not users:
            raise ValueError("must contain at least one user")
        return users

    @field_validator("deepseek_base_url")
    @classmethod
    def require_https_url(cls, value: str) -> str:
        # DeepSeek is a cloud endpoint; the API key must never travel over cleartext http.
        if not value.startswith("https://"):
            raise ValueError("must be an HTTPS URL")
        return value.rstrip("/")

    @field_validator("hermes_base_url")
    @classmethod
    def require_http_url(cls, value: str) -> str:
        # Hermes runs on local loopback, so plain http is acceptable here.
        if not value.startswith(("http://", "https://")):
            raise ValueError("must be an HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("model_provider")
    @classmethod
    def require_supported_model_provider(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned != "deepseek":
            raise ValueError("unsupported model provider")
        return cleaned

    @field_validator("im_provider")
    @classmethod
    def require_supported_im_provider(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned != "feishu":
            raise ValueError("unsupported IM provider")
        return cleaned

    @field_validator("agent_runtime")
    @classmethod
    def require_supported_agent_runtime(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned != "hermes":
            raise ValueError("unsupported agent runtime")
        return cleaned

    @model_validator(mode="after")
    def validate_local_paths(self) -> "Settings":
        journal_root = self.journal_root.expanduser().resolve()
        if not journal_root.is_dir():
            raise ValueError("journal root must be an existing directory")

        data_dir = self.data_dir.expanduser().resolve()
        if data_dir == journal_root or journal_root in data_dir.parents:
            raise ValueError("data directory must be outside the journal root")
        if data_dir.exists() and not data_dir.is_dir():
            raise ValueError("data directory must be a directory")

        self.journal_root = journal_root
        self.data_dir = data_dir
        if self.database_path is not None:
            database_path = self.database_path.expanduser().resolve()
            if database_path.parent != data_dir:
                raise ValueError("database path must be inside the data directory")
            self.database_path = database_path
        return self

    @property
    def resolved_database_path(self) -> Path:
        return self.database_path or self.data_dir / "riji-agent.sqlite3"

    def ensure_data_directory(self) -> None:
        """Create only the configured local runtime directory, never journal folders."""
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Load settings without ever returning validation details to an API caller."""
    try:
        settings = Settings()
        settings.ensure_data_directory()
        return settings
    except Exception as exc:
        raise ConfigurationError(
            "Configuration is invalid. Check required local environment variables and paths."
        ) from exc
