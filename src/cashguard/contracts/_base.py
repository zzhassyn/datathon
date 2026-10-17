"""Базовый класс всех контрактов: неизменяемые модели без лишних ключей."""

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1
"""Версия формата сохраняемых артефактов (trace, analysis). Повышать при несовместимой правке."""


class Contract(BaseModel):
    """Неизменяемая модель: лишние ключи запрещены, значения проверяются при создании."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)
