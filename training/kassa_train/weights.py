"""Поиск весов моделей и сведения о запусках Ultralytics.

    from kassa_train.weights import resolve_weight
    YOLO(resolve_weight("yolo26l.pt"))          # models/pretrained/yolo26l.pt, если скачан
    YOLO(resolve_weight("runs/detect/full/weights/best.pt"))

Правила resolve_weight(name):
  1. существующий путь (абсолютный, от текущей папки или от корня репозитория) → он сам;
  2. голое имя (yolo26l.pt) и файл есть в models/pretrained/ → models/pretrained/<name>
     (туда кладёт веса training/download_models.sh);
  3. голое имя .pt, которого нет нигде → путь models/pretrained/<name>: Ultralytics сам
     скачает официальный файл именно туда, а не в текущую папку (по умолчанию он качает
     в cwd, что засоряет репозиторий). Отключается параметром download_to_pretrained=False —
     тогда возвращается голое имя, как есть;
  4. путь с папками, которого нет, → FileNotFoundError с понятным сообщением.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import common

# Относительная стоимость эпохи по размеру модели (YOLO26 n/s/m/l/x), l = 1.0.
SIZE_FACTORS = {"n": 0.15, "s": 0.35, "m": 0.8, "l": 1.0, "x": 1.8}

_SCALE_RE = re.compile(r"yolo(?:v?\d+)([nsmlx])(?:[-_.]|$)", re.IGNORECASE)


def pretrained_dir() -> Path:
    """models/pretrained — сюда download_models.sh кладёт официальные веса."""
    return common.repo_path("models") / "pretrained"


def resolve_weight(name: str | Path, download_to_pretrained: bool = True) -> str:
    """Имя или путь весов → строка, которую можно передать в YOLO(...). См. описание модуля."""
    raw = str(name).strip()
    if not raw:
        raise ValueError("пустое имя весов")
    p = Path(raw).expanduser()

    if p.exists():
        return str(p.resolve())
    if not p.is_absolute():
        from_repo = common.REPO_ROOT / p
        if from_repo.exists():
            return str(from_repo.resolve())

    is_bare = not p.is_absolute() and len(p.parts) == 1
    if is_bare:
        local = pretrained_dir() / p.name
        if local.exists():
            return str(local.resolve())
        if download_to_pretrained and p.suffix == ".pt":
            local.parent.mkdir(parents=True, exist_ok=True)
            return str(local)  # Ultralytics скачает официальный файл по этому пути
        return raw  # .yaml (обучение с нуля) или download_to_pretrained=False

    tried = list(dict.fromkeys(str(c) for c in (p.resolve(), (common.REPO_ROOT / p).resolve())))
    raise FileNotFoundError(
        f"Файл весов не найден: {raw}\n"
        f"  искали: {'; '.join(tried)}\n"
        "  Проверьте путь (обучение завершено? см. runs/<задача>/<имя>/weights/)."
    )


def is_local_file(resolved: str) -> bool:
    return Path(resolved).is_file()


def model_scale(name: str | Path | None) -> str | None:
    """'yolo26l.pt' → 'l'; 'yolo26s-cls.pt' → 's'; не распознано → None."""
    if not name:
        return None
    m = _SCALE_RE.search(Path(str(name)).name)
    return m.group(1).lower() if m else None


def size_factor(scale: str | None, default: float = 1.0) -> float:
    return SIZE_FACTORS.get((scale or "").lower(), default)


def run_dir_of(weights: str | Path) -> Path | None:
    """runs/<task>/<name>/weights/best.pt → runs/<task>/<name> (если рядом есть args.yaml)."""
    p = Path(weights)
    if p.parent.name == "weights" and (p.parents[1] / "args.yaml").exists():
        return p.parents[1]
    return None


def read_run_args(weights: str | Path) -> dict:
    """Параметры обучения (args.yaml) запуска, которому принадлежат веса; {} если это не запуск."""
    run = run_dir_of(weights)
    if run is None:
        return {}
    return common.load_yaml(run / "args.yaml") or {}


def scale_from_weights(weights: str | Path, _depth: int = 0) -> str | None:
    """Размер модели по имени файла, а для best.pt/last.pt — по args.yaml запуска (цепочка дообучений)."""
    s = model_scale(weights)
    if s or _depth > 5:
        return s
    parent_model = read_run_args(weights).get("model")
    if not parent_model:
        return None
    s = model_scale(parent_model)
    if s:
        return s
    try:
        parent = resolve_weight(parent_model, download_to_pretrained=False)
    except FileNotFoundError:
        return None
    return scale_from_weights(parent, _depth + 1)
