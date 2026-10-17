"""Контракт веб-интерфейса (``/api/ui``). Через OpenAPI превращается в типы ``web/src/shared/api``.

В отличие от jury API кадры отдаются ссылками, а не base64, и есть сигналы.
"""

from typing import Literal

from pydantic import Field

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


class UiEvidence(Contract):
    timestamp_sec: float = Field(ge=0)
    url: str
    kind: EvidenceKind
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class UiViolation(Contract):
    id: str
    code: ViolationCode
    title_ru: str
    timecode_sec: float = Field(ge=0)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    vlm_status: VlmStatus
    provisional: bool
    description_ru: str
    legal: list[UiLegalRef]
    evidence: list[UiEvidence]


class UiSignal(Contract):
    id: str
    code: SignalCode
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
    created_at: float
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
