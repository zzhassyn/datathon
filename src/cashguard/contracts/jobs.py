"""Состояние задач: перечисления, сроки, деградации, стадии для интерфейса.

Таблицы SQLite и репозиторий — в ``cashguard.jobs`` (S1); класс ``Budget`` с поведением — в
``cashguard.core.budget`` (S1). Здесь только данные, общие для процессов.
"""

from enum import StrEnum

from pydantic import Field

from cashguard.contracts._base import Contract


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobStage(StrEnum):
    PERCEPTION = "perception"
    VLM = "vlm"
    FINAL = "final"


class JobSource(StrEnum):
    JURY = "jury"
    UI = "ui"


class PerceptionLevel(StrEnum):
    FULL = "full"
    FAST = "fast"
    MIN = "min"


class ResultLevel(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    RULES = "rules"


class ErrorCode(StrEnum):
    UNSUPPORTED_VIDEO = "UNSUPPORTED_VIDEO"
    PIPELINE_CRASH = "PIPELINE_CRASH"
    INTERNAL = "INTERNAL"


class DegradationReason(StrEnum):
    VLM_DEADLINE = "vlm_deadline"
    VLM_MEMORY = "vlm_memory"
    VLM_CRASH = "vlm_crash"
    VLM_BACKLOG = "vlm_backlog"
    VLM_DISABLED = "vlm_disabled"
    PERCEPTION_FAST = "perception_fast"
    PERCEPTION_MIN = "perception_min"
    DEADLINE_FINALIZE = "deadline_finalize"


class StageCode(StrEnum):
    QUEUED = "queued"
    PROBE = "probe"
    PERCEPTION = "perception"
    RULES = "rules"
    EVIDENCE = "evidence"
    VLM = "vlm"
    DONE = "done"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class DeadlineTiers(Contract):
    """Ступени сроков задачи (ADR-0005). Эпохи wall-clock в секундах, общие для процессов."""

    t0: float
    vision_target: float
    vlm_start_cutoff: float
    vlm_stop: float
    finalize_at: float


class Degradation(Contract):
    stage: JobStage
    reason: DegradationReason


class StageState(Contract):
    """Стадия обработки для интерфейса."""

    code: StageCode
    status: StageStatus
    progress: float = Field(ge=0, le=1)
    elapsed_sec: float | None = None
    note_ru: str | None = None
