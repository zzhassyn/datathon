"""Application settings. Dependencies and public contracts remain owned by S0."""

import os
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cashguard.core.paths import RuntimePaths


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CG_", extra="ignore", populate_by_name=True)

    data_dir: Path = Field(
        default=Path("var"),
        validation_alias=AliasChoices("CG_DATA_DIR", "CG_VAR_DIR", "var_dir"),
    )
    models_dir: Path = Path("models")
    web_dist: Path = Path("web/dist")
    api_keys: SecretStr = SecretStr("")
    ui_access_code: SecretStr = SecretStr("")
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    pipeline_mode: Literal["stub", "replay", "full"] = "stub"
    vision_backend: str = "stub"
    vlm_backend: str = "null"
    vlm_enabled: bool = False
    vlm_quant: str = "auto"
    max_upload_mb: float = Field(default=100, gt=0)
    min_free_disk_gb: float = Field(default=2, ge=0)
    min_free_mem_gb: float = Field(default=3, ge=0)
    retention_hours: float = Field(default=24, ge=0)
    docs_enabled: bool = False
    ui_rate_uploads: int = Field(default=5, ge=1)
    ui_rate_window_sec: int = Field(default=600, ge=1)
    ui_rate_active: int = Field(default=2, ge=1)
    ui_rate_queue: int = Field(default=10, ge=1)
    pipeline_version: str = "stub-v1"
    config_hash: str = "default"
    upload_timeout_sec: float = Field(default=120, gt=0, le=235)
    ui_rate_profile: Literal["normal", "jury"] = "normal"
    stub_delay_sec: float = Field(default=0.1, ge=0)
    worker_poll_sec: float = Field(default=0.25, gt=0)
    heartbeat_interval_sec: float = Field(default=2, gt=0, lt=20)
    max_worker_jobs: int = Field(default=200, ge=1)

    @model_validator(mode="after")
    def apply_ui_profile(self) -> "Settings":
        if self.ui_rate_profile == "jury":
            for field, value in {
                "ui_rate_uploads": 30,
                "ui_rate_active": 5,
                "ui_rate_queue": 20,
            }.items():
                if field not in self.model_fields_set:
                    object.__setattr__(self, field, value)
        return self

    @property
    def var_dir(self) -> Path:
        return self.data_dir.expanduser().resolve()

    @property
    def paths(self) -> RuntimePaths:
        return RuntimePaths(self.var_dir)

    @property
    def db_path(self) -> Path:
        return self.paths.db_path

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1_000_000)

    def parsed_api_keys(self) -> dict[str, str]:
        keys: dict[str, str] = {}
        for item in self.api_keys.get_secret_value().split(","):
            if not item.strip():
                continue
            name, separator, token = item.strip().partition(":")
            if not separator or not name or not token or name in keys:
                raise ValueError("CG_API_KEYS must contain unique name:token entries")
            keys[name] = token
        return keys


def get_settings() -> Settings:
    env_file = Path(os.environ.get("CG_ENV_FILE", "~/.config/cashguard/.env")).expanduser()
    # pydantic-settings принимает _env_file при создании; в сигнатуре его нет.
    return Settings(_env_file=env_file if env_file.is_file() else None)  # pyright: ignore[reportCallIssue]
