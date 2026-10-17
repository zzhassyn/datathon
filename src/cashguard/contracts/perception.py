"""Результат распознавания — единица replay. Пишет S3, читают S4, S5, S6.

Трасса сохраняется как ``trace.json.gz`` и позволяет гонять сцену и правила без моделей.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from cashguard.contracts._base import SCHEMA_VERSION, Contract
from cashguard.contracts.geometry import BBox
from cashguard.contracts.jobs import PerceptionLevel
from cashguard.contracts.video import VideoMeta


class DetClass(StrEnum):
    """14 классов детектора. Порядок = id в ``training/configs/classes.yaml``; не менять."""

    PERSON = "person"
    CASH = "cash"
    COLLECTION_BAG = "collection_bag"
    SAFE_CLOSED = "safe_closed"
    SAFE_OPEN = "safe_open"
    LAPTOP = "laptop"
    DOOR = "door"
    COUNTING_MACHINE = "counting_machine"
    AUTH_DETECTOR = "auth_detector"
    ID_DOCUMENT = "id_document"
    PHONE = "phone"
    CUP = "cup"
    PERSONAL_BAG = "personal_bag"
    OUTERWEAR = "outerwear"


class CropTarget(StrEnum):
    """Мелкие состояния, которые классифицируются по увеличенному фрагменту."""

    SCREEN = "screen"
    SAFE_LOCK = "safe_lock"
    DOOR = "door"


class CropLabel(StrEnum):
    """Метки классификаторов (``training/configs/train.yaml``)."""

    ACTIVE = "active"
    LOCKED = "locked"
    OFF = "off"
    KEY = "key"
    NO_KEY = "no_key"
    CLOSED = "closed"
    OPEN = "open"


CROP_LABELS: dict[CropTarget, tuple[CropLabel, ...]] = {
    CropTarget.SCREEN: (CropLabel.ACTIVE, CropLabel.LOCKED, CropLabel.OFF),
    CropTarget.SAFE_LOCK: (CropLabel.KEY, CropLabel.NO_KEY),
    CropTarget.DOOR: (CropLabel.CLOSED, CropLabel.OPEN),
}

COCO_KEYPOINTS = 17


class Detection(Contract):
    cls: DetClass
    conf: float = Field(ge=0, le=1)
    box: BBox
    track_id: int | None = None


class PoseKeypoints(Contract):
    """17 точек COCO: (x, y, conf), координаты нормированы."""

    track_id: int | None
    box: BBox
    kpts: list[tuple[float, float, float]] = Field(
        min_length=COCO_KEYPOINTS, max_length=COCO_KEYPOINTS
    )


class CropState(Contract):
    target: CropTarget
    source_box: BBox
    label: CropLabel
    conf: float = Field(ge=0, le=1)


class FrameQuality(Contract):
    brightness: float
    blur: float


class FrameObservation(Contract):
    idx: int = Field(ge=0)
    t_sec: float = Field(ge=0)
    detections: list[Detection]
    poses: list[PoseKeypoints] | None = None
    crops: list[CropState] = Field(default_factory=list[CropState])
    quality: FrameQuality | None = None


class PerceptionPlan(Contract):
    level: PerceptionLevel
    sample_fps: float = Field(gt=0)
    pose_fps: float = Field(ge=0)
    cls_fps: float = Field(ge=0)
    imgsz_variant: Literal["864x480", "480x864"]


class ModelRef(Contract):
    version: str
    sha256: str


class PerceptionTrace(Contract):
    schema_version: int = SCHEMA_VERSION
    video: VideoMeta
    models: dict[str, ModelRef]
    plan: PerceptionPlan
    frames: list[FrameObservation]
