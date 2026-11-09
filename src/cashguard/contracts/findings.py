"""Кандидаты правил, вопросы и ответы VLM, решения и итог анализа."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from cashguard.contracts._base import SCHEMA_VERSION, Contract
from cashguard.contracts.catalog import LegalRef, SignalCode, ViolationCode
from cashguard.contracts.diagnostics import RuleExplanation, VisibilityAssessment
from cashguard.contracts.geometry import BBox
from cashguard.contracts.jobs import Degradation, PerceptionLevel, ResultLevel
from cashguard.contracts.perception import PerceptionTrace
from cashguard.contracts.scene import SceneModel
from cashguard.contracts.video import FrameRef, VideoMeta

Fact = str | int | float
Param = str | int | float | bool


class RuleConfig(Contract):
    """``configs/rules/<CODE>.yaml``: порог и параметры правила (подбираются на LOSO)."""

    code: str
    enabled: bool = True
    tau: float = Field(default=0.5, ge=0, le=1)
    params: dict[str, Param] = Field(default_factory=dict[str, Param])


class RuleContext(Contract):
    video: VideoMeta
    trace: PerceptionTrace
    scene: SceneModel
    cfg: RuleConfig


class EvidenceKind(StrEnum):
    EVENT = "event"
    ZOOM = "zoom"
    CONTEXT = "context"


class EvidenceRequest(Contract):
    kind: EvidenceKind
    t_sec: float = Field(ge=0)
    box: BBox | None = None
    label: str


class Candidate(Contract):
    id: str
    code: ViolationCode
    t_sec: float = Field(ge=0)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    facts: dict[str, Fact] = Field(default_factory=dict[str, Fact])
    """Переменные шаблона описания."""
    evidence: list[EvidenceRequest] = Field(default_factory=list[EvidenceRequest])
    vlm_group: str | None = None
    rule_version: str
    verdict_impact: bool


class Signal(Contract):
    id: str
    code: SignalCode
    t_sec: float = Field(ge=0)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    facts: dict[str, Fact] = Field(default_factory=dict[str, Fact])


class VlmQuestionItem(Contract):
    qid: str
    code: ViolationCode
    text_ru: str


class VlmQuestion(Contract):
    group_id: str
    candidate_ids: list[str]
    questions: list[VlmQuestionItem]
    frames: list[FrameRef]
    max_tokens: int = Field(gt=0)


class VlmAnswerItem(Contract):
    qid: str
    a: Literal["yes", "no", "unclear"]
    c: float = Field(ge=0, le=1)


class VlmAnswer(Contract):
    group_id: str
    answers: list[VlmAnswerItem]
    note: str | None = Field(default=None, max_length=200)
    """Только для интерфейса и отладки; в описание жюри не попадает."""
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    model_rev: str


class KeyframeFinding(Contract):
    t_sec: float = Field(ge=0)
    suggested_code: ViolationCode | None = None
    confidence: float = Field(ge=0, le=1)
    summary_ru: str


class VlmReview(Contract):
    answers: list[VlmAnswer] = Field(default_factory=list[VlmAnswer])
    findings: list[KeyframeFinding] = Field(default_factory=list[KeyframeFinding])
    skipped_reason: str | None = None


class VlmStatus(StrEnum):
    CONFIRMED = "confirmed"
    DOWNWEIGHTED = "downweighted"
    UNVERIFIED = "unverified"
    SKIPPED = "skipped"


class Decision(Contract):
    candidate: Candidate
    score: float = Field(ge=0, le=1)
    vlm_status: VlmStatus


class EvidenceImage(Contract):
    kind: EvidenceKind
    timestamp_sec: float = Field(ge=0)
    file: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ViolationFinding(Contract):
    id: str
    code: ViolationCode
    title_ru: str
    timecode_sec: float = Field(ge=0)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    vlm_status: VlmStatus
    provisional: bool
    description_ru: str = Field(min_length=1)
    legal: list[LegalRef]
    evidence: list[EvidenceImage] = Field(min_length=1, max_length=3)
    explanation: RuleExplanation | None = None

    @model_validator(mode="after")
    def _explanation_refs(self) -> Self:
        if self.explanation is not None:
            self.explanation.validate_evidence_count(len(self.evidence))
        return self


class SignalFinding(Contract):
    id: str
    code: SignalCode
    title_ru: str
    t_sec: float = Field(ge=0)
    description_ru: str
    evidence: list[EvidenceImage] = Field(default_factory=list[EvidenceImage])


class AnalysisVersions(Contract):
    git_sha: str
    models_hash: str
    config_hash: str


class AnalysisResult(Contract):
    """``analysis.json``: полный внутренний итог. Из него рендерятся ответ жюри и интерфейс."""

    schema_version: int = SCHEMA_VERSION
    job_id: str
    verdict: Literal["violation", "no_violation"]
    result_level: ResultLevel
    perception_level: PerceptionLevel
    violations: list[ViolationFinding]
    signals: list[SignalFinding]
    video: VideoMeta
    timings: dict[str, float] = Field(default_factory=dict[str, float])
    degradations: list[Degradation] = Field(default_factory=list[Degradation])
    versions: AnalysisVersions
    visibility: VisibilityAssessment | None = None

    @model_validator(mode="after")
    def _visibility_duration(self) -> Self:
        if self.visibility is not None:
            self.visibility.validate_duration(self.video.duration_sec)
        return self
