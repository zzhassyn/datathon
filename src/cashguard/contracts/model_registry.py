"""Реестр моделей ``configs/models.yaml``: версия, путь, sha256 и бэкенд для каждой роли.

Воркеры при старте сверяют sha256 файлов с реестром (офлайн, ТЗ §7). Выпуск версии — экспортёр S3.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from cashguard.contracts._base import Contract


class ModelRole(StrEnum):
    DETECTOR = "detector"
    POSE = "pose"
    CLS_SCREEN = "cls_screen"
    CLS_SAFE_LOCK = "cls_safe_lock"
    CLS_DOOR = "cls_door"
    VLM = "vlm"


class ModelArtifact(Contract):
    backend: str = Field(pattern=r"^(coreml|torch|mlx|transformers)$")
    path: str
    """Относительно ``CG_MODELS_DIR``."""
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    """None — модель ещё не выпущена (заготовка)."""
    imgsz: str | None = None
    """Вход артефакта, если отличается от записи (например, 480x864 для альбомного видео)."""
    repo: str | None = None
    """Репозиторий Hugging Face (VLM)."""
    revision: str | None = None
    """Закреплённая ревизия репозитория (VLM)."""
    quant: Literal["8bit", "6bit", "4bit"] | None = None
    """Квантование VLM; выбор по свободной памяти — 8 → 6 → 4 бит."""


class ModelEntry(Contract):
    role: ModelRole
    version: str
    imgsz: str | None = None
    artifacts: list[ModelArtifact]
    revision: str | None = None
    """Ревизия на Hugging Face (для VLM) или релиз ultralytics/assets."""
    note_ru: str | None = None


class ModelsRegistry(Contract):
    models: list[ModelEntry]
