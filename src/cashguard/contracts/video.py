"""Видео и кадры. Время кадра всегда из PTS (ADR-0011), не из ``frame_index / fps``."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import Field

from cashguard.contracts._base import Contract
from cashguard.contracts.geometry import BBox

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt


class VideoMeta(Contract):
    duration_sec: float = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    rotation_deg: int = 0
    fps_avg: float = Field(gt=0)
    is_vfr: bool
    codec: str
    pix_fmt: str
    start_pts_sec: float
    time_base: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SampledFrame:
    """Кадр в памяти. Никогда не сериализуется."""

    idx: int
    t_sec: float
    pts: int
    image: "npt.NDArray[np.uint8]"
    """BGR, после поворота."""


class FrameRef(Contract):
    t_sec: float = Field(ge=0)
    path: str
    box: BBox | None = None
    scale: float = Field(default=1.0, gt=0)


class FrameIndexEntry(Contract):
    idx: int = Field(ge=0)
    t_sec: float = Field(ge=0)
    pts: int
    path: str


class FrameIndex(Contract):
    frames: list[FrameIndexEntry]
