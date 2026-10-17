"""Геометрия кадра: координаты нормированы в 0..1 после поворота видео."""

from typing import Self

from pydantic import Field, model_validator

from cashguard.contracts._base import Contract

Point = tuple[float, float]


class BBox(Contract):
    """Прямоугольник в нормированных координатах (0..1), x1 <= x2, y1 <= y2."""

    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.x1 > self.x2 or self.y1 > self.y2:
            msg = "ожидается x1 <= x2 и y1 <= y2"
            raise ValueError(msg)
        return self
