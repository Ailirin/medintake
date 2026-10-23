"""Настройки сервиса. Переменные окружения с префиксом MEDINTAKE_."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent


def _default_schema() -> Path:
    return PACKAGE_DIR / "schemas" / "medical_document.xsd"


def _default_icd10() -> Path:
    return PACKAGE_DIR / "data" / "icd10.csv"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEDINTAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    schema_path: Path = Field(default_factory=_default_schema)
    icd10_path: Path = Field(default_factory=_default_icd10)
    icd10_mode: Literal["off", "warn", "error"] = "warn"
    max_files: int = Field(default=10_000, ge=1, le=10_000)
    max_file_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    max_document_age_days: int = Field(default=366, ge=1, le=3650)
    timezone: str = "Europe/Moscow"
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / "var")
    inbox_root: Path | None = None
    keep_input: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_token: str | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value

    @field_validator("icd10_mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower()
        return value

    @field_validator("api_token", mode="before")
    @classmethod
    def _empty_token_is_disabled(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Неизвестный часовой пояс: {value}") from exc
        return value

    def today(self) -> date:
        """Дата приёма в часовом поясе медорганизации, а не сервера (в контейнере UTC)."""
        return datetime.now(ZoneInfo(self.timezone)).date()

    @property
    def inbox(self) -> Path:
        if self.inbox_root is not None:
            return self.inbox_root
        return self.data_dir / "inbox"

    @property
    def batches_dir(self) -> Path:
        return self.data_dir / "batches"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.batches_dir.mkdir(parents=True, exist_ok=True)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
