"""Шаг B1: задачи для Label Studio с предварительной разметкой → data/labelstudio/.

Что делает:
  1. data/labelstudio/label_config.xml — интерфейс разметки: RectangleLabels со всеми классами
     из configs/classes.yaml (подсказка при наведении — что размечать, поле review);
  2. data/labelstudio/tasks.json — по задаче на кадр из data/frames/index.csv:
       data.image  = /data/local-files/?d=<путь кадра от LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT>
       data.video_id, data.stem, data.t_sec, data.reason, data.scene, data.caption — для поиска
       и для обратного импорта (05_labelstudio_import.py);
       predictions — рамки из data/prelabels/<video_id>/<stem>.txt (+ уверенность из .json)
       в процентах кадра (rectanglelabels, from_name="label", to_name="image");
  3. печатает переменные окружения и шаги: создать проект, подключить локальные файлы, импорт.
Кадры не копируются и никуда не загружаются: Label Studio читает их с диска.

Запуск из корня репозитория:
    python training/scripts/04_labelstudio_export.py
    python training/scripts/04_labelstudio_export.py --only-event-frames         # сначала кадры у нарушений
    python training/scripts/04_labelstudio_export.py --videos X-13 X-20 --skip-reviewed
    python training/scripts/04_labelstudio_export.py --document-root /Users/me/datathon/data

Файлы перезаписываются, только если их содержимое изменилось (--force — всегда).
Коды возврата: 0 — успех, 1 — часть кадров не найдена на диске (в задачи не вошли),
2 — нет index.csv / нет кадров / кадры вне --document-root.
"""
from __future__ import annotations

import sys, pathlib  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

FROM_NAME = "label"      # имя RectangleLabels в label_config.xml
TO_NAME = "image"        # имя Image в label_config.xml
MODEL_VERSION = "prelabel"  # одна версия на все задачи: Label Studio подставляет одну выбранную версию
LOCAL_FILES_PREFIX = "/data/local-files/?d="

# Контрастные цвета рамок; при большем числе классов идут по кругу.
PALETTE = [
    "#1f77b4", "#2ca02c", "#17becf", "#7f7f7f", "#d62728", "#9467bd", "#8c564b",
    "#e377c2", "#bcbd22", "#ff7f0e", "#393b79", "#637939", "#843c39", "#7b4173",
]


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


def _to_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def _to_int(value: str | None) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def write_if_changed(path: Path, text: str, force: bool) -> bool:
    """Записать файл, если содержимое другое (или force). True — записан."""
    if not force and path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except (OSError, UnicodeDecodeError):
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def labelstudio_dir() -> Path:
    try:
        return common.data_path("labelstudio")
    except KeyError:  # в pipeline.yaml нет paths.labelstudio
        return common.data_dir() / "labelstudio"


# --------------------------------------------------------------------------- index

@dataclass
class Frame:
    rel_path: str
    video_id: str
    t_sec: float
    reason: str
    scene: str
    width: int
    height: int

    @property
    def stem(self) -> str:
        return Path(self.rel_path).stem


