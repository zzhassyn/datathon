"""Объяснение правила и видимость. Это наблюдения, а не дополнительные вердикты."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from cashguard.contracts._base import Contract

EvidenceIndex = Annotated[int, Field(ge=0, strict=True)]
VideoTime = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]


class RuleCondition(Contract):
    id: str = Field(min_length=1)
    text_ru: str = Field(min_length=1)
    status: Literal["met", "not_met", "unknown"]
    """not_met — наблюдение опровергает условие. Отсутствие детекции означает unknown."""
    reason_ru: str = Field(min_length=1)
    evidence_indices: list[EvidenceIndex] = Field(default_factory=list[EvidenceIndex])
    """Индексы кадров в evidence текущего события; время берётся из самого кадра."""

    @model_validator(mode="after")
    def _evidence(self) -> Self:
        if len(self.evidence_indices) != len(set(self.evidence_indices)):
            raise ValueError("индексы evidence не должны повторяться")
        if self.status != "unknown" and not self.evidence_indices:
            raise ValueError("подтверждение или опровержение требует кадра-доказательства")
        return self


class RuleExplanation(Contract):
    rule_version: str = Field(min_length=1)
    conditions: list[RuleCondition] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len({item.id for item in self.conditions}) != len(self.conditions):
            raise ValueError("условия должны иметь уникальные id")
        return self

    def validate_evidence_count(self, count: int) -> None:
        if any(index >= count for item in self.conditions for index in item.evidence_indices):
            raise ValueError("условие ссылается на отсутствующий кадр события")


class VisibilityCheck(Contract):
    id: str = Field(min_length=1)
    target_ru: str = Field(min_length=1)
    status: Literal["visible", "limited", "not_visible", "unknown"]
    """Возможность наблюдения объекта/зоны в интервале, не выполнение кассового правила."""
    reason_ru: str = Field(min_length=1)
    start_sec: VideoTime | None = None
    end_sec: VideoTime | None = None

    @model_validator(mode="after")
    def _interval(self) -> Self:
        if (self.start_sec is None) != (self.end_sec is None):
            raise ValueError("интервал требует start_sec и end_sec вместе")
        if (
            self.start_sec is not None
            and self.end_sec is not None
            and self.start_sec > self.end_sec
        ):
            raise ValueError("начало интервала позже конца")
        if self.status != "unknown" and self.start_sec is None:
            raise ValueError("оценка видимости требует наблюдаемого интервала")
        return self


class VisibilityAssessment(Contract):
    method_version: str = Field(min_length=1)
    checks: list[VisibilityCheck] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len({item.id for item in self.checks}) != len(self.checks):
            raise ValueError("оценки видимости должны иметь уникальные id")
        return self

    def validate_duration(self, duration_sec: float) -> None:
        if not math.isfinite(duration_sec) or duration_sec < 0:
            raise ValueError("длительность должна быть конечной и неотрицательной")
        if any(item.end_sec is not None and item.end_sec > duration_sec for item in self.checks):
            raise ValueError("оценка видимости выходит за длительность исходного видео")
