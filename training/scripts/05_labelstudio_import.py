"""Шаг B2: проверенная разметка из Label Studio → data/labels/<video_id>/<stem>.txt.

Что делает:
  1. читает экспорт Label Studio (Export → JSON; формат JSON_MIN тоже понимается);
  2. у каждой задачи берёт разметку человека (annotations): не отменённую, «ground truth»,
     иначе самую позднюю. Задачи без разметки пропускаются; с --accept-predictions вместо неё
     берётся предварительная разметка задачи (predictions) — как будто человек её принял;
  3. переводит прямоугольники из процентов кадра в формат YOLO (повёрнутые — в описанный
     прямоугольник), находит кадр (data.video_id/data.stem из 04_labelstudio_export.py или
     путь картинки) и пишет data/labels/<video_id>/<stem>.txt;
  4. размножает неподвижные предметы (`static: true` в classes.yaml — сейф закрыт, ноутбук,
     дверь, машинка, детектор). Для каждого ролика и класса берётся опорный кадр: самый ранний
     кадр экспорта, где человек поправил или нарисовал рамку (поле origin Label Studio, иначе —
     сравнение с data/prelabels) или удалил лишнюю рамку этого класса, оставив остальные
     (например, второй ложный «сейф»: удалить его на первом кадре — и он уйдёт из всех кадров
     ролика, где рамки не правились) или удалил все рамки класса (ложный «детектор»: удалить его
     на первом кадре — и он уйдёт из всех кадров ролика, где рамки не правились); если таких нет —
     ранее импортированный исправленный кадр этого ролика из data/labels; если нет и его — самый
     ранний кадр экспорта с этим классом. Его рамки класса копируются во все остальные проверенные
     кадры ролика из экспорта, где рамок этого класса нет, и заменяют рамки, оставшиеся от
     предварительной разметки без правки. Кадры, где человек сам поправил или удалил рамки
     класса, не трогаются. В кадры с safe_open рамка safe_closed не добавляется, а удаление
     safe_closed в кадре с открытым сейфом не считается удалением ложного предмета.

Запуск из корня репозитория:
    python training/scripts/05_labelstudio_import.py --export ~/Downloads/project-1-at-2026-10-17.json
    python training/scripts/05_labelstudio_import.py --export export.json --accept-predictions
    python training/scripts/05_labelstudio_import.py --export export.json --videos X-13 --dry-run

Повторный импорт того же экспорта ничего не меняет: файл переписывается, только если его
содержимое изменилось (--force — всегда). Кадры, которых нет в экспорте, не трогаются.
Коды возврата: 0 — успех, 1 — в экспорте нет ни одного проверенного кадра,
2 — файл экспорта не найден или не читается.
"""
from __future__ import annotations

import sys, pathlib  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

UNTOUCHED_IOU = 0.98   # рамка совпадает с предварительной так плотно — значит, её не правили
STEM_RE = re.compile(r"^(?P<vid>.+)_t(?P<ms>\d{7,})$")
UPLOAD_PREFIX_RE = re.compile(r"^[0-9a-f]{8}-")  # Label Studio добавляет к загруженным файлам
FROM_KEY = "label"     # имя RectangleLabels (04_labelstudio_export.FROM_NAME) — ключ рамок в JSON_MIN


# --------------------------------------------------------------------------- helpers

