"""Контракт jury API (ТЗ, раздел 10). ЗАМОРОЖЕН.

Оракул — рукописная схема ``schemas/jury_job.schema.json``. Модели ниже повторяют её и добавляют
инварианты, которые JSON Schema выразить не может (совпадение таймкода с первым кадром, порядок).
Менять только с одобрения человека (владелец S0).
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from cashguard.contracts._base import Contract

MAX_VIOLATIONS = 5
"""Не больше пяти нарушений в ответе жюри (docs/api.md)."""

JuryStatus = Literal["processing", "done", "failed"]
JuryVerdict = Literal["violation", "no_violation"]


class AnalyzeAccepted(Contract):
    """Ответ ``POST /api/v1/analyze`` и ``POST /api/ui/jobs``."""

    job_id: str = Field(min_length=1)


class JuryEvidenceFrame(Contract):
    """Кадр-доказательство: сырой JPEG в base64 без префикса ``data:``."""

    timestamp_sec: float = Field(ge=0)
    image_base64: str = Field(min_length=1, pattern=r"^/9j/")


class JuryViolation(Contract):
    """Одно нарушение в ответе жюри. ``type`` — ``CatalogEntry.title_ru``."""

    type: str = Field(min_length=1)
    timecode_sec: float = Field(ge=0)
    description: str = Field(min_length=1)
    evidence_frames: list[JuryEvidenceFrame] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _first_frame_at_timecode(self) -> Self:
        if round(self.evidence_frames[0].timestamp_sec, 2) != round(self.timecode_sec, 2):
            msg = "evidence_frames[0].timestamp_sec должен совпадать с timecode_sec"
            raise ValueError(msg)
        return self


class JuryJobResponse(Contract):
    """Ответ ``GET /api/v1/jobs/{job_id}``. Ровно четыре ключа верхнего уровня."""

    job_id: str = Field(min_length=1)
    status: JuryStatus
    verdict: JuryVerdict | None
    violations: list[JuryViolation]

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        if self.status == "done":
            if self.verdict is None:
                msg = "при status=done verdict не может быть null"
                raise ValueError(msg)
            if (self.verdict == "violation") != bool(self.violations):
                msg = "verdict=violation тогда и только тогда, когда нарушений >= 1"
                raise ValueError(msg)
        elif self.verdict is not None or self.violations:
            msg = "при processing/failed verdict=null и violations=[]"
            raise ValueError(msg)
        if len(self.violations) > MAX_VIOLATIONS:
            msg = f"не больше {MAX_VIOLATIONS} нарушений"
            raise ValueError(msg)
        times = [v.timecode_sec for v in self.violations]
        if times != sorted(times):
            msg = "нарушения должны быть отсортированы по timecode_sec"
            raise ValueError(msg)
        return self
