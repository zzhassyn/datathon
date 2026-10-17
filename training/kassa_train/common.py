"""Общие помощники пайплайна обучения: пути, имена роликов, каталог событий, классы.

Все шаги (training/scripts/NN_*.py) импортируют только отсюда, чтобы соглашения
о путях и форматах были в одном месте.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Операции, которых нет в MPS, — на CPU. PyTorch читает переменную один раз при import torch, а common
# импортируется каждым шагом раньше torch (pick_device импортирует его лениво), поэтому — здесь.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAINING_DIR = REPO_ROOT / "training"
CONFIG_DIR = TRAINING_DIR / "configs"

# Х-47 и Х-48 — побайтные копии Х-4 и Х-5 (Х-47 несёт второе событие ролика Х-4).
DUPLICATES = {"X-47": "X-4", "X-48": "X-5"}

_CYRILLIC_X = {"Х": "X", "х": "x"}
# «╒-13», «ò-13», «╨Ñ-13»: имя «Х-13» из zip без флага UTF-8 (встроенный архиватор Windows),
# распакованное в другой кодировке. Все ролики называются Х-<номер>, поэтому 1–2 не-ASCII символа
# перед «-<цифры>» — это испорченная «Х».
_MOJIBAKE_X_RE = re.compile(r"[^\x00-\x7f]{1,2}(-\d+)")


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def pipeline_config() -> dict:
    return load_yaml(CONFIG_DIR / "pipeline.yaml")


def data_dir() -> Path:
    env = os.environ.get("KASSA_DATA_DIR")
    root = Path(env) if env else REPO_ROOT / pipeline_config()["paths"]["data_dir"]
    root.mkdir(parents=True, exist_ok=True)
    return root


def data_path(key: str) -> Path:
    """Путь из pipeline.yaml → paths.<key> внутри data_dir."""
    return data_dir() / pipeline_config()["paths"][key]


def repo_path(key: str) -> Path:
    """Путь из pipeline.yaml → paths.<key> относительно корня репозитория (runs, models, catalog)."""
    return REPO_ROOT / pipeline_config()["paths"][key]


def norm_video_id(name: str) -> str:
    """'Х-13.mov' / 'X-13' / ' Х-13 ' → 'X-13' (ASCII, без расширения)."""
    stem = Path(str(name).strip()).stem if "." in str(name) else str(name).strip()
    for cyr, lat in _CYRILLIC_X.items():
        stem = stem.replace(cyr, lat)
    m = _MOJIBAKE_X_RE.fullmatch(stem)
    if m:
        stem = "X" + m.group(1)
    return stem.upper()


def parse_ru_timestamp(text: str | None) -> float | None:
    """'22 секунда' → 22.0; '1минута 8 секунда' → 68.0; пусто → None."""
    if text is None or str(text).strip() == "":
        return None
    nums = [int(n) for n in re.findall(r"\d+", str(text))]
    if not nums:
        return None
    if "мин" in str(text).lower() and len(nums) >= 2:
        return float(nums[0] * 60 + nums[1])
    return float(nums[0])


@dataclass
class VideoInfo:
    video_id: str
    label: str                     # violation | no_violation
    scene: str                     # L1..L5
    events: list[float] = field(default_factory=list)
    types: list[str] = field(default_factory=list)


def load_catalog() -> dict[str, VideoInfo]:
    """Каталог роликов из analysis/labels_typed.csv; события дублей сливаются в оригинал."""
    catalog: dict[str, VideoInfo] = {}
    with open(repo_path("catalog"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            vid = norm_video_id(row["file"])
            target = DUPLICATES.get(vid, vid)
            info = catalog.setdefault(target, VideoInfo(target, row["label"], row["scene"]))
            if row["label"] == "violation":
                info.label = "violation"
            if row.get("timecode_sec"):
                info.events.append(float(row["timecode_sec"]))
            if row.get("type_code"):
                info.types.append(row["type_code"])
    for info in catalog.values():
        info.events.sort()
    return catalog


def load_classes() -> list[dict]:
    classes = load_yaml(CONFIG_DIR / "classes.yaml")["classes"]
    ids = [c["id"] for c in classes]
    assert ids == list(range(len(classes))), "id классов должны идти подряд с 0"
    return classes


def class_names() -> list[str]:
    return [c["name"] for c in load_classes()]


def class_id(name: str) -> int:
    names = class_names()
    if name not in names:
        raise KeyError(f"неизвестный класс {name!r}; есть: {names}")
    return names.index(name)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def frame_stem(video_id: str, t_sec: float) -> str:
    """Имя кадра: X-13_t0047250 (миллисекунды, 7 знаков) — сортируется по времени."""
    return f"{video_id}_t{int(round(t_sec * 1000)):07d}"


@dataclass
class Box:
    """Рамка в нормированных координатах YOLO (центр, ширина, высота в долях кадра)."""
    cls: int
    cx: float
    cy: float
    w: float
    h: float
    score: float = 1.0
    source: str = "manual"

    def to_yolo_line(self) -> str:
        return f"{self.cls} {self.cx:.6f} {self.cy:.6f} {self.w:.6f} {self.h:.6f}"

    @staticmethod
    def from_xyxy(cls: int, x1: float, y1: float, x2: float, y2: float, img_w: int, img_h: int,
                  score: float = 1.0, source: str = "manual") -> "Box":
        x1, x2 = max(0.0, min(x1, x2)), min(float(img_w), max(x1, x2))
        y1, y2 = max(0.0, min(y1, y2)), min(float(img_h), max(y1, y2))
        return Box(cls, (x1 + x2) / 2 / img_w, (y1 + y2) / 2 / img_h,
                   (x2 - x1) / img_w, (y2 - y1) / img_h, score, source)


def read_yolo_labels(path: Path) -> list[Box]:
    if not path.exists():
        return []
    boxes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 5:
            boxes.append(Box(int(parts[0]), *map(float, parts[1:5])))
    return boxes


def write_yolo_labels(path: Path, boxes: list[Box]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(b.to_yolo_line() + "\n" for b in boxes), encoding="utf-8")


def pick_device(pref: str = "auto") -> str:
    """auto → mps (Mac) | cuda:0 | cpu."""
    if pref != "auto":
        return pref
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda:0"
    except ImportError:
        pass
    return "cpu"
