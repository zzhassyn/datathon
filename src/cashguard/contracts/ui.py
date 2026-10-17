"""Контракт веб-интерфейса (``/api/ui``). Через OpenAPI превращается в типы ``web/src/shared/api``.

В отличие от jury API кадры отдаются ссылками, а не base64, и есть сигналы.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from cashguard.contracts._base import Contract
from cashguard.contracts.catalog import (
    ActEntry,
    LegalStrength,
    SignalCode,
    ViolationCode,
    ViolationGroup,
)
from cashguard.contracts.findings import EvidenceKind, VlmStatus
from cashguard.contracts.jobs import JobStatus, PerceptionLevel, ResultLevel, StageState

PipelineModeName = Literal["stub", "replay", "full"]
"""``stub`` — демонстрационный режим без моделей: интерфейс обязан показать это явно."""


class UiVideo(Contract):
    url: str
    duration_sec: float = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class UiLegalRef(Contract):
    act_code: str
    act_title_ru: str
    clause: str | None = None
    quote_ru: str | None = None
    url: str
    strength: LegalStrength
    note_ru: str | None = None
    """Оговорка: условие применимости, ссылка на FAQ 31 и т. п."""


class UiEvidence(Contract):
    timestamp_sec: float = Field(ge=0)
    url: str
    kind: EvidenceKind
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class UiViolation(Contract):
    id: str
    code: str
    """Код из каталога (``ViolationCode``) или служебный (``STUB_EVENT``) — для UI непрозрачен."""
    title_ru: str
    timecode_sec: float = Field(ge=0)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    vlm_status: VlmStatus
    provisional: bool
    description_ru: str
    legal: list[UiLegalRef]
    legal_note_ru: str | None = None
    """Заполняется, если основание не установлено: сервер честно сообщает это текстом."""
    evidence: list[UiEvidence]


class UiSignal(Contract):
    id: str
    code: str
    title_ru: str
    t_sec: float = Field(ge=0)
    description_ru: str
    evidence: list[UiEvidence] = Field(default_factory=list[UiEvidence])


class UiError(Contract):
    code: str
    message_ru: str


class UiDegradation(Contract):
    stage: str
    reason_ru: str


class UiJobView(Contract):
    """``GET /api/ui/jobs/{job_id}``. Интерфейс опрашивает раз в секунду, пока задача не готова."""

    job_id: str
    status: JobStatus
    pipeline_mode: PipelineModeName
    created_at: str
    """Время приёма, ISO 8601 UTC, например ``2026-10-17T15:48:09.983783Z``."""
    elapsed_sec: float = Field(ge=0)
    queue_position: int | None = None
    eta_sec: float | None = None
    perception_level: PerceptionLevel | None = None
    result_level: ResultLevel | None = None
    stages: list[StageState]
    degradations: list[UiDegradation] = Field(default_factory=list[UiDegradation])
    provisional: bool = False
    video: UiVideo | None = None
    verdict: Literal["violation", "no_violation"] | None = None
    violations: list[UiViolation] = Field(default_factory=list[UiViolation])
    signals: list[UiSignal] = Field(default_factory=list[UiSignal])
    timings: dict[str, float] | None = None
    error: UiError | None = None

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        if self.status == JobStatus.DONE:
            if self.verdict is None:
                msg = "status=done требует verdict"
                raise ValueError(msg)
            if (self.verdict == "violation") != bool(self.violations):
                msg = "verdict=violation тогда и только тогда, когда есть события"
                raise ValueError(msg)
        elif self.verdict is not None:
            msg = "verdict есть только у status=done"
            raise ValueError(msg)
        if self.status == JobStatus.FAILED and self.error is None:
            msg = "status=failed требует понятную ошибку error"
            raise ValueError(msg)
        return self


class UiCatalogType(Contract):
    code: ViolationCode
    title_ru: str
    group: ViolationGroup
    verdict_impact: bool
    legal: list[UiLegalRef]


class UiCatalogSignal(Contract):
    code: SignalCode
    title_ru: str


class UiCatalog(Contract):
    """``GET /api/ui/catalog``: типы, сигналы и акты. ETag = ``config_hash``."""

    config_hash: str
    types: list[UiCatalogType]
    signals: list[UiCatalogSignal]
    acts: list[ActEntry]
