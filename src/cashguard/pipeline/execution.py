"""Исполнение perception-стадии через замороженные порты StageSet."""

import gzip
import math
import re
import time
from collections.abc import Callable
from typing import TypeVar

from cashguard.contracts.findings import (
    AnalysisResult,
    Candidate,
    RuleConfig,
    RuleContext,
    SignalFinding,
    ViolationFinding,
)
from cashguard.contracts.jobs import Degradation, DegradationReason, JobStage, ResultLevel
from cashguard.contracts.perception import PerceptionTrace
from cashguard.contracts.scene import SceneModel
from cashguard.contracts.video import VideoMeta
from cashguard.core.atomic import atomic_write_bytes, atomic_write_json
from cashguard.pipeline.render.analysis import render_analysis
from cashguard.pipeline.runner import StageBudget, StageJob, StageSet
from cashguard.pipeline.runtime import BoundStageSet, StageResources
from cashguard.pipeline.validation import validate_decisions, validate_scene, validate_trace

T = TypeVar("T")


def _check_budget(budget: StageBudget) -> None:
    remaining = budget.remaining("finalize_at")
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError("Исчерпан бюджет обработки задачи")


def _findings(decisions, images, scene, stages: StageSet, resources: StageResources):
    findings = []
    for decision in decisions:
        candidate = decision.candidate
        evidence = images.get(candidate.id, [])
        if not 1 <= len(evidence) <= 3 or evidence[0].kind != "event":
            raise ValueError("Нет основного кадра-доказательства")
        expected_t = resources.store.nearest(candidate.t_sec).t_sec
        if abs(evidence[0].timestamp_sec - expected_t) > 1e-6:
            raise ValueError("Evidence не соответствует выбранному PTS")
        if any(frame.kind == "zoom" and frame.timestamp_sec != expected_t for frame in evidence):
            raise ValueError("Zoom не соответствует кадру события")
        entry = resources.catalog.entries[candidate.code]
        if not entry.verdict_impact:
            raise ValueError("Код каталога не влияет на вердикт")
        description, legal = stages.legal_describe(decision, scene, resources.catalog.entries)
        if any(ref not in entry.legal for ref in legal):
            raise ValueError("Стадия описания добавила норму вне каталога")
        findings.append(
            ViolationFinding(
                id=candidate.id,
                code=candidate.code,
                title_ru=entry.title_ru,
                timecode_sec=expected_t,
                start_sec=min(candidate.start_sec, expected_t),
                end_sec=max(candidate.end_sec, expected_t),
                confidence=decision.score,
                vlm_status=decision.vlm_status,
                provisional=True,
                description_ru=description,
                legal=legal,
                evidence=evidence,
            )
        )
    return findings