def load_index(index_path: Path) -> list[Frame]:
    frames: list[Frame] = []
    with open(index_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rel = (row.get("frame_path") or "").strip().replace("\\", "/")
            if not rel:
                continue
            vid = common.norm_video_id(row.get("video_id") or Path(rel).parent.name)
            if vid in common.DUPLICATES:
                continue
            frames.append(Frame(rel, vid, _to_float(row.get("t_sec")), (row.get("reason") or "").strip(),
                                (row.get("scene") or "").strip(), _to_int(row.get("width")),
                                _to_int(row.get("height"))))
    frames.sort(key=lambda fr: (vid_key(fr.video_id), fr.t_sec, fr.rel_path))
    return frames


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


# --------------------------------------------------------------------------- label config

def build_label_config(classes: list[dict]) -> str:
    lines = [
        "<View>",
        '  <Header value="$caption"/>',
        f'  <Image name="{TO_NAME}" value="$image" zoom="true" zoomControl="true" rotateControl="false"/>',
        f'  <RectangleLabels name="{FROM_NAME}" toName="{TO_NAME}" strokeWidth="2" canRotate="false">',
    ]
    for c in classes:
        color = PALETTE[c["id"] % len(PALETTE)]
        hint = f"{c.get('ru', c['name'])}. {c.get('review', '')}".strip()
        lines.append(f"    <Label value={quoteattr(c['name'])} background={quoteattr(color)} hint={quoteattr(hint)}/>")
    lines += ["  </RectangleLabels>", "</View>", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- predictions

def result_id(stem: str, i: int) -> str:
    return hashlib.md5(f"{stem}:{i}".encode("utf-8")).hexdigest()[:10]


def _pct(v: float) -> float:
    return round(min(max(v * 100.0, 0.0), 100.0), 4)


def boxes_to_results(boxes: list[common.Box], scores: list[float | None], names: list[str], stem: str,
                     width: int, height: int) -> list[dict]:
    results = []
    for i, (b, score) in enumerate(zip(boxes, scores)):
        if not 0 <= b.cls < len(names):
            continue
        x1, y1 = b.cx - b.w / 2, b.cy - b.h / 2
        x, y = _pct(x1), _pct(y1)
        item = {
            "id": result_id(stem, i),
            "from_name": FROM_NAME,
            "to_name": TO_NAME,
            "type": "rectanglelabels",
            "original_width": width,
            "original_height": height,
            "image_rotation": 0,
            "value": {
                "x": x, "y": y,
                "width": round(min(_pct(x1 + b.w) - x, 100.0 - x), 4),
                "height": round(min(_pct(y1 + b.h) - y, 100.0 - y), 4),
                "rotation": 0,
                "rectanglelabels": [names[b.cls]],
            },
        }
        if score is not None:
            item["score"] = round(float(score), 4)
        results.append(item)
    return results


def read_prediction(label_dir: Path, fr: Frame) -> tuple[list[common.Box], list[float | None]] | None:
    """Рамки кадра из <dir>/<video_id>/<stem>.txt и уверенность из .json (если есть). None — файла нет."""
    txt = label_dir / fr.video_id / f"{fr.stem}.txt"
    if not txt.exists():
        return None
    boxes = common.read_yolo_labels(txt)
    scores: list[float | None] = [None] * len(boxes)
    js = txt.with_suffix(".json")
    if js.exists():
        try:
            meta = json.loads(js.read_text(encoding="utf-8")).get("boxes", [])
        except (OSError, ValueError, AttributeError):
            meta = []
        if len(meta) == len(boxes):
            scores = [m.get("score") for m in meta]
    return boxes, scores


# --------------------------------------------------------------------------- instructions

def print_instructions(doc_root: Path, frames_dir: Path, config_path: Path, tasks_path: Path) -> None:
    if os.name == "nt":
        env = (f'  $env:LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED="true"\n'
               f'  $env:LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT="{doc_root}"\n'
               f"  label-studio start")
        shell = "PowerShell"
    else:
        env = (f"  export LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true\n"
               f'  export LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT="{doc_root}"\n'
               f"  label-studio start")
        shell = "терминал"
    print(f"""
=== Как открыть задачи в Label Studio ===
1. Запустить Label Studio с доступом к локальным файлам (отдельный {shell}, окружение .venv-ls):
{env}
2. http://localhost:8080 → войти (учётная запись локальная, кадры не уходят с компьютера).
3. Create Project → Project Name: kassa → Labeling Setup → Custom template → вкладка Code →
   вставить содержимое
     {config_path}
   → Save.
4. Settings → Cloud Storage → Add Source Storage → Storage Type: Local files
     Storage Title: frames
     Absolute local path: {frames_dir}
     File Filter Regex: .*\\.jpg
   → Check Connection → Add Storage.
   Кнопку «Sync Storage» НЕ нажимать: появятся задачи-дубли без предварительной разметки.
   (Хранилище нужно только для того, чтобы Label Studio разрешил отдавать кадры.)
5. Settings → Annotation (в старых версиях — Machine Learning) → включить
   «Use predictions to prelabel tasks», источник «{MODEL_VERSION}» → Save.
6. Вернуться в проект → Import → Upload Files → выбрать
     {tasks_path}
   → Import.
7. Правка: неподвижные предметы (сейф, ноутбук, дверь, машинка, детектор) достаточно поправить
   на первом кадре ролика — при импорте рамка размножится на остальные проверенные кадры.
   Ложный неподвижный предмет достаточно удалить на первом кадре — он уйдёт из кадров без правки.
8. Готово → Export → JSON → сохранить файл и в другом терминале, с окружением .venv-train
   (не .venv-ls: импорту нужен PyYAML из обучающего окружения), выполнить:
     python training/scripts/05_labelstudio_import.py --export <файл>.json
""")


# --------------------------------------------------------------------------- main

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--videos", nargs="+", metavar="ID",
                   help="только эти ролики: X-1 X-13 или X-1,X-13 (можно кириллицей Х-13)")
    p.add_argument("--only-event-frames", action="store_true",
                   help="только кадры вокруг нарушений (reason=event в index.csv)")
    p.add_argument("--document-root", type=Path, default=None,
                   help="LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT (по умолчанию data_dir)")
    p.add_argument("--skip-reviewed", action="store_true",
                   help="не включать кадры, у которых уже есть проверенная разметка data/labels/")
    p.add_argument("--prefer-reviewed", action="store_true",
                   help="если есть data/labels/<video>/<stem>.txt, подставлять её вместо предварительной")
    p.add_argument("--no-predictions", action="store_true", help="задачи без предварительной разметки")
    p.add_argument("--out-dir", type=Path, default=None, help="куда писать (по умолчанию data/labelstudio)")
    p.add_argument("--force", action="store_true", help="перезаписать файлы, даже если не изменились")
    return p.parse_args()


def main() -> int:
    setup_console()
    args = parse_args()
    classes = common.load_classes()
    names = [c["name"] for c in classes]
    data_root = common.data_dir().resolve()
    doc_root = (args.document_root.expanduser().resolve() if args.document_root else data_root)
    frames_dir = common.data_path("frames").resolve()
    prelabel_dir = common.data_path("prelabels")
    labels_dir = common.data_path("labels")
    out_dir = (args.out_dir.expanduser().resolve() if args.out_dir else labelstudio_dir())

    index_path = frames_dir / "index.csv"
    if not index_path.exists():
        print(f"[x] нет {index_path}. Сначала: python training/scripts/02_extract_frames.py")
        return 2
    try:
        frames_dir.relative_to(doc_root)
    except ValueError:
        print(f"[x] кадры {frames_dir} лежат вне --document-root {doc_root}: Label Studio не сможет их отдать")
        return 2

    frames = load_index(index_path)
    wanted = parse_videos(args.videos)
    if wanted:
        missing_vids = sorted(wanted - {f.video_id for f in frames}, key=vid_key)
        if missing_vids:
            print(f"[!] в index.csv нет роликов: {', '.join(missing_vids)}")
        frames = [f for f in frames if f.video_id in wanted]
    if args.only_event_frames:
        frames = [f for f in frames if f.reason == "event"]

    stats: Counter = Counter()
    per_cls: Counter = Counter()
    per_video: Counter = Counter()
    missing_files: list[str] = []
    tasks: list[dict] = []
    for fr in frames:
        if args.skip_reviewed and (labels_dir / fr.video_id / f"{fr.stem}.txt").exists():
            stats["reviewed_skipped"] += 1
            continue
        abs_path = (data_root / fr.rel_path).resolve()
        if not abs_path.exists():
            missing_files.append(fr.rel_path)
            continue
        width, height = fr.width, fr.height
        if width <= 0 or height <= 0:
            try:
                width, height = image_size(abs_path)
            except OSError as e:
                missing_files.append(f"{fr.rel_path} ({e})")
                continue
        rel_doc = abs_path.relative_to(doc_root).as_posix()
        caption = f"{fr.video_id} · {fr.t_sec:.2f} с · {fr.reason or '—'} · сцена {fr.scene or '?'}"
        task: dict = {"data": {
            "image": LOCAL_FILES_PREFIX + quote(rel_doc, safe="/"),
            "video_id": fr.video_id, "stem": fr.stem, "t_sec": round(fr.t_sec, 3),
            "reason": fr.reason, "scene": fr.scene, "caption": caption,
        }}
        if not args.no_predictions:
            pred = read_prediction(labels_dir, fr) if args.prefer_reviewed else None
            source = "reviewed" if pred is not None else "prelabel"
            if pred is None:
                pred = read_prediction(prelabel_dir, fr)
            if pred is None:
                stats["no_prelabel"] += 1
            else:
                boxes, scores = pred
                results = boxes_to_results(boxes, scores, names, fr.stem, width, height)
                known = [s for s in scores if s is not None]
                prediction = {"model_version": MODEL_VERSION, "result": results}
                if known:
                    prediction["score"] = round(sum(known) / len(known), 4)
                task["predictions"] = [prediction]
                stats[f"pred_{source}"] += 1
                for r in results:
                    per_cls[r["value"]["rectanglelabels"][0]] += 1
        tasks.append(task)
        per_video[fr.video_id] += 1
        stats["event" if fr.reason == "event" else "base"] += 1

    if not tasks:
        print("[x] нет кадров для задач (проверьте --videos / --only-event-frames / --skip-reviewed)")
        for rel in missing_files[:10]:
            print(f"[x] нет файла кадра: {rel}")
        return 2

    config_path = out_dir / "label_config.xml"
    tasks_path = out_dir / "tasks.json"
    cfg_written = write_if_changed(config_path, build_label_config(classes), args.force)
    tasks_written = write_if_changed(tasks_path, json.dumps(tasks, ensure_ascii=False, indent=1) + "\n",
                                     args.force)

    print("=== Экспорт в Label Studio: итог ===")
    print(f"задач: {len(tasks)} (кадры у нарушений: {stats['event']}, остальные: {stats['base']}); "
          f"роликов: {len(per_video)}")
    print(f"с предварительной разметкой: {stats['pred_prelabel']}"
          + (f", с проверенной: {stats['pred_reviewed']}" if args.prefer_reviewed else "")
          + f"; без разметки: {stats['no_prelabel']}"
          + (f"; пропущено проверенных: {stats['reviewed_skipped']}" if args.skip_reviewed else ""))
    if per_cls:
        print("рамок по классам: " + ", ".join(f"{n} {per_cls[n]}" for n in names if per_cls[n]))
    print(f"{config_path}: {'записан' if cfg_written else 'без изменений'}")
    print(f"{tasks_path}: {'записан' if tasks_written else 'без изменений'}")
    if stats["no_prelabel"] and not args.no_predictions:
        print("[!] у части кадров нет предварительной разметки — запустите 03_prelabel.py")
    for rel in missing_files[:10]:
        print(f"[x] нет файла кадра: {rel}")
    if len(missing_files) > 10:
        print(f"[x] ... и ещё {len(missing_files) - 10}")

    print_instructions(doc_root, frames_dir, config_path, tasks_path)
    return 1 if missing_files else 0


if __name__ == "__main__":
    sys.exit(main())
