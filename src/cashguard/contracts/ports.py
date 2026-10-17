"""Порты (``typing.Protocol``) между стадиями.

Реализации подменяются: coreml | torch | replay | stub | null.

Благодаря портам сцену и правила можно разрабатывать на Windows без моделей (режим replay).
"""

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from cashguard.contracts.findings import (
    Candidate,
    KeyframeFinding,
    RuleContext,
    Signal,
    VlmAnswer,
    VlmQuestion,
)
from cashguard.contracts.perception import PerceptionPlan, PerceptionTrace
from cashguard.contracts.video import FrameIndex, FrameRef, SampledFrame, VideoMeta

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

ProgressFn = Callable[[str, float, str], None]
"""``progress(stage_code, frac 0..1, message)``."""


class Clock(Protocol):
    def now(self) -> float:
        """Wall-clock, секунды эпохи (общие для процессов сроки)."""
        ...

    def monotonic(self) -> float:
        """Монотонное время для замеров внутри процесса."""
        ...


class VideoSource(Protocol):
    def probe(self, path: Path) -> VideoMeta: ...

    def iter_samples(self, path: Path, plan: PerceptionPlan) -> Iterator[SampledFrame]: ...

    def decode_window(self, path: Path, t0: float, t1: float, fps: float) -> list[SampledFrame]: ...


class FrameStore(Protocol):
    def put(self, frame: SampledFrame) -> FrameRef: ...

    def get(self, t_sec: float) -> "npt.NDArray[np.uint8]": ...

    def nearest(self, t_sec: float) -> FrameRef: ...

    def index(self) -> FrameIndex: ...


class PerceptionBackend(Protocol):
    name: str

    def warmup(self) -> None: ...

    def info(self) -> dict[str, str]: ...

    def run(
        self, samples: Iterator[SampledFrame], plan: PerceptionPlan, progress: ProgressFn
    ) -> PerceptionTrace: ...


@runtime_checkable
class Rule(Protocol):
    """Правило, влияющее на вердикт. Регистрируется ``@register_rule``, центрального списка нет."""

    code: ClassVar[str]
    version: ClassVar[str]
    verdict_impact: ClassVar[bool]

    def evaluate(self, ctx: RuleContext) -> list[Candidate]: ...


@runtime_checkable
class SignalRule(Protocol):
    """Сигнал для проверяющего. На вердикт не влияет."""

    code: ClassVar[str]
    version: ClassVar[str]

    def evaluate(self, ctx: RuleContext) -> list[Signal]: ...


class VlmBackend(Protocol):
    name: str

    def load(self, path: Path, quant: str) -> None: ...

    def warmup(self) -> None: ...

    def ask(
        self, q: VlmQuestion, images: Sequence["npt.NDArray[np.uint8]"], deadline_ts: float
    ) -> VlmAnswer: ...

    def scan_keyframes(
        self,
        images: Sequence["npt.NDArray[np.uint8]"],
        times: Sequence[float],
        deadline_ts: float,
    ) -> list[KeyframeFinding]: ...
