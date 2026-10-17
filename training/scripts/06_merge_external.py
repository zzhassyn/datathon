"""Шаг 06: конвертация внешних датасетов в наш формат и наши классы.

Что делает
    Читает training/configs/external_datasets.yaml (шаблон — external_datasets.example.yaml)
    и для каждого включённого датасета (enabled: true) пишет
        data/external_converted/<name>/images/ext_<name>_*.jpg
        data/external_converted/<name>/labels/ext_<name>_*.txt   — YOLO, id классов из classes.yaml
        data/external_converted/<name>/stats.json                — кадры, рамки по классам, лицензия
        data/external_converted/<name>/sources.csv               — какой исходный файл чем стал
    Классы датасета переводятся в наши по полю `classes`; всё, что не указано, отбрасывается.
    Кадры без наших классов не берутся (кроме keep_empty: true — до 10 % пустых кадров).
    Если кадров больше max_images, выборка детерминированная и в первую очередь берёт
    кадры с редкими классами (баланс по числу кадров на класс).

Форматы
    yolo: data.yaml (names) + images/ и labels/ в любой вложенности
          (images/train/…, train/images/… (Roboflow), images/… без сплитов);
          полигоны сегментации переводятся в рамки, у поз берутся только рамки.
    coco: JSON с images/annotations/categories (annotations.json, annotations/*.json,
          _annotations.coco.json в папках сплитов) + картинки рядом или в images/.

Поля датасета в external_datasets.yaml
    name        латиница/цифры/_/-, станет префиксом файлов ext_<name>_
    enabled     true | false (по умолчанию true)
    format      yolo | coco
    path        папка датасета: относительно data_dir (или data_dir/external), можно абсолютный
    license     фактическая лицензия (пустая — громкое предупреждение)
    source_url  откуда скачан
    max_images  потолок кадров (по умолчанию 1000; 0 — без ограничения)
    classes     {класс датасета (имя или id): наш класс | null}
    необязательные: data_yaml (yolo), annotations (coco: путь или список), images_dir (coco),
                    keep_empty (true — взять до 10 % кадров без наших рамок как фон)

Запуск
    python training/scripts/06_merge_external.py                 # все включённые датасеты
    python training/scripts/06_merge_external.py --only a,b      # только перечисленные
    python training/scripts/06_merge_external.py --force         # пересобрать даже без изменений

Повторный запуск пропускает датасет, если не изменились его описание, классы и исходные файлы.
Дальше: python training/scripts/07_build_dataset.py --source reviewed --external a,b
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_VERSION = 1
CONFIG_PATH = common.CONFIG_DIR / "external_datasets.yaml"
EXAMPLE_PATH = common.CONFIG_DIR / "external_datasets.example.yaml"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
JPEG_EXTS = {".jpg", ".jpeg"}
SKIP_DIRS = {"__MACOSX"}
DEFAULT_MAX_IMAGES = 1000
EMPTY_SHARE = 0.10          # доля кадров-фона при keep_empty: true
MIN_SIDE = 1e-4             # рамки уже этой доли кадра отбрасываются
JPEG_QUALITY = 95
MAX_STEM_LEN = 100
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
NONCOMMERCIAL_RE = re.compile(r"(\bNC\b|non[-\s]?commercial|research[-\s]+only|personal[-\s]+use)", re.I)
EMPTY_LICENSES = {"", "unknown", "?", "todo", "tbd", "none", "n/a", "-"}
DROP_VALUES = {"", "drop", "none", "null", "-", "~"}


class DatasetError(Exception):
    """Ошибка описания или содержимого одного датасета (сообщение — для пользователя)."""


@dataclass
class Candidate:
    """Кадр внешнего датасета, кандидат в выборку."""
    src: Path
    rel: str                                   # путь относительно корня датасета (posix) — для имени и хэша
    boxes: list = field(default_factory=list)  # list[common.Box] с НАШИМИ id
    has_label: bool = False                    # разметка для кадра есть (пусть и без наших классов)


@dataclass
class ConvertReport:
    name: str
    status: str                 # converted | skipped | disabled | error
    images: int = 0
    boxes: int = 0
    license: str = ""
    message: str = ""


# ----------------------------------------------------------------------------- общие помощники

def warn(msg: str) -> None:
    print(f"ВНИМАНИЕ: {msg}", file=sys.stderr)


def loud(msg: str) -> None:
    bar = "!" * 78
    print(f"\n{bar}\n!!! {msg}\n{bar}\n", file=sys.stderr)


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def walk_images(root: Path) -> list[Path]:
    """Все картинки под root (без скрытых папок и __MACOSX), в стабильном порядке."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if not fn.startswith(".") and Path(fn).suffix.lower() in IMAGE_EXTS:
                found.append(Path(dirpath) / fn)
    return found


