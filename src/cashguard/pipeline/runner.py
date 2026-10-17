"""Оркестрация стадий анализа. Сигнатуры стадий ЗАМОРОЖЕНЫ в P0 (ADR-0012); владелец — S0.

Стадии реализуют владельцы папок. Runner вызывает их только через типы ``StageSet``, поэтому
режимы ``stub``, ``replay`` и ``full`` проходят по одним и тем же стадиям (P1-S0-01):

    probe → make_preview → perception.run → scene.build → rules.evaluate_all → verdict.decide
    → evidence.render → legal.describe → render.jury / render.ui      (стадия perception)
    vlm.plan_questions → VlmBackend.ask → verdict.decide (второй проход)  (стадия vlm, S6)

Пайплайн не импортирует ``api``, ``jobs`` и ``workers``: воркер (S1) строит ``StageJob`` из
строки очереди и передаёт бюджет, совместимый с ``StageBudget``.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from cashguard.contracts.catalog import CatalogEntry, LegalRef, ViolationCode
from cashguard.contracts.findings import (
    AnalysisResult,
    Candidate,
    Decision,
    EvidenceImage,
    RuleConfig,
    RuleContext,
    Signal,
    VlmQuestion,
    VlmReview,
)
from cashguard.contracts.jury import JuryJobResponse
from cashguard.contracts.perception import PerceptionPlan, PerceptionTrace
from cashguard.contracts.ports import FrameStore, PerceptionBackend, ProgressFn
from cashguard.contracts.scene import SceneModel
from cashguard.contracts.ui import UiJobView
from cashguard.contracts.video import VideoMeta

Config = Mapping[str, Any]
"""Проверенный YAML-конфиг стадии (scene.yaml, vlm.yaml, …); модель конфига — у владельца стадии."""


class PipelineMode(StrEnum):
    STUB = "stub"
    """Детерминированный фиктивный результат без моделей (P0–P1, проверка контракта)."""
    REPLAY = "replay"
    """Сцена и правила по сохранённым трассам распознавания (Windows, eval)."""
    FULL = "full"
    """Полный пайплайн с моделями (Mac)."""


# ---------------------------------------------------------------- сигнатуры стадий (ЗАМОРОЖЕНЫ)

ProbeFn = Callable[[Path], VideoMeta]
"""ingest.probe(path) -> VideoMeta  [S1]"""

MakePreviewFn = Callable[[Path, VideoMeta, Path], Path | None]
"""ingest.make_preview(path, meta, out) -> Path | None  [S1]"""

PerceptionRunFn = Callable[
    [Path, VideoMeta, PerceptionPlan, PerceptionBackend, FrameStore, ProgressFn], PerceptionTrace
]
"""perception.run(path, meta, plan, backend, store, progress) -> PerceptionTrace  [S3]"""

SceneBuildFn = Callable[[PerceptionTrace, Config], SceneModel]
"""scene.build(trace, cfg) -> SceneModel  [S4]"""

RulesEvaluateFn = Callable[
    [RuleContext, Mapping[str, RuleConfig]], tuple[list[Candidate], list[Signal]]
]
"""rules.evaluate_all(ctx, configs) -> (candidates, signals)  [S0, реестр]"""

VerdictDecideFn = Callable[[list[Candidate], VlmReview | None, Config], list[Decision]]
"""verdict.decide(cands, review | None, cfg) -> list[Decision]  [S5]"""

EvidenceRenderFn = Callable[[list[Decision], FrameStore, Path], dict[str, list[EvidenceImage]]]
"""evidence.render(decisions, store, out_dir) -> {candidate_id: [EvidenceImage]}  [S3]"""

LegalDescribeFn = Callable[
    [Decision, SceneModel, Mapping[ViolationCode, CatalogEntry]], tuple[str, list[LegalRef]]
]
"""legal.describe(decision, scene, catalog) -> (description_ru, legal)  [S5]"""

VlmPlanFn = Callable[[list[Decision], FrameStore, Config], list[VlmQuestion]]
"""vlm.plan_questions(decisions, store, cfg) -> list[VlmQuestion]  [S6]"""

RenderJuryFn = Callable[[AnalysisResult, Path], JuryJobResponse]
"""render.jury(result, evidence_dir) -> JuryJobResponse (проверен, с безопасным запасным)  [S1]"""

RenderUiFn = Callable[[AnalysisResult], UiJobView]
"""render.ui(result) -> UiJobView  [S1]"""


@dataclass(frozen=True)
class StageSet:
    """Набор реализаций стадий для одного режима."""

    probe: ProbeFn
    make_preview: MakePreviewFn
    perception_run: PerceptionRunFn
    scene_build: SceneBuildFn
    rules_evaluate: RulesEvaluateFn
    verdict_decide: VerdictDecideFn
    evidence_render: EvidenceRenderFn
    legal_describe: LegalDescribeFn
    vlm_plan: VlmPlanFn
    render_jury: RenderJuryFn
    render_ui: RenderUiFn


@dataclass(frozen=True)
class StageJob:
    """Задача глазами пайплайна. Строит воркер (S1) из строки очереди."""

    job_id: str
    job_dir: Path
    """``var/jobs/{job_id}``: все файлы задачи пишутся атомарно до смены статуса в БД."""
    input_path: Path
    source: str
    """``jury`` или ``ui``: превью нужно только интерфейсу."""
    mode: PipelineMode


class StageBudget(Protocol):
    """Бюджет времени задачи (ADR-0005). Реализация — ``cashguard.core.budget.Budget`` (S1)."""

    def now(self) -> float: ...

    def remaining(self, tier: str) -> float: ...

    def allows(
        self, est_sec: float, tier: str = "vlm_start_cutoff", margin_sec: float = 15
    ) -> bool: ...


def load_stages(mode: PipelineMode) -> StageSet:
    """Реализации стадий для режима. Подключаются в P1-S0-01 по мере готовности срезов S1–S6."""
    msg = f"стадии режима {mode} подключаются в P1-S0-01"
    raise NotImplementedError(msg)


def run_perception_stage(job: StageJob, budget: StageBudget, stages: StageSet) -> AnalysisResult:
    """Стадия perception: от видео до предварительного результата по правилам.

    Пишет ``trace.json.gz``, ``candidates.json``, ``evidence/``, ``analysis.json`` и предварительный
    ``jury.json``; возвращает предварительный ``AnalysisResult`` (уровень ``rules``).
    """
    msg = "реализация — P1-S0-01 (сборка вертикального среза)"
    raise NotImplementedError(msg)


def run_vlm_stage(job: StageJob, budget: StageBudget, stages: StageSet) -> AnalysisResult:
    """Стадия vlm: проверка кандидатов Qwen и второй проход ``decide`` (только понижает балл).

    Запускается, только если ``budget.allows(est_vlm)``, свободно >= 3 ГБ и очередь позволяет.
    """
    msg = "реализация — S6 (карточка run_vlm_stage в docs/roadmap.md)"
    raise NotImplementedError(msg)