def execute_perception(job: StageJob, budget: StageBudget, stages: StageSet) -> AnalysisResult:
    if not isinstance(stages, BoundStageSet):
        raise RuntimeError("Не подключены ресурсы стадий")
    if job.source not in {"ui", "jury"} or not re.fullmatch(r"[a-f0-9]{32}", job.job_id):
        raise ValueError("Неверный идентификатор или источник задачи")
    _check_budget(budget)
    timings: dict[str, float] = {}
    started = time.monotonic()
    video = VideoMeta.model_validate(stages.probe(job.input_path).model_dump())
    timings["probe"] = time.monotonic() - started
    _check_budget(budget)
    resources = stages.resource_factory(job, video, budget)
    if any(code != cfg.code for code, cfg in resources.rule_configs.items()):
        raise ValueError("Код настройки правила не соответствует ключу")
    active_codes = {cfg.code for cfg in resources.rule_configs.values() if cfg.enabled}
    if not any(
        entry.verdict_impact and str(entry.code) in active_codes
        for entry in resources.catalog.entries.values()
    ):
        raise ValueError("Нет включённых правил вердикта")

    def progress(stage: str, fraction: float, message: str) -> None:
        _check_budget(budget)
        if not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError("Некорректный прогресс стадии")
        resources.progress(stage, fraction, message)

    def step(code: str, message: str, operation: Callable[[], T]) -> T:
        progress(code, 0, message)
        begin = time.monotonic()
        value = operation()
        timings[code] = timings.get(code, 0) + time.monotonic() - begin
        progress(code, 1, message)
        return value

    progress("probe", 1, "Параметры видео проверены")
    if job.source == "ui":
        _check_budget(budget)
        stages.make_preview(job.input_path, video, job.job_dir / "preview.mp4")
        _check_budget(budget)
    trace = step(
        "perception",
        "Распознавание объектов и состояний",
        lambda: stages.perception_run(
            job.input_path, video, resources.plan, resources.backend, resources.store, progress
        ),
    )
    trace = PerceptionTrace.model_validate(trace.model_dump())
    validate_trace(trace, video, resources, job.mode)
    atomic_write_bytes(
        job.job_dir / "trace.json.gz",
        gzip.compress(trace.model_dump_json().encode("utf-8"), mtime=0),
    )
    scene = step(
        "rules", "Построение сцены", lambda: stages.scene_build(trace, resources.scene_config)
    )
    scene = SceneModel.model_validate(scene.model_dump())
    validate_scene(scene, video)
    atomic_write_json(job.job_dir / "scene.json", scene.model_dump(mode="json"))
    ctx = RuleContext(video=video, trace=trace, scene=scene, cfg=RuleConfig(code="base"))
    candidates, signals = step(
        "rules", "Проверка правил", lambda: stages.rules_evaluate(ctx, resources.rule_configs)
    )
    candidates = [Candidate.model_validate(c.model_dump()) for c in candidates]
    for candidate in candidates:
        if (
            not 0
            <= candidate.start_sec
            <= candidate.t_sec
            <= candidate.end_sec
            <= video.duration_sec
            or candidate.verdict_impact != resources.catalog.entries[candidate.code].verdict_impact
            or str(candidate.code) not in active_codes
        ):
            raise ValueError("Кандидат вне видео или несовместим с каталогом")
    atomic_write_json(
        job.job_dir / "candidates.json",
        {
            "candidates": [c.model_dump(mode="json") for c in candidates],
            "signals": [s.model_dump(mode="json") for s in signals],
        },
    )
    # An owner-supplied stage must not be able to mutate the reference used for verification.
    originals = [c.model_copy(deep=True) for c in candidates]
    decisions = step(
        "rules",
        "Формирование вердикта",
        lambda: stages.verdict_decide(candidates, None, resources.verdict_config),
    )
    validate_decisions(decisions, originals)
    atomic_write_json(
        job.job_dir / "decisions.json", [d.model_dump(mode="json") for d in decisions]
    )
    images = step(
        "evidence",
        "Подготовка кадров-доказательств",
        lambda: stages.evidence_render(decisions, resources.store, job.job_dir / "evidence"),
    )
    violations = _findings(decisions, images, scene, stages, resources)
    signal_entries = {entry.code: entry for entry in resources.catalog.signals.signals}
    signal_findings = []
    for signal in signals:
        entry = signal_entries[signal.code]
        if not 0 <= signal.start_sec <= signal.t_sec <= signal.end_sec <= video.duration_sec:
            raise ValueError("Сигнал вне видео")
        if not set(entry.required_facts) <= signal.facts.keys():
            raise ValueError("Нет фактов для описания сигнала")
        signal_findings.append(
            SignalFinding(
                id=signal.id,
                code=signal.code,
                title_ru=entry.title_ru,
                t_sec=signal.t_sec,
                description_ru=entry.template_ru.format_map(signal.facts),
                evidence=[],
            )
        )
    result = AnalysisResult(
        job_id=job.job_id,
        verdict="violation" if violations else "no_violation",
        result_level=ResultLevel.RULES,
        perception_level=trace.plan.level,
        violations=violations,
        signals=signal_findings,
        video=video,
        timings=dict(timings),
        degradations=[Degradation(stage=JobStage.VLM, reason=DegradationReason.VLM_DISABLED)],
        versions=resources.versions,
    )
    # Trusted publication adapter also entropy-decodes JPEGs and checks the frozen schema.
    verified = render_analysis(result, job.job_dir / "evidence")
    jury = stages.render_jury(result, job.job_dir / "evidence")
    ui = stages.render_ui(result)
    if jury != verified.jury or ui.job_id != job.job_id or ui.pipeline_mode != job.mode:
        raise ValueError("Рендер не соответствует результату задачи")
    ui_fields = ui.model_dump(mode="json")
    if any(ui_fields[key] != value for key, value in verified.ui.items()):
        raise ValueError("Рендер UI не соответствует результату задачи")
    _check_budget(budget)
    atomic_write_json(job.job_dir / "jury.json", jury.model_dump(mode="json"))
    atomic_write_json(job.job_dir / "ui_result.json", verified.ui)
    # analysis.json is written last, only after every stage and media validation succeeded.
    atomic_write_json(job.job_dir / "analysis.json", result.model_dump(mode="json"))
    return result
