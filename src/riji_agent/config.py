"""Configuration loading with a deliberately small and local-only surface."""

from __future__ import annotations

from pathlib import Path
from datetime import date
from typing import Annotated, FrozenSet, Optional
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from riji_agent.agent.registry import supported_agent_runtimes
from riji_agent.im.registry import supported_im_providers
from riji_agent.models.registry import supported_model_providers
from riji_agent.paths import default_data_dir


class ConfigurationError(RuntimeError):
    """A safe startup error which never serializes configuration values."""


def _default_data_dir() -> Path:
    return default_data_dir()


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
    deepseek_api_key: Optional[SecretStr] = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-reasoner", alias="DEEPSEEK_MODEL")
    # Generic OpenAI-compatible model credentials, used when RIJI_MODEL_PROVIDER
    # selects the non-default "openai" adapter. Unused by the DeepSeek default.
    model_api_key: Optional[SecretStr] = Field(default=None, alias="RIJI_MODEL_API_KEY")
    model_base_url: str = Field(default="https://api.openai.com/v1", alias="RIJI_MODEL_BASE_URL")
    model_name: str = Field(default="gpt-4o-mini", alias="RIJI_MODEL_NAME")
    runtime_trace_policy_path: Optional[Path] = Field(
        default=None, alias="RIJI_RUNTIME_TRACE_POLICY_PATH"
    )
    im_provider: str = Field(default="feishu", alias="RIJI_IM_PROVIDER")
    agent_runtime: str = Field(default="hermes", alias="RIJI_AGENT_RUNTIME")
    mentors_enabled: bool = Field(default=False, alias="RIJI_MENTORS_ENABLED")
    mentors_config_path: Optional[Path] = Field(default=None, alias="RIJI_MENTORS_CONFIG_PATH")
    model_provider: str = Field(default="deepseek", alias="RIJI_MODEL_PROVIDER")
    memory_model_provider: str = Field(default="deepseek", alias="RIJI_MEMORY_MODEL_PROVIDER")
    codex_bin: str = Field(default="codex", alias="RIJI_CODEX_BIN")
    codex_home: Optional[Path] = Field(default=None, alias="RIJI_CODEX_HOME")
    codex_proxy_url: Optional[SecretStr] = Field(default=None, alias="RIJI_CODEX_PROXY_URL")
    codex_model: str = Field(default="gpt-5.6-terra", alias="RIJI_CODEX_MODEL")
    memory_codex_model: str = Field(default="gpt-5.6-luna", alias="RIJI_MEMORY_CODEX_MODEL")
    codex_timeout_seconds: float = Field(
        default=90.0, alias="RIJI_CODEX_TIMEOUT_SECONDS", ge=5, le=600
    )
    memory_provider: str = Field(default="sqlite", alias="RIJI_MEMORY_PROVIDER")
    mem0_base_url: str = Field(
        default="http://127.0.0.1:38881", alias="RIJI_MEM0_BASE_URL"
    )
    mem0_dashboard_url: str = Field(
        default="http://127.0.0.1:38880", alias="RIJI_MEM0_DASHBOARD_URL"
    )
    mem0_api_key: Optional[SecretStr] = Field(default=None, alias="RIJI_MEM0_API_KEY")
    memory_auto_capture: bool = Field(default=True, alias="RIJI_MEMORY_AUTO_CAPTURE")
    journal_memory_enabled: bool = Field(default=False, alias="RIJI_JOURNAL_MEMORY_ENABLED")
    journal_memory_user_id: str = Field(default="", alias="RIJI_JOURNAL_MEMORY_USER_ID")
    journal_memory_sections: str = Field(default="🧠 Notes", alias="RIJI_JOURNAL_MEMORY_SECTIONS")
    journal_memory_date_from: Optional[str] = Field(default=None, alias="RIJI_JOURNAL_MEMORY_DATE_FROM")
    journal_memory_date_to: Optional[str] = Field(default=None, alias="RIJI_JOURNAL_MEMORY_DATE_TO")
    journal_memory_segment_chars: int = Field(default=900, alias="RIJI_JOURNAL_MEMORY_SEGMENT_CHARS", ge=100, le=2000)
    journal_memory_source_chars: int = Field(default=4000, alias="RIJI_JOURNAL_MEMORY_SOURCE_CHARS", ge=100)
    journal_memory_daily_chars: int = Field(default=100000, alias="RIJI_JOURNAL_MEMORY_DAILY_CHARS", ge=100)
    journal_memory_initialization_unlimited: bool = Field(
        default=False, alias="RIJI_JOURNAL_MEMORY_INITIALIZATION_UNLIMITED"
    )
    journal_memory_scan_seconds: int = Field(default=60, alias="RIJI_JOURNAL_MEMORY_SCAN_SECONDS", ge=5)
    memory_context_max_chars: int = Field(
        default=2000, alias="RIJI_MEMORY_CONTEXT_MAX_CHARS", ge=200, le=12000
    )
    memory_review_enabled: bool = Field(
        default=False, alias="RIJI_MEMORY_REVIEW_ENABLED"
    )
    memory_review_token: Optional[SecretStr] = Field(
        default=None, alias="RIJI_MEMORY_REVIEW_TOKEN"
    )
    memory_snapshot_enabled: bool = Field(
        default=True, alias="RIJI_MEMORY_SNAPSHOT_ENABLED"
    )
    memory_snapshot_path: Optional[Path] = Field(
        default=None, alias="RIJI_MEMORY_SNAPSHOT_PATH"
    )
    semantic_search_enabled: bool = Field(default=False, alias="RIJI_SEMANTIC_SEARCH")
    index_schedule_enabled: bool = Field(default=True, alias="RIJI_INDEX_SCHEDULE_ENABLED")
    index_interval_seconds: int = Field(default=600, alias="RIJI_INDEX_INTERVAL_SECONDS", ge=1)
    index_startup_timeout_seconds: float = Field(
        default=10.0, alias="RIJI_INDEX_STARTUP_TIMEOUT_SECONDS", ge=0
    )
    index_file_timeout_seconds: float = Field(
        default=5.0, alias="RIJI_INDEX_FILE_TIMEOUT_SECONDS", ge=0
    )
    feishu_voice_reply_mode: str = Field(default="off", alias="RIJI_FEISHU_VOICE_REPLY_MODE")
    calendar_provider: str = Field(default="off", alias="RIJI_CALENDAR_PROVIDER")
    feishu_app_id: Optional[str] = Field(default=None, alias="FEISHU_APP_ID")
    feishu_app_secret: Optional[SecretStr] = Field(default=None, alias="FEISHU_APP_SECRET")
    feishu_calendar_id: str = Field(default="primary", alias="FEISHU_CALENDAR_ID")
    feishu_open_base_url: str = Field(
        default="https://open.feishu.cn", alias="FEISHU_OPEN_BASE_URL"
    )
    tts_provider: str = Field(default="macos_say", alias="RIJI_TTS_PROVIDER")
    tts_voice: Optional[str] = Field(default=None, alias="RIJI_TTS_VOICE")
    tts_max_chars: int = Field(default=1200, alias="RIJI_TTS_MAX_CHARS", ge=1)
    tts_output_dir: Optional[Path] = Field(default=None, alias="RIJI_TTS_OUTPUT_DIR")
    tts_language: str = Field(default="ZH", alias="RIJI_TTS_LANGUAGE")
    tts_device: str = Field(default="auto", alias="RIJI_TTS_DEVICE")
    tts_speed: float = Field(default=1.0, alias="RIJI_TTS_SPEED", ge=0.1)
    tts_model: str = Field(default="openbmb/VoxCPM2", alias="RIJI_TTS_MODEL")
    tts_cfg_value: float = Field(default=2.0, alias="RIJI_TTS_CFG_VALUE", ge=0.1)
    tts_inference_timesteps: int = Field(
        default=10, alias="RIJI_TTS_INFERENCE_TIMESTEPS", ge=1
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

    @field_validator("mem0_base_url", "mem0_dashboard_url")
    @classmethod
    def require_loopback_mem0_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("must be an HTTP(S) URL")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("must use a loopback host")
        return value.rstrip("/")

    @field_validator("model_provider")
    @classmethod
    def require_supported_model_provider(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in supported_model_providers():
            raise ValueError("unsupported model provider")
        return cleaned

    @field_validator("memory_model_provider")
    @classmethod
    def require_supported_memory_model_provider(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"deepseek", "codex"}:
            raise ValueError("unsupported memory model provider")
        return cleaned

    @field_validator("codex_bin", "codex_model", "memory_codex_model")
    @classmethod
    def require_codex_value(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(character in cleaned for character in ("\x00", "\n", "\r")):
            raise ValueError("Codex configuration must be nonempty and single line")
        return cleaned

    @field_validator("codex_proxy_url", mode="before")
    @classmethod
    def normalize_codex_proxy(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @field_validator("codex_proxy_url")
    @classmethod
    def require_local_codex_proxy(cls, value: Optional[SecretStr]) -> Optional[SecretStr]:
        if value is None:
            return None
        raw = value.get_secret_value()
        try:
            parsed = urlparse(raw)
            valid = (
                not any(character.isspace() or ord(character) < 32 for character in raw)
                and parsed.scheme in {"http", "https"}
                and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                and parsed.port is not None and 1 <= parsed.port <= 65535
                and parsed.username is None and parsed.password is None
                and parsed.path in {"", "/"} and not parsed.params
                and not parsed.query and not parsed.fragment
                and "?" not in raw and "#" not in raw and "\\" not in raw
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Codex proxy must be a loopback HTTP(S) URL with a port and no credentials or URL suffix")
        return value

    @field_validator("memory_provider")
    @classmethod
    def require_supported_memory_provider(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"sqlite", "mem0"}:
            raise ValueError("unsupported memory provider")
        return cleaned

    @field_validator("im_provider")
    @classmethod
    def require_supported_im_provider(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in supported_im_providers():
            raise ValueError("unsupported IM provider")
        return cleaned

    @field_validator("agent_runtime")
    @classmethod
    def require_supported_agent_runtime(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in supported_agent_runtimes():
            raise ValueError("unsupported agent runtime")
        return cleaned

    @field_validator("feishu_voice_reply_mode")
    @classmethod
    def require_supported_voice_mode(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"off", "text_and_voice"}:
            raise ValueError("unsupported Feishu voice reply mode")
        return cleaned

    @field_validator("calendar_provider")
    @classmethod
    def require_supported_calendar_provider(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"off", "feishu"}:
            raise ValueError("unsupported calendar provider")
        return cleaned

    @field_validator("feishu_open_base_url")
    @classmethod
    def require_https_feishu_open_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("must be an HTTPS URL")
        return value.rstrip("/")

    @field_validator("tts_provider")
    @classmethod
    def require_supported_tts_provider(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"macos_say", "melotts", "voxcpm"}:
            raise ValueError("unsupported TTS provider")
        return cleaned

    @model_validator(mode="after")
    def validate_selected_model_provider(self) -> "Settings":
        # Chat and memory select credentials independently. Codex reuses its own
        # ChatGPT login; an unused DeepSeek credential is not a startup dependency.
        deepseek_required = self.model_provider == "deepseek" or (
            self.memory_provider == "mem0" and self.memory_model_provider == "deepseek"
        )
        if deepseek_required and (
            self.deepseek_api_key is None or not self.deepseek_api_key.get_secret_value()
        ):
            raise ValueError("DeepSeek API key is required for the selected provider")
        if self.model_provider == "openai":
            if self.model_api_key is None or not self.model_api_key.get_secret_value():
                raise ValueError("model api key is required for the selected provider")
        if self.calendar_provider == "feishu":
            if not self.feishu_app_id:
                raise ValueError("Feishu app id is required for the selected calendar provider")
            if self.feishu_app_secret is None or not self.feishu_app_secret.get_secret_value():
                raise ValueError("Feishu app secret is required for the selected calendar provider")
        if self.memory_provider == "mem0":
            if self.mem0_api_key is None or not self.mem0_api_key.get_secret_value():
                raise ValueError("Mem0 API key is required for the selected memory provider")
        if self.memory_review_enabled:
            if self.memory_provider != "mem0":
                raise ValueError("memory review requires the Mem0 memory provider")
            token = self.memory_review_token
            if token is None or len(token.get_secret_value()) < 16:
                raise ValueError("memory review token must contain at least 16 characters")
        if self.journal_memory_enabled:
            if self.memory_provider != "mem0":
                raise ValueError("journal memory requires the Mem0 memory provider")
            if self.journal_memory_user_id not in self.allowed_feishu_user_ids:
                raise ValueError("journal memory requires one allowlisted owner")
            if not any(value.strip() for value in self.journal_memory_sections.split(",")):
                raise ValueError("journal memory requires explicit section scope")
        return self

    @model_validator(mode="after")
    def validate_journal_memory_dates(self) -> "Settings":
        for value in (self.journal_memory_date_from, self.journal_memory_date_to):
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError("journal memory dates must use YYYY-MM-DD")
        if self.journal_memory_date_from and self.journal_memory_date_to and self.journal_memory_date_from > self.journal_memory_date_to:
            raise ValueError("journal memory date range is reversed")
        return self

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
        codex_home = (self.codex_home or data_dir / "codex").expanduser()
        if not codex_home.is_absolute():
            raise ValueError("Codex home must be absolute")
        codex_home = codex_home.resolve()
        if codex_home == journal_root or journal_root in codex_home.parents:
            raise ValueError("Codex home must be outside the journal root")
        if codex_home.exists() and not codex_home.is_dir():
            raise ValueError("Codex home must be a directory")
        self.codex_home = codex_home
        snapshot_path = self.memory_snapshot_path or (data_dir / "memory" / "MEMORY.md")
        snapshot_path = snapshot_path.expanduser().resolve()
        if snapshot_path == journal_root or journal_root in snapshot_path.parents:
            raise ValueError("memory snapshot must be outside the journal root")
        self.memory_snapshot_path = snapshot_path
        if self.runtime_trace_policy_path is not None:
            policy_path = self.runtime_trace_policy_path.expanduser()
            if not policy_path.is_absolute():
                raise ValueError("runtime trace policy path must be absolute")
            self.runtime_trace_policy_path = policy_path
        if self.tts_output_dir is not None:
            self.tts_output_dir = self.tts_output_dir.expanduser().resolve()
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
