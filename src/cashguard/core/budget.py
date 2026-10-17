"""Persist wall-clock deadlines; measure elapsed time monotonically in-process.

These internal types will adapt to S0's contracts.jobs when that module exists.
"""

import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_OFFSETS = {
    "vision_target": 150.0,
    "vlm_start_cutoff": 180.0,
    "vlm_stop": 210.0,
    "finalize_at": 225.0,
    "perception_hard_stop": 235.0,
}


def load_deadline_offsets(path: Path | None = None) -> dict[str, float]:
    """Validate the S1 config at startup; defaults are ADR-0005 estimates."""
    if path is None:
        return dict(DEFAULT_OFFSETS)
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_OFFSETS):
        raise ValueError("deadline config requires exactly the five deadline offsets")
    result = {key: float(raw[key]) for key in DEFAULT_OFFSETS}
    ordered = list(result.values())
    if not all(math.isfinite(value) and value > 0 for value in ordered):
        raise ValueError("deadline offsets must be positive and finite")
    if ordered != sorted(set(ordered)) or ordered[-1] > 235:
        raise ValueError("deadline offsets must increase and end by 235 seconds")
    return result


@dataclass(frozen=True)
class DeadlineTiers:
    t0: float
    vision_target: float
    vlm_start_cutoff: float
    vlm_stop: float
    finalize_at: float
    perception_hard_stop: float

    @classmethod
    def from_received_at(
        cls, t0: float, offsets: dict[str, float] | None = None
    ) -> "DeadlineTiers":
        if not math.isfinite(t0):
            raise ValueError("received_at must be finite")
        values = DEFAULT_OFFSETS if offsets is None else offsets
        return cls(t0=t0, **{key: t0 + values[key] for key in DEFAULT_OFFSETS})

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


class Budget:
    def __init__(
        self,
        tiers: DeadlineTiers,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tiers = tiers
        self._monotonic = monotonic
        self._wall_anchor = wall_clock()
        self._mono_anchor = monotonic()

    def now(self) -> float:
        return self._wall_anchor + self._monotonic() - self._mono_anchor

    def remaining(self, tier: str = "perception_hard_stop") -> float:
        if tier not in self.tiers.as_dict() or tier == "t0":
            raise ValueError("unknown deadline tier")
        return max(0.0, getattr(self.tiers, tier) - self.now())

    def allows(
        self, est_sec: float, tier: str = "vlm_start_cutoff", margin_sec: float = 15.0
    ) -> bool:
        if (
            not math.isfinite(est_sec)
            or est_sec < 0
            or not math.isfinite(margin_sec)
            or margin_sec < 0
        ):
            raise ValueError("invalid time estimate")
        return self.remaining(tier) > est_sec + margin_sec
