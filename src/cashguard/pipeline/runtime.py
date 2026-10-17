"""Явная привязка ресурсов к замороженным портам стадий, без ML-импортов.

Каждый spawned worker регистрирует свою фабрику при запуске. Контекст не
наследуется другим процессом и не включает режимы, чьи адаптеры не поставлены.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, replace

from cashguard.contracts.findings import AnalysisVersions, RuleConfig
from cashguard.contracts.perception import ModelRef, PerceptionPlan
from cashguard.contracts.ports import FrameStore, PerceptionBackend, ProgressFn
from cashguard.contracts.video import VideoMeta
from cashguard.pipeline.legal.catalog import Catalog
from cashguard.pipeline.runner import Config, PipelineMode, StageBudget, StageJob, StageSet


@dataclass(frozen=True)
class StageResources:
    plan: PerceptionPlan
    backend: PerceptionBackend
    store: FrameStore
    catalog: Catalog
    versions: AnalysisVersions
    expected_models: Mapping[str, ModelRef]
    rule_configs: Mapping[str, RuleConfig]
    scene_config: Config
    verdict_config: Config
    progress: ProgressFn


ResourceFactory = Callable[[StageJob, VideoMeta, StageBudget], StageResources]


@dataclass(frozen=True)
class BoundStageSet(StageSet):
    resource_factory: ResourceFactory


def bind_stages(stages: StageSet, factory: ResourceFactory) -> BoundStageSet:
    return BoundStageSet(
        **{f.name: getattr(stages, f.name) for f in fields(StageSet)}, resource_factory=factory
    )


def with_progress(stages: StageSet, progress: ProgressFn) -> BoundStageSet:
    """Worker привязывает lease-aware progress, сохраняя замороженный StageJob."""
    if not isinstance(stages, BoundStageSet):
        raise RuntimeError("Не подключены ресурсы стадий")

    def resources(job: StageJob, video: VideoMeta, budget: StageBudget) -> StageResources:
        return replace(stages.resource_factory(job, video, budget), progress=progress)

    return bind_stages(stages, resources)


StageFactory = Callable[[], BoundStageSet]
_factories: ContextVar[Mapping[PipelineMode, StageFactory] | None] = ContextVar(
    "stage_factories", default=None
)


@contextmanager
def stage_factory(mode: PipelineMode, factory: StageFactory) -> Iterator[None]:
    """Привязка на срок жизни worker; вложенные контексты восстанавливаются."""
    token = _factories.set(dict(_factories.get() or {}) | {PipelineMode(mode): factory})
    try:
        yield
    finally:
        _factories.reset(token)


def load_bound_stages(mode: PipelineMode) -> BoundStageSet:
    factory = (_factories.get() or {}).get(PipelineMode(mode))
    if factory is None:
        raise RuntimeError(
            f"Набор стадий режима {mode} не подключен; нужны ML/scene/rules adapters"
        )
    stages = factory()
    if not isinstance(stages, BoundStageSet):
        raise RuntimeError("Фабрика не подключила ресурсы стадий")
    return stages