def tree_fingerprint(root: Path) -> dict:
    """Дешёвый отпечаток исходной папки: число файлов, суммарный размер, последнее изменение."""
    n, size, mtime = 0, 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            try:
                st = os.stat(os.path.join(dirpath, fn))
            except OSError:
                continue
            n += 1
            size += st.st_size
            mtime = max(mtime, st.st_mtime_ns)
    return {"files": n, "bytes": size, "mtime_ns": mtime}


def jsonable(obj):
    """Ключи словарей → строки (в YAML бывают и числа, и строки — json sort_keys на них падает)."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj if isinstance(obj, (str, int, float, bool)) or obj is None else str(obj)


def is_inside(path: Path, root: Path) -> bool:
    key, root_key = same_path_key(path), same_path_key(root)
    return key == root_key or key.startswith(root_key.rstrip(os.sep) + os.sep)


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(common.REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def rel_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return f"_outside/{stable_hash(str(path.resolve()))[:8]}/{path.name}"


def same_path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def box_from_xyxy_norm(cls: int, x1: float, y1: float, x2: float, y2: float) -> "common.Box | None":
    """Рамка в долях кадра → Box с обрезкой по краям; None для вырожденных и нечисловых."""
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
        return None
    box = common.Box.from_xyxy(cls, x1, y1, x2, y2, 1, 1, source="external")
    if box.w < MIN_SIDE or box.h < MIN_SIDE:
        return None
    return box


# ----------------------------------------------------------------------------- соответствие классов

class ClassMapper:
    """Переводит класс датасета (имя или id) в наш id; None — класс отбрасывается."""

    def __init__(self, raw: dict | None, our_names: list[str]):
        if not raw:
            raise DatasetError("не задано поле classes (соответствие «класс датасета → наш класс»)")
        if not isinstance(raw, dict):
            raise DatasetError("classes должно быть словарём «класс датасета: наш класс»")
        self.our_names = our_names
        self.by_key: dict[str, int | None] = {}
        self.by_casefold: dict[str, int | None] = {}
        bad = []
        for src, dst in raw.items():
            key = str(src).strip()
            if dst is None or str(dst).strip().lower() in DROP_VALUES:
                target = None
            elif str(dst).strip() in our_names:
                target = our_names.index(str(dst).strip())
            else:
                bad.append(f"{key!r} → {dst!r}")
                continue
            self.by_key[key] = target
            self.by_casefold[key.casefold()] = target
        if bad:
            raise DatasetError("неизвестные наши классы в classes: " + ", ".join(bad)
                               + f". Допустимые: {', '.join(our_names)}")
        self.used_keys: set[str] = set()

    def lookup(self, src_name: str | None, src_id: int | None) -> int | None:
        for key in ([str(src_name)] if src_name is not None else []) + \
                   ([str(src_id)] if src_id is not None else []):
            if key in self.by_key:
                self.used_keys.add(key)
                return self.by_key[key]
            cf = key.casefold()
            if cf in self.by_casefold:
                self.used_keys.update(k for k in self.by_key if k.casefold() == cf)
                return self.by_casefold[cf]
        return None

    def unused_keys(self) -> list[str]:
        return sorted(k for k in self.by_key if k not in self.used_keys)


# ----------------------------------------------------------------------------- YOLO

def find_yolo_yaml(root: Path, cfg: dict) -> Path | None:
    if cfg.get("data_yaml"):
        p = Path(cfg["data_yaml"])
        p = p if p.is_absolute() else root / p
        if not p.is_file():
            raise DatasetError(f"data_yaml не найден: {p}")
        return p
    for fn in ("data.yaml", "data.yml", "dataset.yaml", "dataset.yml"):
        if (root / fn).is_file():
            return root / fn
    for p in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml"))):
        try:
            doc = common.load_yaml(p)
        except Exception:
            continue
        if isinstance(doc, dict) and "names" in doc:
            return p
    return None


def yolo_names(doc: dict | None, root: Path) -> dict[int, str]:
    names = (doc or {}).get("names")
    if isinstance(names, list):
        return {i: str(n) for i, n in enumerate(names)}
    if isinstance(names, dict):
        return {int(k): str(v) for k, v in names.items()}
    for fn in ("classes.txt", "obj.names", "classes.names", "labels.txt"):
        p = root / fn
        if p.is_file():
            lines = [ln.strip() for ln in p.read_text(encoding="utf-8-sig").splitlines()]
            return {i: n for i, n in enumerate(ln for ln in lines if ln)}
    return {}


def yolo_extra_sources(yaml_path: Path, doc: dict, root: Path) -> tuple[list[Path], list[Path]]:
    """Папки и списки картинок из train/val/test в data.yaml, лежащие ВНЕ root."""
    dirs, files = [], []
    base = Path(str(doc.get("path") or "."))
    if not base.is_absolute():
        base = yaml_path.parent / base
    for key in ("train", "val", "test"):
        entries = doc.get(key)
        if entries is None:
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            entry_path = Path(str(entry))
            candidates = [entry_path] if entry_path.is_absolute() else [base / entry_path, yaml_path.parent / entry_path]
            for p in candidates:
                if p.is_dir():
                    if not is_inside(p, root):
                        dirs.append(p)
                    break
                if p.is_file() and p.suffix.lower() == ".txt":
                    for line in p.read_text(encoding="utf-8-sig").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        img = Path(line)
                        if not img.is_absolute():
                            img = (p.parent / line) if line.startswith("./") else (base / line)
                        if img.is_file() and is_image(img) and not is_inside(img, root):
                            files.append(img)
                    break
    return dirs, files


def yolo_label_path(img: Path) -> Path | None:
    """Правило Ultralytics: …/images/… → …/labels/….txt; запасной вариант — .txt рядом с картинкой."""
    parts = list(img.parts)
    candidates = []
    idx = [i for i, p in enumerate(parts[:-1]) if p.lower() == "images"]
    if idx:
        i = idx[-1]
        candidates.append(Path(*parts[:i], "labels", *parts[i + 1:]).with_suffix(".txt"))
    candidates.append(img.with_suffix(".txt"))
    for c in candidates:
        if c.is_file():
            return c
    return None


def parse_yolo_line(parts: list[str], pose: bool) -> tuple[int, float, float, float, float] | None:
    """Строка YOLO (рамка, полигон или поза) → (класс, x1, y1, x2, y2) в долях кадра."""
    try:
        vals = [float(x) for x in parts]
    except ValueError:
        return None
    cls_f, coords = vals[0], vals[1:]
    if not math.isfinite(cls_f) or not cls_f.is_integer() or cls_f < 0:
        return None
    if pose or len(coords) in (4, 5):
        if len(coords) < 4:
            return None
        cx, cy, w, h = coords[:4]
        return int(cls_f), cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    if len(coords) >= 6 and len(coords) % 2 == 0:
        xs, ys = coords[0::2], coords[1::2]
        return int(cls_f), min(xs), min(ys), max(xs), max(ys)
    return None


def collect_yolo(root: Path, cfg: dict, mapper: ClassMapper, stats: Counter,
                 unmapped: Counter) -> list[Candidate]:
    yaml_path = find_yolo_yaml(root, cfg)
    doc = common.load_yaml(yaml_path) if yaml_path else {}
    doc = doc if isinstance(doc, dict) else {}
    names = yolo_names(doc, root)
    if not names:
        warn(f"{root}: нет data.yaml/classes.txt с names — классы сопоставляются только по id")
    pose = doc.get("kpt_shape") is not None

    images = walk_images(root)
    extra_dirs, extra_files = (yolo_extra_sources(yaml_path, doc, root) if yaml_path else ([], []))
    for d in extra_dirs:
        images.extend(walk_images(d))
    images.extend(extra_files)

    seen: set[str] = set()
    candidates = []
    for img in images:
        key = same_path_key(img)
        if key in seen:
            continue
        seen.add(key)
        stats["source_images"] += 1
        cand = Candidate(src=img, rel=rel_posix(img, root))
        label = yolo_label_path(img)
        if label is None:
            stats["images_without_label_file"] += 1
            candidates.append(cand)
            continue
        cand.has_label = True
        for line in label.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            parts = line.split()
            if not parts:
                continue
            parsed = parse_yolo_line(parts, pose)
            if parsed is None:
                stats["invalid_source_boxes"] += 1
                continue
            src_id, x1, y1, x2, y2 = parsed
            src_name = names.get(src_id)
            our = mapper.lookup(src_name, src_id)
            if our is None:
                unmapped[src_name if src_name is not None else f"id {src_id}"] += 1
                continue
            box = box_from_xyxy_norm(our, x1, y1, x2, y2)
            if box is None:
                stats["degenerate_boxes"] += 1
                continue
            cand.boxes.append(box)
        candidates.append(cand)
    return candidates


# ----------------------------------------------------------------------------- COCO

def looks_like_coco(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict) and isinstance(doc.get("images"), list) and isinstance(doc.get("annotations"), list):
        return doc
    return None


def find_coco_jsons(root: Path, cfg: dict) -> list[Path]:
    if cfg.get("annotations"):
        raw = cfg["annotations"]
        paths = [Path(p) for p in (raw if isinstance(raw, list) else [raw])]
        paths = [p if p.is_absolute() else root / p for p in paths]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise DatasetError("annotations не найдены: " + ", ".join(missing))
        return paths
    if (root / "annotations.json").is_file():
        return [root / "annotations.json"]
    ann_dir = root / "annotations"
    if ann_dir.is_dir():
        found = sorted(p for p in ann_dir.glob("*.json"))
        if found:
            return found
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        found.extend(Path(dirpath) / fn for fn in sorted(filenames) if fn.lower().endswith(".json"))
    return found


class ImageResolver:
    """Ищет файл картинки COCO: images_dir, папка JSON, root/images, root, затем по имени файла."""

    def __init__(self, root: Path, images_dir: Path | None):
        self.root = root
        self.images_dir = images_dir
        self._by_name: dict[str, list[Path]] | None = None

    def resolve(self, file_name: str, json_path: Path) -> Path | None:
        fn = Path(str(file_name).replace("\\", "/"))
        if fn.is_absolute() and fn.is_file():
            return fn
        bases = [b for b in (self.images_dir, json_path.parent, json_path.parent / "images",
                             self.root / "images", self.root) if b is not None]
        for base in bases:
            for cand in (base / fn, base / fn.name):
                if cand.is_file():
                    return cand
        if self._by_name is None:
            self._by_name = defaultdict(list)
            for p in walk_images(self.root):
                self._by_name[p.name.casefold()].append(p)
        hits = self._by_name.get(fn.name.casefold(), [])
        return hits[0] if len(hits) == 1 else None


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def collect_coco(root: Path, cfg: dict, mapper: ClassMapper, stats: Counter,
                 unmapped: Counter) -> list[Candidate]:
    json_paths = find_coco_jsons(root, cfg)
    images_dir = None
    if cfg.get("images_dir"):
        images_dir = Path(cfg["images_dir"])
        images_dir = images_dir if images_dir.is_absolute() else root / images_dir
    resolver = ImageResolver(root, images_dir)

    by_path: dict[str, Candidate] = {}
    used_jsons = 0
    for jp in json_paths:
        doc = looks_like_coco(jp)
        if doc is None:
            continue
        used_jsons += 1
        cats = {c.get("id"): str(c.get("name")) for c in doc.get("categories", []) if isinstance(c, dict)}
        anns_by_image: dict = defaultdict(list)
        for a in doc["annotations"]:
            if isinstance(a, dict):
                anns_by_image[a.get("image_id")].append(a)
        for im in doc["images"]:
            if not isinstance(im, dict) or "file_name" not in im:
                continue
            path = resolver.resolve(im["file_name"], jp)
            if path is None:
                stats["missing_images"] += 1
                continue
            key = same_path_key(path)
            cand = by_path.get(key)
            if cand is None:
                cand = Candidate(src=path, rel=rel_posix(path, root), has_label=True)
                by_path[key] = cand
                stats["source_images"] += 1
            w, h = im.get("width") or 0, im.get("height") or 0
            if not w or not h:
                size = image_size(path)
                if size is None:
                    stats["unreadable_images"] += 1
                    continue
                w, h = size
            existing = {b.to_yolo_line() for b in cand.boxes}
            for a in anns_by_image.get(im.get("id"), []):
                if a.get("iscrowd"):
                    stats["crowd_boxes_skipped"] += 1
                    continue
                bbox = a.get("bbox")
                cat_id = a.get("category_id")
                src_name = cats.get(cat_id)
                our = mapper.lookup(src_name, cat_id)
                if our is None:
                    unmapped[src_name if src_name is not None else f"id {cat_id}"] += 1
                    continue
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    stats["invalid_source_boxes"] += 1
                    continue
                try:
                    x, y, bw, bh = (float(v) for v in bbox)
                except (TypeError, ValueError):
                    stats["invalid_source_boxes"] += 1
                    continue
                box = box_from_xyxy_norm(our, x / w, y / h, (x + bw) / w, (y + bh) / h)
                if box is None:
                    stats["degenerate_boxes"] += 1
                    continue
                line = box.to_yolo_line()
                if line not in existing:      # один кадр в двух JSON (например, instances + keypoints)
                    existing.add(line)
                    cand.boxes.append(box)
    if used_jsons == 0:
        raise DatasetError(f"не найден JSON в формате COCO (images/annotations) в {root}; "
                           "укажите путь в поле annotations")
    stats["coco_json_files"] = used_jsons
    return list(by_path.values())


# ----------------------------------------------------------------------------- выборка

def balanced_sample(items: list[tuple[str, set[int]]], cap: int, seed: str) -> set[str]:
    """Детерминированная выборка не более cap ключей с приоритетом редких классов.

    На каждом шаге берётся класс с наименьшим числом уже выбранных кадров (при равенстве —
    класс, у которого меньше кандидатов), и из его очереди (порядок — по хэшу) берётся
    следующий ещё не выбранный кадр. Так редкие классы попадают в выборку целиком,
    а частые добирают остаток.
    """
    keys = [k for k, _ in items]
    if cap <= 0 or len(items) <= cap:
        return set(keys)
    classes_of = dict(items)
    queues: dict[int, list[str]] = defaultdict(list)
    for key in sorted(keys, key=lambda k: stable_hash(f"{seed}:{k}")):
        for c in classes_of[key]:
            queues[c].append(key)
    pos = {c: 0 for c in queues}
    taken = {c: 0 for c in queues}
    selected: set[str] = set()
    while len(selected) < cap:
        best = None
        for c in sorted(queues):
            q = queues[c]
            while pos[c] < len(q) and q[pos[c]] in selected:
                pos[c] += 1
            if pos[c] >= len(q):
                continue
            if best is None or (taken[c], len(q)) < (taken[best], len(queues[best])):
                best = c
        if best is None:
            break
        key = queues[best][pos[best]]
        pos[best] += 1
        selected.add(key)
        for c in classes_of[key]:
            taken[c] += 1
    return selected


def select_candidates(cands: list[Candidate], cap: int, keep_empty: bool, seed: str) -> list[Candidate]:
    """Кадры с нашими рамками (сбалансированно) + при keep_empty до EMPTY_SHARE кадров-фона."""
    positives = [c for c in cands if c.boxes]
    empties = [c for c in cands if c.has_label and not c.boxes] if keep_empty else []
    pos_cap = cap - min(len(empties), int(cap * EMPTY_SHARE)) if cap > 0 else 0   # 0 — без ограничения
    pos_keys = balanced_sample([(c.rel, {b.cls for b in c.boxes}) for c in positives], pos_cap, seed)
    chosen = [c for c in positives if c.rel in pos_keys]
    n_empty = min(len(empties), int(len(chosen) * EMPTY_SHARE / (1 - EMPTY_SHARE)))
    if cap > 0:
        n_empty = min(n_empty, cap - len(chosen))
    empties.sort(key=lambda c: stable_hash(f"{seed}:empty:{c.rel}"))
    chosen.extend(empties[:max(0, n_empty)])
    chosen.sort(key=lambda c: c.rel)
    return chosen


# ----------------------------------------------------------------------------- запись

def safe_stem(rel: str) -> str:
    parts = [p for p in Path(rel).with_suffix("").parts if p.lower() != "images"]
    text = "__".join(parts) or "img"
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._") or "img"
    return text


def assign_names(chosen: list[Candidate], ds_name: str) -> list[tuple[Candidate, str]]:
    used: set[str] = set()
    named = []
    for cand in chosen:
        base = safe_stem(cand.rel)
        stem = f"ext_{ds_name}_{base[:MAX_STEM_LEN]}"
        if len(base) > MAX_STEM_LEN or stem.casefold() in used:
            stem = f"{stem}_{stable_hash(cand.rel)[:8]}"
        n = 2
        while stem.casefold() in used:      # почти невозможно, но имена обязаны быть уникальны
            stem = f"{stem}_{n}"
            n += 1
        used.add(stem.casefold())
        named.append((cand, stem))
    return named


def write_image(src: Path, dst: Path, counter: Counter) -> None:
    if src.suffix.lower() in JPEG_EXTS:
        try:
            os.link(src, dst)
            counter["hardlink"] += 1
            return
        except OSError:
            shutil.copy2(src, dst)
            counter["copy"] += 1
            return
    try:
        from PIL import Image
    except ImportError as e:
        raise DatasetError("для перевода PNG/BMP/WebP в JPEG нужен Pillow: pip install pillow") from e
    with Image.open(src) as im:
        im.convert("RGB").save(dst, "JPEG", quality=JPEG_QUALITY)
    counter["converted"] += 1


def resolve_dataset_path(raw: str) -> Path:
    p = Path(str(raw)).expanduser()
    if p.is_absolute():
        return p
    candidates = [common.data_dir() / p, common.data_path("external_raw") / p]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def license_problem(lic: str) -> str | None:
    if lic.strip().lower() in EMPTY_LICENSES:
        return "лицензия не указана"
    if NONCOMMERCIAL_RE.search(lic):
        return f"лицензия «{lic}» похожа на некоммерческую"
    return None


def convert_dataset(cfg: dict, our_names: list[str], out_root: Path, force: bool,
                    config_path: Path) -> ConvertReport:
    """Конвертирует один датасет; DatasetError — понятная пользователю ошибка описания/данных."""
    name = str(cfg.get("name") or "").strip()
    lic = str(cfg.get("license") or "").strip()
    report = ConvertReport(name=name, status="error", license=lic)
    if not NAME_RE.match(name):
        raise DatasetError(f"имя датасета {name!r}: нужны латиница, цифры, _ и -")
    fmt = str(cfg.get("format") or "").strip().lower()
    if fmt not in ("yolo", "coco"):
        raise DatasetError(f"format должен быть yolo или coco, а не {cfg.get('format')!r}")
    if not cfg.get("path"):
        raise DatasetError("не задано поле path")
    root = resolve_dataset_path(cfg["path"])
    if not root.is_dir():
        raise DatasetError(f"папка датасета не найдена: {root} "
                           f"(path считается от {common.data_dir()} или {common.data_path('external_raw')})")
    raw_cap = cfg.get("max_images", DEFAULT_MAX_IMAGES)
    cap = DEFAULT_MAX_IMAGES if raw_cap is None else int(raw_cap)
    if cap < 0:
        raise DatasetError("max_images не может быть отрицательным (0 — без ограничения)")
    keep_empty = bool(cfg.get("keep_empty", False))

    problem = license_problem(lic)
    if problem:
        loud(f"{name}: {problem}. Решение получает банк (FAQ п. 34): нужна лицензия, разрешающая "
             f"коммерческое использование. Заполните license в {display_path(config_path)}.")

    out_dir = out_root / name
    signature = stable_hash(json.dumps(jsonable({
        "version": SCRIPT_VERSION,
        "config": cfg,
        "classes": our_names,
        "root": str(root.resolve()),
        "tree": tree_fingerprint(root),
    }), sort_keys=True, ensure_ascii=False))
    stats_path = out_dir / "stats.json"
    if not force and stats_path.is_file():
        try:
            old = json.loads(stats_path.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
        if old.get("signature") == signature and (out_dir / "labels").is_dir():
            report.status, report.images, report.boxes = "skipped", old.get("images", 0), old.get("boxes_total", 0)
            report.message = "без изменений"
            print(f"[{name}] уже сконвертирован, изменений нет — пропускаю (--force для пересборки)")
            return report

    print(f"[{name}] {fmt.upper()} из {root}")
    mapper = ClassMapper(cfg.get("classes"), our_names)
    stats: Counter = Counter()
    unmapped: Counter = Counter()
    collect = collect_yolo if fmt == "yolo" else collect_coco
    cands = collect(root, cfg, mapper, stats, unmapped)
    if stats["source_images"] == 0:
        raise DatasetError(f"в {root} не найдено ни одной картинки")
    for key in mapper.unused_keys():
        warn(f"{name}: класса {key!r} из classes нет в разметке датасета (опечатка?)")
    positives = sum(1 for c in cands if c.boxes)
    if positives == 0:
        raise DatasetError("ни одной рамки наших классов: проверьте classes и names в data.yaml/categories")

    chosen = select_candidates(cands, cap, keep_empty, seed=name)
    named = assign_names(chosen, name)

    partial = out_root / f".{name}.partial"
    if partial.exists():
        shutil.rmtree(partial)
    (partial / "images").mkdir(parents=True)
    (partial / "labels").mkdir(parents=True)
    link_counter: Counter = Counter()
    boxes_per_class: Counter = Counter()
    images_per_class: Counter = Counter()
    rows = []
    for cand, stem in named:
        write_image(cand.src, partial / "images" / f"{stem}.jpg", link_counter)
        common.write_yolo_labels(partial / "labels" / f"{stem}.txt", cand.boxes)
        for b in cand.boxes:
            boxes_per_class[our_names[b.cls]] += 1
        for c in {b.cls for b in cand.boxes}:
            images_per_class[our_names[c]] += 1
        rows.append((f"{stem}.jpg", cand.rel))

    with open(partial / "sources.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "source"])
        w.writerows(rows)

    boxes_total = sum(boxes_per_class.values())
    doc = {
        "name": name,
        "format": fmt,
        "source_path": str(root.resolve()),
        "license": lic,
        "license_warning": problem,
        "source_url": str(cfg.get("source_url") or ""),
        "max_images": cap,
        "keep_empty": keep_empty,
        "candidates_with_boxes": positives,
        "images": len(named),
        "empty_images": sum(1 for c, _ in named if not c.boxes),
        "boxes_total": boxes_total,
        "boxes_per_class": {n: boxes_per_class[n] for n in our_names if boxes_per_class[n]},
        "images_per_class": {n: images_per_class[n] for n in our_names if images_per_class[n]},
        "unmapped_source_boxes": dict(unmapped.most_common()),
        "source_stats": dict(sorted(stats.items())),
        "files": dict(link_counter),
        "class_names": our_names,
        "signature": signature,
        "script_version": SCRIPT_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (partial / "stats.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    partial.rename(out_dir)

    print(f"[{name}] кадров {len(named)} из {positives} с нашими классами "
          f"(всего в датасете {stats['source_images']}), рамок {boxes_total}")
    for n in our_names:
        if boxes_per_class[n]:
            print(f"    {n:<18} рамок {boxes_per_class[n]:>6}  кадров {images_per_class[n]:>6}")
    if unmapped:
        top = ", ".join(f"{k} ({v})" for k, v in unmapped.most_common(8))
        print(f"    отброшено (нет в classes): {top}")
    if stats["missing_images"]:
        warn(f"{name}: в JSON {stats['missing_images']} картинок, которых нет на диске")
    report.status, report.images, report.boxes = "converted", len(named), boxes_total
    return report


# ----------------------------------------------------------------------------- main

def explain_missing_config(config_path: Path) -> None:
    print(f"Файл {display_path(config_path)} не найден — внешние датасеты не подключены, делать нечего.")
    print("Чтобы подключить:")
    print(f"  1. скопируйте шаблон: cp {display_path(EXAMPLE_PATH)} {display_path(config_path)}")
    print(f"  2. положите датасет в {common.data_path('external_raw')}/<имя>/ и опишите его "
          "(format, path, license, source_url, max_images, classes, enabled: true)")
    print("  3. запустите этот скрипт ещё раз")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Конвертация внешних датасетов (YOLO/COCO) в наши классы: "
                    "data/external_converted/<name>/{images,labels,stats.json}.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Описание датасетов: training/configs/external_datasets.yaml "
               "(шаблон — external_datasets.example.yaml).")
    p.add_argument("--config", type=Path, default=CONFIG_PATH,
                   help="YAML со списком датасетов (по умолчанию configs/external_datasets.yaml)")
    p.add_argument("--only", default="", help="через запятую: обработать только эти датасеты")
    p.add_argument("--force", action="store_true", help="пересобрать, даже если ничего не изменилось")
    return p.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parse_args()
    config_path = args.config.resolve()
    if not config_path.is_file():
        explain_missing_config(config_path)
        return 0

    try:
        doc = common.load_yaml(config_path) or {}
    except Exception as e:
        print(f"ОШИБКА: не удалось прочитать {config_path}: {e}", file=sys.stderr)
        return 1
    datasets = doc.get("datasets") if isinstance(doc, dict) else None
    if not isinstance(datasets, list):
        print(f"ОШИБКА: в {config_path} нет списка datasets", file=sys.stderr)
        return 1

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    names_in_cfg = [str(d.get("name", "")) if isinstance(d, dict) else "" for d in datasets]
    unknown = sorted(only - set(names_in_cfg))
    if unknown:
        print(f"ОШИБКА: в {config_path.name} нет датасетов: {', '.join(unknown)}", file=sys.stderr)
        return 1
    dupes = sorted(n for n, k in Counter(names_in_cfg).items() if k > 1 and n)
    if dupes:
        print(f"ОШИБКА: повторяются имена датасетов: {', '.join(dupes)}", file=sys.stderr)
        return 1

    our_names = common.class_names()
    out_root = common.data_path("external_converted")
    out_root.mkdir(parents=True, exist_ok=True)

    reports: list[ConvertReport] = []
    for cfg in datasets:
        if not isinstance(cfg, dict):
            reports.append(ConvertReport(name="?", status="error", message="запись датасета — не словарь"))
            continue
        name = str(cfg.get("name", "?"))
        if only and name not in only:
            continue
        if not cfg.get("enabled", True):
            print(f"[{name}] выключен (enabled: false) — пропускаю")
            reports.append(ConvertReport(name=name, status="disabled", license=str(cfg.get("license") or "")))
            continue
        try:
            reports.append(convert_dataset(cfg, our_names, out_root, args.force, config_path))
        except DatasetError as e:
            print(f"ОШИБКА [{name}]: {e}", file=sys.stderr)
            reports.append(ConvertReport(name=name, status="error", message=str(e)))
        except Exception as e:  # неожиданная ошибка — показать трассировку и продолжить с другими
            traceback.print_exc()
            reports.append(ConvertReport(name=name, status="error", message=f"{type(e).__name__}: {e}"))

    status_ru = {"converted": "готов", "skipped": "без изменений", "disabled": "выключен", "error": "ОШИБКА"}
    print("\nИтог 06_merge_external:")
    if not reports:
        print("  нет датасетов для обработки")
    for r in reports:
        lic = r.license or "ЛИЦЕНЗИЯ НЕ УКАЗАНА"
        line = f"  {r.name:<24} {status_ru.get(r.status, r.status):<14}"
        if r.status in ("converted", "skipped"):
            line += f" кадров {r.images:>6}, рамок {r.boxes:>7}, лицензия: {lic}"
        elif r.message:
            line += f" {r.message}"
        print(line)
    print(f"  результат: {out_root}")
    no_license = [r.name for r in reports if r.status in ("converted", "skipped") and license_problem(r.license)]
    if no_license:
        loud("проверьте лицензии: " + ", ".join(no_license))
    return 1 if any(r.status == "error" for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