def setup_console() -> None:
    """Не падать на символах, которых нет в кодировке консоли (Windows, вывод в pipe)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def vid_key(video_id: str) -> tuple[str, int, str]:
    """Естественная сортировка: X-2 < X-10."""
    m = re.match(r"^([A-Z]+)-(\d+)$", video_id)
    return (m.group(1), int(m.group(2)), "") if m else (video_id, 0, video_id)


def parse_videos(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    out: set[str] = set()
    for value in values:
        for part in re.split(r"[,\s]+", value):
            if part.strip():
                vid = common.norm_video_id(part)
                out.add(common.DUPLICATES.get(vid, vid))
    return out or None


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def box_xyxy(b: common.Box) -> tuple[float, float, float, float]:
    return b.cx - b.w / 2, b.cy - b.h / 2, b.cx + b.w / 2, b.cy + b.h / 2


def box_iou(a: common.Box, b: common.Box) -> float:
    ax1, ay1, ax2, ay2 = box_xyxy(a)
    bx1, by1, bx2, by2 = box_xyxy(b)
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def copy_box(b: common.Box, source: str) -> common.Box:
    return common.Box(b.cls, b.cx, b.cy, b.w, b.h, b.score, source)


# --------------------------------------------------------------------------- frames index

@dataclass
class IndexRow:
    video_id: str
    stem: str
    t_sec: float
    width: int
    height: int


def load_index_map(index_path: Path) -> dict[str, IndexRow]:
    out: dict[str, IndexRow] = {}
    if not index_path.exists():
        return out
    with open(index_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rel = (row.get("frame_path") or "").strip().replace("\\", "/")
            if not rel:
                continue
            stem = Path(rel).stem
            vid = common.norm_video_id(row.get("video_id") or Path(rel).parent.name)
            out[stem] = IndexRow(vid, stem, _to_float(row.get("t_sec")),
                                 int(_to_float(row.get("width"))), int(_to_float(row.get("height"))))
    return out


# --------------------------------------------------------------------------- tasks

@dataclass
class Reviewed:
    video_id: str
    stem: str
    t_sec: float
    boxes: list[common.Box]
    modified: list[bool | None]          # None — неизвестно (нет origin), решается по prelabels
    origin: str                          # annotation | prediction
    updated: str = ""
    task_id: object = None
    added: int = 0
    replaced: int = 0


def image_ref(data: dict) -> str:
    for key in ("image", "img", "url"):
        if isinstance(data.get(key), str):
            return data[key]
    return ""


def stem_from_url(url: str) -> tuple[str | None, str | None]:
    """'/data/local-files/?d=frames/X-13/X-13_t0047250.jpg' → ('X-13', 'X-13_t0047250')."""
    if not url:
        return None, None
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    raw = qs["d"][0] if "d" in qs and qs["d"] else unquote(parsed.path)
    p = Path(raw.replace("\\", "/"))
    stem = UPLOAD_PREFIX_RE.sub("", p.stem)
    m = STEM_RE.match(stem)
    vid = m.group("vid") if m else (p.parent.name or None)
    return (common.norm_video_id(vid) if vid else None), stem


def locate_frame(data: dict, index: dict[str, IndexRow]) -> tuple[str | None, str | None, float]:
    vid = data.get("video_id")
    stem = data.get("stem")
    if not stem:
        vid_url, stem = stem_from_url(image_ref(data))
        vid = vid or vid_url
    if not stem:
        return None, None, 0.0
    stem = str(stem)
    row = index.get(stem)
    if row is not None:
        return row.video_id, stem, row.t_sec
    m = STEM_RE.match(stem)
    if not vid and m:
        vid = m.group("vid")
    t = int(m.group("ms")) / 1000.0 if m else _to_float(data.get("t_sec"))
    return (common.norm_video_id(vid) if vid else None), stem, t


def rect_to_box(r: dict, name_to_id: dict[str, int], fallback_wh: tuple[int, int],
                stats: Counter) -> tuple[common.Box, bool | None] | None:
    """Элемент результата Label Studio → (Box YOLO, правил ли человек рамку)."""
    if "value" in r:
        if r.get("type") not in (None, "rectanglelabels"):
            stats[f"skip_type:{r.get('type')}"] += 1
            return None
        v = r["value"]
    elif "rectanglelabels" in r:  # JSON_MIN
        v = r
    else:
        stats["skip_unknown_result"] += 1
        return None
    labels = v.get("rectanglelabels") or []
    if not labels:
        stats["skip_no_label"] += 1
        return None
    name = str(labels[0])
    if name not in name_to_id:
        stats[f"skip_label:{name}"] += 1
        return None
    W = r.get("original_width") or v.get("original_width") or fallback_wh[0]
    H = r.get("original_height") or v.get("original_height") or fallback_wh[1]
    try:
        x, y, w, h = (float(v[k]) for k in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        stats["skip_bad_geometry"] += 1
        return None
    rot = float(v.get("rotation") or 0.0)
    if abs(rot) > 1e-6 and W and H:
        # Label Studio вращает прямоугольник вокруг левого верхнего угла (по часовой, ось y вниз)
        px, py, pw, ph = x * W / 100, y * H / 100, w * W / 100, h * H / 100
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        xs, ys = [], []
        for dx, dy in ((0, 0), (pw, 0), (pw, ph), (0, ph)):
            xs.append(px + dx * c - dy * s)
            ys.append(py + dx * s + dy * c)
        x1, y1, x2, y2 = min(xs) / W, min(ys) / H, max(xs) / W, max(ys) / H
    else:
        x1, y1, x2, y2 = x / 100, y / 100, (x + w) / 100, (y + h) / 100
    x1, x2 = sorted((min(max(x1, 0.0), 1.0), min(max(x2, 0.0), 1.0)))
    y1, y2 = sorted((min(max(y1, 0.0), 1.0), min(max(y2, 0.0), 1.0)))
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        stats["skip_empty_box"] += 1
        return None
    origin = r.get("origin")
    modified = {"prediction": False, "manual": True, "prediction-changed": True}.get(origin)
    box = common.Box(name_to_id[name], (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, 1.0, "ls")
    return box, modified


def pick_annotation(task: dict) -> dict | None:
    anns = [a for a in task.get("annotations") or [] if isinstance(a, dict) and not a.get("was_cancelled")]
    if not anns:
        return None
    return max(anns, key=lambda a: (bool(a.get("ground_truth")), str(a.get("updated_at") or ""),
                                    _to_float(a.get("id"), 0.0)))


def pick_prediction(task: dict) -> dict | None:
    preds = [p for p in task.get("predictions") or [] if isinstance(p, dict) and "result" in p]
    if not preds:
        return None
    return max(preds, key=lambda p: (str(p.get("updated_at") or p.get("created_at") or ""),
                                     _to_float(p.get("id"), 0.0)))


def parse_export(tasks: list, index: dict[str, IndexRow], name_to_id: dict[str, int], accept_predictions: bool,
                 stats: Counter) -> dict[str, Reviewed]:
    reviewed: dict[str, Reviewed] = {}
    for task in tasks:
        if not isinstance(task, dict):
            stats["skip_not_task"] += 1
            continue
        is_min = "annotations" not in task and "data" not in task
        data = task if is_min else (task.get("data") or {})
        vid, stem, t_sec = locate_frame(data, index)
        if not vid or not stem:
            stats["skip_unknown_image"] += 1
            continue
        if stem not in index:
            stats["warn_not_in_index"] += 1

        if is_min:
            if "annotation_id" not in task and FROM_KEY not in task:
                stats["unreviewed"] += 1
                continue
            results = [r for r in task.get(FROM_KEY) or [] if isinstance(r, dict)]
            origin, updated = "annotation", str(task.get("updated_at") or "")
        else:
            ann = pick_annotation(task)
            if ann is not None:
                results, origin, updated = ann.get("result") or [], "annotation", str(ann.get("updated_at") or "")
            elif accept_predictions and (pred := pick_prediction(task)) is not None:
                results, origin, updated = pred.get("result") or [], "prediction", ""
            else:
                stats["cancelled" if task.get("annotations") else "unreviewed"] += 1
                continue

        row = index.get(stem)
        fallback = (row.width, row.height) if row else (0, 0)
        boxes: list[common.Box] = []
        modified: list[bool | None] = []
        for r in results:
            if not isinstance(r, dict):
                continue
            conv = rect_to_box(r, name_to_id, fallback, stats)
            if conv is None:
                continue
            boxes.append(conv[0])
            modified.append(False if origin == "prediction" else conv[1])
        item = Reviewed(vid, stem, t_sec, boxes, modified, origin, updated, task.get("id"))
        prev = reviewed.get(stem)
        if prev is not None:
            stats["duplicate_tasks"] += 1
            # приоритет: разметка человека > принятая предразметка, затем более поздняя
            if (prev.origin == "annotation", prev.updated) > (item.origin == "annotation", item.updated):
                continue
        reviewed[stem] = item
        stats[f"from_{origin}"] += 1
    return reviewed



# --------------------------------------------------------------------------- static propagation

def resolve_modified(item: Reviewed, prelabel_dir: Path) -> None:
    """Где origin неизвестен: рамка совпадает с предварительной → не правилась."""
    if all(m is not None for m in item.modified):
        return
    pre = common.read_yolo_labels(prelabel_dir / item.video_id / f"{item.stem}.txt")
    for i, (b, m) in enumerate(zip(item.boxes, item.modified)):
        if m is None:
            item.modified[i] = not any(p.cls == b.cls and box_iou(p, b) >= UNTOUCHED_IOU for p in pre)


def earlier_corrected(video_id: str, cls: int, exclude: set[str], labels_dir: Path, prelabel_dir: Path,
                      safe_closed: int | None = None, safe_open: int | None = None) -> list[common.Box] | None:
    """Рамки класса из самого раннего ранее импортированного кадра ролика, где человек их правил.

    Правка — рамка не совпадает с предварительной или рамок класса стало меньше, чем в
    предварительной (лишние удалены). [] — человек удалил все рамки класса: предмета нет.
    None — исправленных кадров нет.
    """
    vdir = labels_dir / video_id
    if not vdir.is_dir():
        return None
    for txt in sorted(vdir.glob("*.txt"), key=lambda p: p.stem):  # имя кадра сортируется по времени
        if txt.stem in exclude:
            continue
        labels = common.read_yolo_labels(txt)
        boxes = [b for b in labels if b.cls == cls]
        pre = [p for p in common.read_yolo_labels(prelabel_dir / video_id / txt.name) if p.cls == cls]
        if not boxes and cls == safe_closed and safe_open is not None and any(b.cls == safe_open for b in labels):
            continue  # сейф открыт: закрытого нет по состоянию, а не потому, что рамку удалили
        if len(boxes) < len(pre) or any(not any(box_iou(p, b) >= UNTOUCHED_IOU for p in pre) for b in boxes):
            return boxes
    return None


def propagate_static(reviewed: dict[str, Reviewed], static_cls: set[int], safe_closed: int | None,
                     safe_open: int | None, labels_dir: Path, prelabel_dir: Path) -> dict[str, str]:
    """Размножить рамки неподвижных предметов по проверенным кадрам ролика. → {video:класс: откуда}.

    Удаление тоже правка: если человек удалил все рамки класса в кадре (ложный «детектор»),
    в этот кадр ничего не добавляется, а если это самый ранний исправленный кадр — он становится
    опорным с пустым списком рамок, и непоправленные рамки класса удаляются из остальных кадров.
    """
    by_video: dict[str, list[Reviewed]] = defaultdict(list)
    for item in reviewed.values():
        by_video[item.video_id].append(item)
    pre_cache: dict[str, list[common.Box] | None] = {}

    def has_open_safe(it: Reviewed) -> bool:
        return safe_open is not None and any(b.cls == safe_open for b in it.boxes)

    def deleted(it: Reviewed, cls: int) -> tuple[bool, bool]:
        """(человек удалил часть или все рамки класса из предварительной разметки, удалил все)."""
        if it.origin != "annotation":
            return False, False
        if it.stem not in pre_cache:
            path = prelabel_dir / it.video_id / f"{it.stem}.txt"
            pre_cache[it.stem] = common.read_yolo_labels(path) if path.exists() else None
        pre = pre_cache[it.stem]
        n_pre = sum(1 for b in pre if b.cls == cls) if pre else 0
        if n_pre == 0:
            return False, False
        if cls == safe_closed and has_open_safe(it):
            return False, False  # сейф открыт: закрытый убран по состоянию, это не «ложный предмет»
        n_now = sum(1 for b in it.boxes if b.cls == cls)
        return n_now < n_pre, n_now == 0

    refs: dict[str, str] = {}
    for vid, items in by_video.items():
        items.sort(key=lambda it: (it.t_sec, it.stem))
        stems = {it.stem for it in items}
        for cls in sorted(static_cls):
            ref_boxes: list[common.Box] | None = None
            ref_modified = False
            ref_from = ""
            for it in items:  # 1) самый ранний кадр экспорта, где рамку правил (или удалил) человек
                if any(b.cls == cls and m for b, m in zip(it.boxes, it.modified)) or deleted(it, cls)[0]:
                    ref_boxes = [b for b in it.boxes if b.cls == cls]  # [] — все рамки класса удалены
                    ref_modified, ref_from = True, it.stem
                    break
            if ref_boxes is None:  # 2) исправленный кадр из прошлых импортов
                ref_boxes = earlier_corrected(vid, cls, stems, labels_dir, prelabel_dir, safe_closed, safe_open)
                if ref_boxes is not None:
                    ref_modified, ref_from = True, "data/labels"
            if ref_boxes is None:  # 3) самый ранний кадр экспорта с этим классом (как есть)
                for it in items:
                    if any(b.cls == cls for b in it.boxes):
                        ref_boxes = [b for b in it.boxes if b.cls == cls]
                        ref_from = it.stem
                        break
            if ref_boxes is None:
                continue
            refs[f"{vid}:{cls}"] = ref_from if ref_boxes else f"{ref_from} (удалено)"
            for it in items:
                if it.stem == ref_from:
                    continue
                if cls == safe_closed and has_open_safe(it):
                    continue  # сейф в кадре открыт — закрытый сейф не добавлять
                some_deleted, all_deleted = deleted(it, cls)
                own = [i for i, b in enumerate(it.boxes) if b.cls == cls]
                if not own:
                    if all_deleted:
                        continue  # человек сам удалил рамки класса в этом кадре — не возвращать
                    it.boxes.extend(copy_box(b, "propagated") for b in ref_boxes)
                    it.modified.extend([False] * len(ref_boxes))
                    it.added += len(ref_boxes)
                elif ref_modified and not some_deleted and not any(it.modified[i] for i in own):
                    keep = [i for i in range(len(it.boxes)) if i not in set(own)]
                    it.boxes = [it.boxes[i] for i in keep] + [copy_box(b, "propagated") for b in ref_boxes]
                    it.modified = [it.modified[i] for i in keep] + [False] * len(ref_boxes)
                    it.replaced += len(own)
    return refs


# --------------------------------------------------------------------------- main

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--export", type=Path, required=True, help="файл экспорта Label Studio (Export → JSON)")
    p.add_argument("--accept-predictions", action="store_true",
                   help="задачи без разметки человека импортировать по предварительной разметке (predictions)")
    p.add_argument("--videos", nargs="+", metavar="ID", help="импортировать только эти ролики")
    p.add_argument("--no-propagate", action="store_true", help="не размножать неподвижные предметы")
    p.add_argument("--dry-run", action="store_true", help="только посчитать, ничего не писать")
    p.add_argument("--force", action="store_true", help="переписать файлы, даже если содержимое не изменилось")
    return p.parse_args()


def main() -> int:
    setup_console()
    args = parse_args()
    export_path = args.export.expanduser()
    if not export_path.exists():
        print(f"[x] нет файла экспорта {export_path}")
        return 2
    try:
        tasks = json.loads(export_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        print(f"[x] не читается JSON {export_path}: {e}")
        return 2
    if isinstance(tasks, dict):  # экспорт одной задачи
        tasks = [tasks]
    if not isinstance(tasks, list):
        print(f"[x] {export_path}: ожидается список задач Label Studio")
        return 2

    classes = common.load_classes()
    names = [c["name"] for c in classes]
    name_to_id = {n: i for i, n in enumerate(names)}
    static_cls = {c["id"] for c in classes if c.get("static")}
    safe_closed = name_to_id.get("safe_closed")
    safe_open = name_to_id.get("safe_open")
    index = load_index_map(common.data_path("frames") / "index.csv")
    if not index:
        print("[!] нет data/frames/index.csv — кадры определяются только по имени картинки")
    labels_dir = common.data_path("labels")
    prelabel_dir = common.data_path("prelabels")

    stats: Counter = Counter()
    reviewed = parse_export(tasks, index, name_to_id, args.accept_predictions, stats)
    wanted = parse_videos(args.videos)
    if wanted:
        reviewed = {k: v for k, v in reviewed.items() if v.video_id in wanted}
    if not reviewed:
        print(f"[x] в экспорте нет проверенных кадров (задач: {len(tasks)}, без разметки: {stats['unreviewed']}). "
              "Разметьте задачи или добавьте --accept-predictions")
        return 1

    for item in reviewed.values():
        resolve_modified(item, prelabel_dir)
    refs = {} if args.no_propagate else propagate_static(reviewed, static_cls, safe_closed, safe_open,
                                                         labels_dir, prelabel_dir)

    per_cls: Counter = Counter()
    per_video: dict[str, Counter] = defaultdict(Counter)
    written = unchanged = 0
    for item in sorted(reviewed.values(), key=lambda it: (vid_key(it.video_id), it.t_sec, it.stem)):
        boxes = sorted(item.boxes, key=lambda b: (b.cls, b.cy, b.cx))
        text = "".join(b.to_yolo_line() + "\n" for b in boxes)
        path = labels_dir / item.video_id / f"{item.stem}.txt"
        same = path.exists() and path.read_text(encoding="utf-8") == text
        if same and not args.force:
            unchanged += 1
        else:
            if not args.dry_run:
                common.write_yolo_labels(path, boxes)
            written += 1
        for b in boxes:
            per_cls[b.cls] += 1
        v = per_video[item.video_id]
        v["frames"] += 1
        v["boxes"] += len(boxes)
        v["added"] += item.added
        v["replaced"] += item.replaced
        v["accepted"] += item.origin == "prediction"
        v["empty"] += not boxes

    print("=== Импорт из Label Studio: итог ===")
    print(f"задач в экспорте: {len(tasks)}; проверенных кадров: {len(reviewed)} "
          f"(разметка человека: {stats['from_annotation']}, принятая предразметка: {stats['from_prediction']}); "
          f"без разметки: {stats['unreviewed']}; отменено: {stats['cancelled']}")
    action = "было бы записано" if args.dry_run else "записано"
    print(f"файлов {action}: {written}; без изменений: {unchanged} → {labels_dir}")
    print(f"\n{'класс':<18}{'рамок':>7}")
    for c, name in enumerate(names):
        print(f"{c:>2} {name:<15}{per_cls[c]:>7}")
    print(f"всего рамок: {sum(per_cls.values())}")
    print(f"\n{'ролик':<8}{'кадров':>7}{'рамок':>7}{'+стат':>7}{'замен':>7}{'принято':>9}{'пустых':>8}")
    for vid in sorted(per_video, key=vid_key):
        v = per_video[vid]
        print(f"{vid:<8}{v['frames']:>7}{v['boxes']:>7}{v['added']:>7}{v['replaced']:>7}"
              f"{v['accepted']:>9}{v['empty']:>8}")
    if refs:
        print("\nопорные кадры неподвижных предметов:")
        for key in sorted(refs, key=lambda k: (vid_key(k.split(':')[0]), int(k.split(':')[1]))):
            vid, cls = key.split(":")
            print(f"  {vid} {names[int(cls)]}: {refs[key]}")
    problems = {k: n for k, n in stats.items() if k.startswith(("skip_", "warn_", "duplicate"))}
    if problems:
        print("\n[!] замечания: " + ", ".join(f"{k} {n}" for k, n in sorted(problems.items())))
    if args.dry_run:
        print("(--dry-run: файлы не записаны)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
