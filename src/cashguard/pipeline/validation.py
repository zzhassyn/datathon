"""Проверка числовых артефактов между стадиями K4."""

import math
from fractions import Fraction

from cashguard.contracts._base import SCHEMA_VERSION
from cashguard.contracts.findings import Candidate, Decision
from cashguard.contracts.perception import PerceptionTrace
from cashguard.contracts.scene import SceneModel
from cashguard.contracts.video import VideoMeta
from cashguard.pipeline.runner import PipelineMode
from cashguard.pipeline.runtime import StageResources


def validate_trace(
    trace: PerceptionTrace, video: VideoMeta, resources: StageResources, mode: PipelineMode
) -> None:
    if (
        trace.schema_version != SCHEMA_VERSION
        or trace.video != video
        or trace.plan != resources.plan
        or trace.models != dict(resources.expected_models)
    ):
        raise ValueError("Трасса не соответствует видео, моделям или плану задачи")
    if not trace.frames:
        raise ValueError("Пустая трасса не подтверждает отсутствие нарушений")
    if not all(
        math.isfinite(value)
        for value in (video.duration_sec, video.start_pts_sec, video.fps_avg, trace.plan.sample_fps)
    ):
        raise ValueError("Некорректные параметры времени видео")
    # The contract has no truncated-pass flag: unseen video must not become a clean result.
    margin = 1 / trace.plan.sample_fps + 1 / video.fps_avg
    if trace.frames[0].t_sec > margin or video.duration_sec - trace.frames[-1].t_sec > margin:
        raise ValueError("Неполная трасса: начало или конец видео не обработаны")
    expected_backend = {
        PipelineMode.STUB: {"stub"},
        PipelineMode.REPLAY: {"replay"},
        PipelineMode.FULL: {"torch", "coreml"},
    }
    if resources.backend.name not in expected_backend[mode]:
        raise ValueError("Бэкенд не соответствует режиму пайплайна")
    if mode != PipelineMode.STUB and not trace.models:
        raise ValueError("Реальный режим требует версий и хешей моделей")
    index = resources.store.index()
    if [(f.idx, f.t_sec) for f in trace.frames] != [(f.idx, f.t_sec) for f in index.frames]:
        raise ValueError("Трасса не покрывает все выбранные кадры хранилища")
    entries = {frame.idx: frame for frame in index.frames}
    if len(entries) != len(index.frames):
        raise ValueError("Повторяющиеся индексы кадров")
    time_base = Fraction(video.time_base)
    if time_base <= 0:
        raise ValueError("Некорректный time_base")
    previous_idx, previous_t = -1, -1.0
    for frame in trace.frames:
        if (
            not math.isfinite(frame.t_sec)
            or not previous_t < frame.t_sec <= video.duration_sec
            or frame.idx <= previous_idx
        ):
            raise ValueError("Немонотонная трасса или таймкод вне видео")
        entry = entries.get(frame.idx)
        if entry is None or abs(entry.t_sec - frame.t_sec) > 1e-6:
            raise ValueError("В хранилище нет кадра наблюдения")
        pts_time = float(entry.pts * time_base) - video.start_pts_sec
        if not math.isfinite(pts_time) or abs(pts_time - frame.t_sec) > 1e-6:
            raise ValueError("Время кадра не соответствует исходному PTS")
        previous_idx, previous_t = frame.idx, frame.t_sec


def validate_scene(scene: SceneModel, video: VideoMeta) -> None:
    intervals = [(s.start_t, s.end_t) for s in scene.states]
    intervals += [(r.first_t, r.last_t) for r in scene.roles]
    intervals += [
        (s.start_t, s.end_t if s.end_t is not None else video.duration_sec) for s in scene.sessions
    ]
    for start, end in intervals:
        if not (
            math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= video.duration_sec
        ):
            raise ValueError("Интервал сцены вне видео")
    if any(not math.isfinite(e.t) or e.t > video.duration_sec for e in scene.events):
        raise ValueError("Событие сцены вне видео")


def validate_decisions(decisions: list[Decision], candidates: list[Candidate]) -> None:
    originals = {candidate.id: candidate for candidate in candidates}
    if len(originals) != len(candidates):
        raise ValueError("Повторяющийся ID кандидата")
    if len(decisions) > 5 or len({d.candidate.id for d in decisions}) != len(decisions):
        raise ValueError("Некорректный набор решений")
    for decision in decisions:
        candidate = decision.candidate
        original = originals.get(candidate.id)
        if (
            original is None
            or not candidate.verdict_impact
            or not original.verdict_impact
            or candidate.code != original.code
            or candidate.t_sec != original.t_sec
            or candidate.facts != original.facts
            or not math.isfinite(decision.score)
            or decision.score > original.confidence
            or not 0 <= candidate.start_sec <= candidate.t_sec <= candidate.end_sec
            or not math.isfinite(candidate.end_sec)
        ):
            raise ValueError("Вердикт добавил или усилил неподтверждённое событие")
