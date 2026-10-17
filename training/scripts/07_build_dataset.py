"""Шаг 07: сборка датасета для Ultralytics из кадров, разметки и внешних датасетов.

Что делает
    Собирает data/yolo/<name>/
        images/{train,val}/*.jpg     — жёсткие ссылки на кадры (без лишнего места на диске;
                                       если ссылка невозможна, например другой том, — копия)
        labels/{train,val}/*.txt     — проверенная разметка YOLO
        dataset.yaml                 — path (абсолютный), train/val (относительные), names из classes.yaml
        stats.json                   — кадры и рамки по классам в каждом сплите, пропуски, ошибки разметки
    Деление по сценам: кадры сцен из --val-scenes (по умолчанию split.val_scenes из pipeline.yaml,
    сейчас L4) идут в val, остальные — в train. Внешние кадры — только в train.

Источники разметки (--source)
    pseudo    data/prelabels/<video>/<stem>.txt  (автоматическая разметка 03_prelabel.py)
    reviewed  data/labels/<video>/<stem>.txt     (проверено человеком, 05_labelstudio_import.py);
              берутся только кадры, у которых есть такой файл (пустой файл = кадр без объектов)
    external  только data/external_converted/* (06_merge_external.py) — для предобучения.
              В train — внешние кадры; в val — наши кадры сцен --val-scenes (reviewed, если
              проверенная разметка есть, иначе pseudo); если наших кадров нет — val = train
              (с предупреждением: метрики тогда не показательны).

Запуск
    python training/scripts/07_build_dataset.py --source pseudo
    python training/scripts/07_build_dataset.py --source reviewed --loso
    python training/scripts/07_build_dataset.py --source reviewed --external banknotes,bags --external-cap 500
    python training/scripts/07_build_dataset.py --source external --name ext_pretrain

    --name NAME          имя датасета (по умолчанию = --source): data/yolo/NAME
    --val-scenes L4,L5   сцены для val (через запятую)
    --loso               дополнительно data/yolo/NAME_loso_<сцена> для каждой сцены (она — val)
    --external a,b       добавить внешние датасеты в train (для --source external по умолчанию —
                         все сконвертированные и включённые в external_datasets.yaml)
    --external-cap N     не больше N кадров из КАЖДОГО внешнего датасета (с приоритетом редких классов)
    --strict             любая ошибочная строка разметки — ошибка (иначе строка отбрасывается)
    --force              пересобрать, даже если входные данные не изменились

Проверка разметки: в строке ровно 5 полей, id класса — целое в диапазоне classes.yaml,
координаты в [0, 1], ширина и высота > 0. После сборки датасет проверяется ещё раз целиком.
Повторный запуск без изменений входных данных ничего не пересобирает.
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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SCRIPT_VERSION = 1
SOURCES = ("pseudo", "reviewed", "external")
LABEL_KEYS = {"pseudo": "prelabels", "reviewed": "labels"}
SPLITS = ("train", "val")
EPS = 1e-6
MAX_EXAMPLES = 20
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
EXT_CONFIG = common.CONFIG_DIR / "external_datasets.yaml"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class BuildError(Exception):
    """Ошибка сборки, понятная пользователю."""


def warn(msg: str) -> None:
    print(f"ВНИМАНИЕ: {msg}", file=sys.stderr)


def loud(msg: str) -> None:
    bar = "!" * 78
    print(f"\n{bar}\n!!! {msg}\n{bar}\n", file=sys.stderr)


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def rel_to_data(path: Path) -> str:
    try:
        return path.resolve().relative_to(common.data_dir().resolve()).as_posix()
    except ValueError:
        return str(path)


# ----------------------------------------------------------------------------- данные

@dataclass
class Frame:
    image: Path
    video_id: str
    stem: str
    scene: str
    reason: str


@dataclass
class Item:
    """Кадр, который пойдёт в датасет."""
    image: Path
    dest_stem: str                 # имя файла в датасете без расширения
    lines: list[str]               # проверенные строки YOLO
    origin: str                    # own | <имя внешнего датасета>
    scene: str = ""
    video_id: str = ""
    reason: str = ""
    size: int = 0
    mtime_ns: int = 0
    classes: list[int] = field(default_factory=list)

    @property
    def dest_name(self) -> str:
        return self.dest_stem + self.image.suffix.lower()


def check_line(parts: list[str], nc: int) -> tuple[str | None, str | None]:
    """Строка YOLO → (нормализованная строка, None) или (None, причина ошибки)."""
    if len(parts) != 5:
        return None, f"полей {len(parts)} вместо 5"
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None, "не число"
    cls_f = vals[0]
    if not math.isfinite(cls_f) or not cls_f.is_integer():
        return None, "id класса не целое"
    cls = int(cls_f)
    if not 0 <= cls < nc:
        return None, f"id класса {cls} вне 0..{nc - 1}"
    coords = vals[1:]
    if not all(math.isfinite(v) and -EPS <= v <= 1 + EPS for v in coords):
        return None, "координаты вне [0, 1]"
    cx, cy, w, h = (min(1.0, max(0.0, v)) for v in coords)
    if w <= 0 or h <= 0:
        return None, "нулевая ширина или высота"
    return common.Box(cls, cx, cy, w, h).to_yolo_line(), None


class LabelChecker:
    """Читает файлы разметки, отбрасывает ошибочные строки и дубли, копит примеры ошибок."""

    def __init__(self, nc: int):
        self.nc = nc
        self.invalid = 0
        self.duplicates = 0
        self.examples: list[str] = []

    def read(self, path: Path) -> list[str]:
        lines, seen = [], set()
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for lineno, raw in enumerate(text.splitlines(), 1):
            parts = raw.split()
            if not parts:
                continue
            clean, err = check_line(parts, self.nc)
            if err:
                self.invalid += 1
                if len(self.examples) < MAX_EXAMPLES:
                    self.examples.append(f"{rel_to_data(path)}:{lineno}: {err}: {raw.strip()[:80]}")
                continue
            if clean in seen:
                self.duplicates += 1
                continue
            seen.add(clean)
            lines.append(clean)
        return lines


def make_item(image: Path, dest_stem: str, lines: list[str], origin: str, **kw) -> Item | None:
    try:
        st = os.stat(image)
    except OSError:
        return None
    return Item(image=image, dest_stem=dest_stem, lines=lines, origin=origin,
                size=st.st_size, mtime_ns=st.st_mtime_ns,
                classes=[int(ln.split()[0]) for ln in lines], **kw)


def load_catalog_scenes() -> dict[str, str]:
    try:
        return {vid: info.scene.strip().upper() for vid, info in common.load_catalog().items()}
    except Exception as e:  # каталог в git, но не валим сборку, если его нет
        warn(f"не удалось прочитать каталог событий ({e}); сцены — только из index.csv")
        return {}


def load_frames(scenes_by_video: dict[str, str]) -> list[Frame]:
    """Кадры из data/frames/index.csv (или сканированием папок, если индекса нет)."""
    frames_root = common.data_path("frames")
    index = frames_root / "index.csv"
    data_root = common.data_dir()
    frames: list[Frame] = []
    if index.is_file():
        with open(index, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                rel = (row.get("frame_path") or "").strip().replace("\\", "/")
                if not rel:
                    continue
                vid = common.norm_video_id(row.get("video_id") or Path(rel).parent.name)
                scene = (row.get("scene") or "").strip().upper() or scenes_by_video.get(vid, "")
                frames.append(Frame(data_root / rel, vid, Path(rel).stem, scene, (row.get("reason") or "").strip()))
    elif frames_root.is_dir():
        warn(f"нет {rel_to_data(index)} — ищу кадры по папкам {rel_to_data(frames_root)}/<видео>/")
        for vdir in sorted(p for p in frames_root.iterdir() if p.is_dir()):
            vid = common.norm_video_id(vdir.name)
            for img in sorted(vdir.glob("*.jpg")):
                frames.append(Frame(img, vid, img.stem, scenes_by_video.get(vid, ""), ""))
    unique, seen = [], set()
    for fr in frames:
        key = fr.stem.casefold()
        if fr.video_id in common.DUPLICATES or key in seen:   # дубли роликов Х-47/Х-48 не берём
            continue
        seen.add(key)
        unique.append(fr)
    return unique


def own_items(frames: list[Frame], source: str, checker: LabelChecker, skipped: Counter,
              scenes_by_video: dict[str, str]) -> list[Item]:
    """Наши кадры, у которых есть файл разметки нужного источника."""
    label_root = common.data_path(LABEL_KEYS[source])
    items: list[Item] = []
    covered: set[str] = set()
    for fr in frames:
        lp = label_root / fr.video_id / f"{fr.stem}.txt"
        if not lp.is_file():
            skipped[f"{source}: кадр без разметки"] += 1
            continue
        covered.add(fr.stem.casefold())
        item = make_item(fr.image, fr.stem, checker.read(lp), "own",
                         scene=fr.scene, video_id=fr.video_id, reason=fr.reason)
        if item is None:
            skipped["нет файла кадра"] += 1
            continue
        items.append(item)
    # разметка для кадров, которых нет в index.csv (например, индекс пересоздан) — берём, если кадр на месте
    if label_root.is_dir():
        frames_root = common.data_path("frames")
        for lp in sorted(label_root.glob("*/*.txt")):
            if lp.stem.casefold() in covered:
                continue
            vid = common.norm_video_id(lp.parent.name)
            if vid in common.DUPLICATES:
                continue
            img = frames_root / lp.parent.name / f"{lp.stem}.jpg"
            item = make_item(img, lp.stem, checker.read(lp), "own",
                             scene=scenes_by_video.get(vid, ""), video_id=vid, reason="")
            if item is None:
                skipped[f"{source}: разметка без кадра"] += 1
                continue
            skipped[f"{source}: кадр не из index.csv (взят)"] += 1
            covered.add(lp.stem.casefold())
            items.append(item)
    return items


# ----------------------------------------------------------------------------- внешние датасеты

def balanced_sample(items: list[tuple[str, set[int]]], cap: int, seed: str) -> set[str]:
    """Детерминированная выборка не более cap ключей с приоритетом редких классов.

    Логика как в 06_merge_external.py: каждый раз берётся класс с наименьшим числом выбранных
    кадров; кадры без рамок добираются последними, если осталось место.
    """
    keys = [k for k, _ in items]
    if cap <= 0 or len(items) <= cap:
        return set(keys)
    classes_of = dict(items)
    queues: dict[int, list[str]] = defaultdict(list)
    ordered = sorted(keys, key=lambda k: stable_hash(f"{seed}:{k}"))
    for key in ordered:
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
    for key in ordered:                       # кадры без рамок — только если место осталось
        if len(selected) >= cap:
            break
        if not classes_of[key]:
            selected.add(key)
    return selected


def converted_names() -> list[str]:
    root = common.data_path("external_converted")
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and (p / "stats.json").is_file())


def default_external_names() -> list[str]:
    """Для --source external: сконвертированные датасеты, включённые в external_datasets.yaml."""
    available = converted_names()
    if not EXT_CONFIG.is_file():
        return available
    try:
        cfg = common.load_yaml(EXT_CONFIG) or {}
    except Exception as e:
        warn(f"не удалось прочитать {EXT_CONFIG.name}: {e}; беру все сконвертированные датасеты")
        return available
    enabled = {str(d.get("name")) for d in cfg.get("datasets") or []
               if isinstance(d, dict) and d.get("enabled", True)}
    off = [n for n in available if n not in enabled]
    if off:
        print(f"Не беру выключенные в {EXT_CONFIG.name} датасеты: {', '.join(off)}")
    return [n for n in available if n in enabled]


def external_items(names: list[str], cap: int | None, checker: LabelChecker, our_names: list[str],
                   skipped: Counter) -> tuple[list[Item], dict]:
    root = common.data_path("external_converted")
    items: list[Item] = []
    info: dict = {}
    for name in names:
        d = root / name
        stats_path = d / "stats.json"
        if not stats_path.is_file():
            raise BuildError(f"внешний датасет {name!r} не сконвертирован ({rel_to_data(d)}): "
                             f"запустите training/scripts/06_merge_external.py"
                             + (f"; есть: {', '.join(converted_names())}" if converted_names() else ""))
        try:
            st = json.loads(stats_path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise BuildError(f"{rel_to_data(stats_path)} повреждён: {e}") from e
        if st.get("class_names") != our_names:
            raise BuildError(f"классы в classes.yaml изменились после конвертации {name!r}: "
                             f"python training/scripts/06_merge_external.py --only {name} --force")
        if not (d / "images").is_dir():
            raise BuildError(f"нет папки {rel_to_data(d / 'images')}: "
                             f"python training/scripts/06_merge_external.py --only {name} --force")
        ds_items: list[Item] = []
        for img in sorted(p for p in (d / "images").iterdir() if p.suffix.lower() in IMAGE_EXTS):
            lp = d / "labels" / f"{img.stem}.txt"
            if not lp.is_file():
                skipped[f"{name}: картинка без разметки"] += 1
                continue
            item = make_item(img, img.stem, checker.read(lp), name)
            if item is not None:
                ds_items.append(item)
        if not ds_items:
            raise BuildError(f"во внешнем датасете {name!r} нет кадров с разметкой")
        if cap:
            keep = balanced_sample([(it.dest_stem, set(it.classes)) for it in ds_items], cap, seed=name)
            chosen = [it for it in ds_items if it.dest_stem in keep]
        else:
            chosen = ds_items
        lic = str(st.get("license") or "")
        if not lic.strip():
            loud(f"внешний датасет {name}: лицензия не указана (FAQ п. 34 — нужна коммерческая)")
        info[name] = {"images": len(chosen), "available": len(ds_items), "license": lic,
                      "source_url": st.get("source_url", "")}
        items.extend(chosen)
    return items, info


# ----------------------------------------------------------------------------- сборка

def split_stats(items: list[Item], names: list[str]) -> dict:
    boxes, frames_with, scenes, reasons, origins = Counter(), Counter(), Counter(), Counter(), Counter()
    videos, empty = set(), 0
    for it in items:
        for c in it.classes:
            boxes[names[c]] += 1
        for c in set(it.classes):
            frames_with[names[c]] += 1
        if not it.classes:
            empty += 1
        origins[it.origin] += 1
        if it.origin == "own":
            scenes[it.scene or "?"] += 1
            reasons[it.reason or "?"] += 1
            videos.add(it.video_id)
    return {
        "frames": len(items),
        "own_frames": origins["own"],
        "external_frames": len(items) - origins["own"],
        "empty_frames": empty,
        "videos": len(videos),
        "boxes_total": sum(boxes.values()),
        "boxes": {n: boxes[n] for n in names},
        "frames_with_class": {n: frames_with[n] for n in names},
        "scenes": dict(sorted(scenes.items())),
        "reasons": dict(sorted(reasons.items())),
        "external": {k: v for k, v in sorted(origins.items()) if k != "own"},
    }


def link_or_copy(src: Path, dst: Path, counter: Counter) -> None:
    try:
        os.link(src, dst)
        counter["hardlink"] += 1
    except OSError:
        shutil.copy2(src, dst)
        counter["copy"] += 1


def verify_dataset(out_dir: Path, nc: int) -> list[str]:
    """Повторная проверка собранного датасета: пары картинка↔разметка и каждая строка."""
    errors = []
    for split in SPLITS:
        img_dir, lbl_dir = out_dir / "images" / split, out_dir / "labels" / split
        imgs = {p.stem for p in img_dir.iterdir() if p.is_file()} if img_dir.is_dir() else set()
        lbls = {p.stem for p in lbl_dir.glob("*.txt")} if lbl_dir.is_dir() else set()
        for stem in sorted(imgs - lbls)[:5]:
            errors.append(f"{split}: у картинки {stem} нет разметки")
        for stem in sorted(lbls - imgs)[:5]:
            errors.append(f"{split}: у разметки {stem} нет картинки")
        for lp in sorted(lbl_dir.glob("*.txt")) if lbl_dir.is_dir() else []:
            for lineno, raw in enumerate(lp.read_text(encoding="utf-8").splitlines(), 1):
                parts = raw.split()
                if parts:
                    _, err = check_line(parts, nc)
                    if err:
                        errors.append(f"{split}/{lp.name}:{lineno}: {err}")
                        if len(errors) >= MAX_EXAMPLES:
                            return errors
    return errors


def build_dataset(out_dir: Path, train: list[Item], val: list[Item], meta: dict, names: list[str],
                  force: bool, val_is_train: bool = False) -> tuple[str, dict]:
    """Собирает один датасет. Возвращает (статус, stats); статус: built | skipped."""
    plan = [("train", it) for it in train] + [("val", it) for it in val]
    seen: dict[str, Item] = {}
    for split, it in plan:
        key = it.dest_name.casefold()
        if key in seen:
            raise BuildError(f"совпадают имена файлов в датасете: {it.image} и {seen[key].image}")
        seen[key] = it

    signature = stable_hash(json.dumps({
        "version": SCRIPT_VERSION,
        "meta": meta,
        "names": names,
        "val_is_train": val_is_train,
        "out": str(out_dir.resolve()),
        "items": [(split, it.dest_name, str(it.image), it.size, it.mtime_ns, stable_hash("\n".join(it.lines)))
                  for split, it in plan],
    }, sort_keys=True, ensure_ascii=False))

    stats_path = out_dir / "stats.json"
    if not force and stats_path.is_file() and (out_dir / "dataset.yaml").is_file():
        try:
            old = json.loads(stats_path.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
        if old.get("signature") == signature:
            return "skipped", old

    out_root = out_dir.parent
    out_root.mkdir(parents=True, exist_ok=True)
    partial = out_root / f".{out_dir.name}.partial"
    if partial.exists():
        shutil.rmtree(partial)
    for split in SPLITS:
        (partial / "images" / split).mkdir(parents=True)
        (partial / "labels" / split).mkdir(parents=True)

    files: Counter = Counter()
    for split, it in plan:
        link_or_copy(it.image, partial / "images" / split / it.dest_name, files)
        (partial / "labels" / split / f"{it.dest_stem}.txt").write_text(
            "".join(ln + "\n" for ln in it.lines), encoding="utf-8")

    doc = {
        "path": out_dir.resolve().as_posix(),
        "train": "images/train",
        "val": "images/train" if val_is_train else "images/val",
        "nc": len(names),
        "names": list(names),
    }
    header = ("# Собрано training/scripts/07_build_dataset.py — не править вручную, пересобрать скриптом.\n"
              f"# источник: {meta['source']}, val: {', '.join(meta['val_scenes']) or '—'}"
              + (" (val = train: своих кадров для проверки нет)" if val_is_train else "") + "\n")
    (partial / "dataset.yaml").write_text(
        header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8")

    stats = {
        "name": out_dir.name,
        **meta,
        "val_is_train": val_is_train,
        "dataset_yaml": (out_dir / "dataset.yaml").resolve().as_posix(),
        "splits": {"train": split_stats(train, names), "val": split_stats(val, names)},
        "files": dict(files),
        "signature": signature,
        "script_version": SCRIPT_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (partial / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    partial.rename(out_dir)

    errors = verify_dataset(out_dir, len(names))
    if errors:
        raise BuildError(f"проверка собранного {rel_to_data(out_dir)} не прошла:\n  " + "\n  ".join(errors))
    return "built", stats


# ----------------------------------------------------------------------------- main

def parse_list(text: str | None) -> list[str]:
    return [s.strip() for s in (text or "").split(",") if s.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Сборка датасета Ultralytics data/yolo/<name>/ (images, labels, dataset.yaml, stats.json).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Примеры:\n"
               "  07_build_dataset.py --source pseudo\n"
               "  07_build_dataset.py --source reviewed --loso\n"
               "  07_build_dataset.py --source reviewed --external banknotes --external-cap 500\n"
               "  07_build_dataset.py --source external --name ext_pretrain")
    p.add_argument("--source", required=True, choices=SOURCES,
                   help="pseudo — data/prelabels; reviewed — только кадры с data/labels; "
                        "external — только внешние датасеты (предобучение)")
    p.add_argument("--name", default=None, help="имя датасета: data/yolo/NAME (по умолчанию = --source)")
    p.add_argument("--val-scenes", default=None,
                   help="сцены для val через запятую (по умолчанию split.val_scenes из pipeline.yaml)")
    p.add_argument("--loso", action="store_true",
                   help="ещё собрать NAME_loso_<сцена> для каждой сцены (эта сцена — val)")
    p.add_argument("--external", default=None,
                   help="внешние датасеты через запятую (из data/external_converted), только в train")
    p.add_argument("--external-cap", type=int, default=None, metavar="N",
                   help="не больше N кадров из каждого внешнего датасета")
    p.add_argument("--strict", action="store_true",
                   help="ошибочная строка разметки — ошибка сборки (по умолчанию строка отбрасывается)")
    p.add_argument("--force", action="store_true", help="пересобрать, даже если ничего не изменилось")
    args = p.parse_args()
    if args.external_cap is not None and args.external_cap < 1:
        p.error("--external-cap должен быть >= 1")
    return args


def print_class_table(stats: dict, names: list[str]) -> None:
    tr, va = stats["splits"]["train"], stats["splits"]["val"]
    print(f"  {'класс':<18} {'train':>8} {'val':>8}   (рамок; кадров с классом)")
    for n in names:
        print(f"  {n:<18} {tr['boxes'][n]:>8} {va['boxes'][n]:>8}   "
              f"({tr['frames_with_class'][n]} / {va['frames_with_class'][n]})")
    print(f"  {'кадров':<18} {tr['frames']:>8} {va['frames']:>8}   "
          f"(внешних в train: {tr['external_frames']}, пустых: {tr['empty_frames']} / {va['empty_frames']})")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parse_args()
    try:
        return run(args)
    except BuildError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 1


def run(args: argparse.Namespace) -> int:
    names = common.class_names()
    nc = len(names)
    split_cfg = common.pipeline_config().get("split") or {}
    if args.val_scenes is not None:
        val_scenes = [s.upper() for s in parse_list(args.val_scenes)]
    else:
        val_scenes = [str(s).strip().upper() for s in split_cfg.get("val_scenes") or []]
    if split_cfg.get("external_in_val"):
        warn("split.external_in_val: true игнорируется — внешние кадры идут только в train")
    name = args.name or args.source
    if not NAME_RE.match(name):
        raise BuildError(f"--name {name!r}: допустимы латиница, цифры, '_', '-', '.'")

    checker = LabelChecker(nc)
    skipped: Counter = Counter()
    scenes_by_video = load_catalog_scenes()
    frames = load_frames(scenes_by_video)
    all_scenes = sorted({f.scene for f in frames if f.scene} or set(scenes_by_video.values()) - {""})
    if args.source != "external" and not frames:
        raise BuildError(f"нет кадров в {common.data_path('frames')}: сначала 02_extract_frames.py")

    # наши кадры с разметкой
    if args.source in LABEL_KEYS:
        own_source = args.source
        own = own_items(frames, own_source, checker, skipped, scenes_by_video)
        if not own:
            hint = "03_prelabel.py" if own_source == "pseudo" else "05_labelstudio_import.py"
            raise BuildError(f"нет ни одного кадра с разметкой в {common.data_path(LABEL_KEYS[own_source])}: "
                             f"сначала {hint}")
    else:
        # val для внешнего предобучения: проверенная разметка, если она есть в сценах val, иначе pseudo
        vset = set(val_scenes)
        own, own_source = [], "pseudo"
        for cand_source in ("reviewed", "pseudo"):
            tmp_checker, tmp_skipped = LabelChecker(nc), Counter()
            cand = own_items(frames, cand_source, tmp_checker, tmp_skipped, scenes_by_video)
            if any(it.scene in vset for it in cand) or cand_source == "pseudo":
                own, own_source = cand, cand_source
                checker.invalid += tmp_checker.invalid
                checker.duplicates += tmp_checker.duplicates
                checker.examples.extend(tmp_checker.examples[:MAX_EXAMPLES - len(checker.examples)])
                break
        n_val = sum(1 for it in own if it.scene in vset)
        print(f"val для внешнего предобучения — наши кадры сцен {', '.join(val_scenes) or '—'}, "
              f"разметка {own_source}: {n_val} кадров")

    # внешние датасеты
    if args.external is not None:
        ext_names = parse_list(args.external)
    elif args.source == "external":
        ext_names = default_external_names()
    else:
        ext_names = []
    if args.source == "external" and not ext_names:
        raise BuildError("нет сконвертированных внешних датасетов: настройте configs/external_datasets.yaml "
                         "и запустите training/scripts/06_merge_external.py")
    ext, ext_info = external_items(ext_names, args.external_cap, checker, names, skipped)

    if checker.invalid:
        warn(f"ошибочных строк разметки: {checker.invalid} (отброшены). Примеры:\n  "
             + "\n  ".join(checker.examples))
        if args.strict:
            raise BuildError("--strict: исправьте разметку и запустите снова")
    unknown_scene = sum(1 for it in own if not it.scene)
    if unknown_scene:
        warn(f"{unknown_scene} кадров без сцены (нет в каталоге) — идут в train")
    missing_val = [s for s in val_scenes if s not in all_scenes]
    if missing_val and all_scenes:
        warn(f"сцен {', '.join(missing_val)} нет среди кадров (есть: {', '.join(all_scenes)})")

    meta_base = {
        "source": args.source,
        "own_label_source": own_source,
        "external": ext_info,
        "external_cap": args.external_cap,
        "label_problems": {"invalid_lines": checker.invalid, "duplicate_lines": checker.duplicates,
                           "examples": checker.examples},
        "skipped": dict(sorted(skipped.items())),
    }

    plans = [(name, val_scenes, True)]
    if args.loso:
        plans += [(f"{name}_loso_{s}", [s], False) for s in all_scenes]

    yolo_root = common.data_path("yolo")
    results = []
    for ds_name, vs, is_main in plans:
        vset = set(vs)
        val = [it for it in own if it.scene in vset]
        train = list(ext) if args.source == "external" else [it for it in own if it.scene not in vset] + ext
        val_is_train = False
        if not train:
            msg = f"{ds_name}: пустой train"
            if is_main:
                raise BuildError(msg)
            warn(msg + " — фолд пропущен")
            continue
        if not val:
            if args.source == "external":
                val_is_train = True
                loud(f"{ds_name}: своих размеченных кадров сцен {', '.join(vs) or '—'} нет — val = train, "
                     "метрики валидации не показательны")
            elif is_main:
                raise BuildError(f"{ds_name}: в val нет кадров ({args.source}, сцены {', '.join(vs) or '—'}). "
                                 "Разметьте кадры этих сцен или задайте другие --val-scenes")
            else:
                warn(f"{ds_name}: нет размеченных кадров сцены {', '.join(vs)} — фолд пропущен")
                continue
        meta = {**meta_base, "val_scenes": vs}
        out_dir = yolo_root / ds_name
        status, stats = build_dataset(out_dir, train, val, meta, names, args.force, val_is_train)
        results.append((ds_name, status, stats))

    # итог
    print(f"\nИтог 07_build_dataset (источник: {args.source}, разметка наших кадров: {own_source}):")
    status_ru = {"built": "собран", "skipped": "без изменений"}
    for ds_name, status, stats in results:
        tr, va = stats["splits"]["train"], stats["splits"]["val"]
        val_txt = "val = train" if stats.get("val_is_train") else f"val {va['frames']:>5} кадров ({', '.join(stats['val_scenes'])})"
        print(f"  data/yolo/{ds_name:<24} {status_ru[status]:<14} train {tr['frames']:>5} кадров "
              f"(внешних {tr['external_frames']}), {val_txt}; рамок {tr['boxes_total']} / {va['boxes_total']}")
    for k, v in sorted(skipped.items()):
        print(f"  пропущено — {k}: {v}")
    if checker.duplicates:
        print(f"  повторяющихся строк разметки убрано: {checker.duplicates}")
    for ext_name, inf in ext_info.items():
        print(f"  внешний {ext_name}: {inf['images']} из {inf['available']} кадров, лицензия: "
              f"{inf['license'] or 'НЕ УКАЗАНА'}")

    main_stats = results[0][2]
    print(f"\nКлассы в data/yolo/{results[0][0]}:")
    print_class_table(main_stats, names)
    if not main_stats.get("val_is_train"):
        no_val = [n for n in names if main_stats["splits"]["val"]["boxes"][n] == 0]
        if no_val:
            warn(f"в val нет рамок классов: {', '.join(no_val)} — их точность на val не измерить")
    no_train = [n for n in names if main_stats["splits"]["train"]["boxes"][n] == 0]
    if no_train:
        warn(f"в train нет рамок классов: {', '.join(no_train)} — модель их не выучит")
    print(f"\ndataset.yaml: {main_stats['dataset_yaml']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
