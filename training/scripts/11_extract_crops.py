"""Шаг D1: фрагменты кадров для классификаторов состояний (экран ноутбука, ключ в замке сейфа, дверь).

Использование (из корня репозитория):
    python training/scripts/11_extract_crops.py --weights runs/detect/full/weights/best.pt
    python training/scripts/11_extract_crops.py --from-labels               # data/labels, где нет — data/prelabels
    python training/scripts/11_extract_crops.py --from-labels prelabels --tasks safe_lock
    python training/scripts/11_extract_crops.py --weights runs/detect/full/weights/best.pt --videos X-22,X-23

Откуда рамки:
    --weights <best.pt>             детектор (рамка с наибольшей уверенностью, порог --conf);
    --from-labels [auto|labels|prelabels]
                                    готовая разметка: auto — проверенная data/labels, а для кадров без
                                    неё — data/prelabels (берётся самая крупная рамка класса).

Какие фрагменты (по одному на кадр и задачу):
    screen     рамка laptop (+5 % поля)                      → классы active / locked / off
    safe_lock  рамка safe_closed: правая средняя часть, где замок (доли рамки x 0.55-1.0, y 0.25-0.75,
               --lock-region), +15 % поля, увеличение x4 и не меньше 224 px по короткой стороне
                                                             → классы key / no_key
    door       рамка door (+5 % поля)                        → классы closed / open
    Короткая сторона любого фрагмента доводится минимум до 224 px.

Куда:
    data/crops/<task>/_unsorted/<stem>.jpg — ждут ручной сортировки по папкам классов;
    data/crops/<task>/<class>/<stem>.jpg   — известные по каталогу случаи раскладываются сразу:
        safe_lock: X-22 с 14 с → key, X-23 с 12 с → no_key;
        screen:    X-11 с 15 с и X-28 с 7 с → active, X-29 с 6 с и X-23 с 5 с → locked.
    data/crops/crop_spec.json — геометрия вырезания (сервис должен вырезать так же).

Повторный запуск не трогает уже вырезанные фрагменты (где бы они ни лежали — ручная сортировка
сохраняется); --force перезаписывает изображения на их текущем месте.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import csv
import json
import os
from collections import Counter
from pathlib import Path

from kassa_train import weights as wts

UNSORTED = "_unsorted"
MIN_SIDE = 224
DEFAULT_LOCK_REGION = (0.55, 0.25, 1.0, 0.75)

# Геометрия: region — доли рамки (x1, y1, x2, y2); pad — поле в долях размера области; upscale — увеличение.
TASK_SPECS = {
    "screen":    {"source_class": "laptop",      "region": (0.0, 0.0, 1.0, 1.0), "pad": 0.05, "upscale": 1.0},
    "safe_lock": {"source_class": "safe_closed", "region": DEFAULT_LOCK_REGION,  "pad": 0.15, "upscale": 4.0},
    "door":      {"source_class": "door",        "region": (0.0, 0.0, 1.0, 1.0), "pad": 0.05, "upscale": 1.0},
}

# Известные по каталогу (analysis/labels_typed.csv, analysis/detection_design.md) состояния:
# (ролик, с какой секунды, класс).
SEEDS = {
    "safe_lock": [("X-22", 14.0, "key"), ("X-23", 12.0, "no_key")],
    "screen": [("X-11", 15.0, "active"), ("X-28", 7.0, "active"),
               ("X-29", 6.0, "locked"), ("X-23", 5.0, "locked")],
    "door": [],
}


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def yaml_label(v) -> str:
    """YAML 1.1 читает off/on/no/yes как bool — возвращаем исходное слово."""
    if v is False:
        return "off"
    if v is True:
        return "on"
    return str(v)


def cls_task_classes(task: str) -> list[str]:
    cfg = common.load_yaml(common.CONFIG_DIR / "train.yaml")
    tasks = (cfg.get("cls") or {}).get("tasks") or {}
    if task not in tasks:
        raise KeyError(f"задачи {task!r} нет в train.yaml → cls.tasks")
    return [yaml_label(c) for c in tasks[task].get("classes", [])]


def seed_class(task: str, video_id: str, t_sec: float) -> str | None:
    for vid, t_from, cls in SEEDS.get(task, []):
        if video_id == vid and t_sec >= t_from:
            return cls
    return None


# ---------------------------------------------------------------- геометрия

def crop_box(img, box_xyxy: tuple[float, float, float, float], spec: dict):
    """Вырезает область spec['region'] рамки с полем spec['pad'] и увеличивает. None — слишком мало."""
    from PIL import Image

    W, H = img.size
    x1, y1, x2, y2 = box_xyxy
    bw, bh = x2 - x1, y2 - y1
    rx1, ry1, rx2, ry2 = spec["region"]
    cx1, cx2 = x1 + rx1 * bw, x1 + rx2 * bw
    cy1, cy2 = y1 + ry1 * bh, y1 + ry2 * bh
    pw, ph = (cx2 - cx1) * spec["pad"], (cy2 - cy1) * spec["pad"]
    ix1, iy1 = max(0, int(round(cx1 - pw))), max(0, int(round(cy1 - ph)))
    ix2, iy2 = min(W, int(round(cx2 + pw))), min(H, int(round(cy2 + ph)))
    w, h = ix2 - ix1, iy2 - iy1
    if w < 4 or h < 4:
        return None
    crop = img.crop((ix1, iy1, ix2, iy2))
    scale = max(float(spec["upscale"]), MIN_SIDE / min(w, h))
    if scale > 1.0:
        crop = crop.resize((int(round(w * scale)), int(round(h * scale))), Image.LANCZOS)
    return crop


def write_crop_spec(specs: dict) -> Path:
    """Обновляет data/crops/crop_spec.json для обработанных задач, записи остальных задач сохраняются."""
    path = common.data_path("crops") / "crop_spec.json"
    tasks: dict = {}
    if path.exists():
        try:
            tasks = json.loads(path.read_text(encoding="utf-8")).get("tasks") or {}
        except (OSError, ValueError):
            tasks = {}
    for task, s in specs.items():
        tasks[task] = {"source_class": s["source_class"], "region": list(s["region"]), "pad": s["pad"],
                       "upscale": s["upscale"], "min_side": MIN_SIDE, "classes": cls_task_classes(task)}
    data = {
        "version": 1,
        "description": "Геометрия фрагментов для YOLO26-cls (11_extract_crops.py): region — доли рамки "
                       "(x1, y1, x2, y2) от левого верхнего угла; pad — поле в долях размера области "
                       "с каждой стороны; затем увеличение в upscale раз и не меньше min_side по короткой "
                       "стороне (LANCZOS). Рамка — самая уверенная рамка source_class на кадре.",
        "tasks": dict(sorted(tasks.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- рамки

def load_frames(videos: set[str] | None) -> list[dict]:
    index = common.data_path("frames") / "index.csv"
    if not index.exists():
        raise FileNotFoundError(f"нет {index}: сначала запустите 02_extract_frames.py")
    root = common.data_dir()
    frames = []
    with open(index, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            vid = common.norm_video_id(row["video_id"])
            if vid in common.DUPLICATES or (videos and vid not in videos):
                continue
            path = root / row["frame_path"]
            frames.append({"path": path, "stem": path.stem, "video_id": vid, "t_sec": float(row["t_sec"])})
    return frames


def label_file_for(frame: dict, mode: str) -> Path | None:
    rel = Path(frame["video_id"]) / f"{frame['stem']}.txt"
    reviewed, pre = common.data_path("labels") / rel, common.data_path("prelabels") / rel
    if mode in ("auto", "labels") and reviewed.exists():
        return reviewed
    if mode in ("auto", "prelabels") and pre.exists():
        return pre
    return None


def boxes_from_labels(frame: dict, img_size: tuple[int, int], mode: str, wanted: dict[int, str]) -> dict:
    """{класс: (x1, y1, x2, y2)} — самая крупная рамка каждого нужного класса из YOLO-разметки."""
    path = label_file_for(frame, mode)
    if path is None:
        return {}
    W, H = img_size
    best: dict[str, tuple[float, tuple]] = {}
    for b in common.read_yolo_labels(path):
        name = wanted.get(b.cls)
        if name is None:
            continue
        xyxy = ((b.cx - b.w / 2) * W, (b.cy - b.h / 2) * H, (b.cx + b.w / 2) * W, (b.cy + b.h / 2) * H)
        area = b.w * b.h
        if name not in best or area > best[name][0]:
            best[name] = (area, xyxy)
    return {k: v[1] for k, v in best.items()}


def boxes_from_result(result, wanted: dict[int, str]) -> dict:
    """{класс: (x1, y1, x2, y2)} — самая уверенная рамка каждого нужного класса из предсказания."""
    best: dict[str, tuple[float, tuple]] = {}
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return {}
    for xyxy, cls, conf in zip(boxes.xyxy.tolist(), boxes.cls.tolist(), boxes.conf.tolist()):
        name = wanted.get(int(cls))
        if name is None:
            continue
        if name not in best or conf > best[name][0]:
            best[name] = (float(conf), tuple(xyxy))
    return {k: v[1] for k, v in best.items()}


# ---------------------------------------------------------------- main

def existing_crops(task_dir: Path) -> dict[str, Path]:
    """stem → текущий путь фрагмента в любой подпапке задачи (_unsorted или папка класса)."""
    found = {}
    if task_dir.exists():
        for p in task_dir.rglob("*.jpg"):
            found.setdefault(p.stem, p)
    return found


def parse_region(text: str) -> tuple[float, float, float, float]:
    vals = tuple(float(v) for v in text.split(","))
    if len(vals) != 4 or not (0 <= vals[0] < vals[2] <= 1 and 0 <= vals[1] < vals[3] <= 1):
        raise argparse.ArgumentTypeError("нужно x1,y1,x2,y2 в долях рамки, 0 <= x1 < x2 <= 1")
    return vals


def main() -> int:
    _setup_console()
    ap = argparse.ArgumentParser(
        description="Фрагменты laptop / safe_closed / door для классификаторов состояний → data/crops/<task>/.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--weights", help="детектор .pt (runs/detect/<run>/weights/best.pt)")
    src.add_argument("--from-labels", nargs="?", const="auto", choices=["auto", "labels", "prelabels"],
                     help="брать рамки из разметки: auto (по умолчанию) | labels | prelabels")
    ap.add_argument("--tasks", default=",".join(TASK_SPECS), help="через запятую: screen,safe_lock,door")
    ap.add_argument("--videos", help="только эти ролики, через запятую (X-22,X-23)")
    ap.add_argument("--conf", type=float, default=0.35, help="порог уверенности детектора")
    ap.add_argument("--imgsz", type=int, help="размер входа детектора (по умолчанию из args.yaml запуска, иначе 864)")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--batch", type=int, default=16, help="кадров за один проход детектора")
    ap.add_argument("--lock-region", type=parse_region, default=DEFAULT_LOCK_REGION,
                    help="область замка в долях рамки safe_closed: x1,y1,x2,y2 (по умолчанию 0.55,0.25,1.0,0.75)")
    ap.add_argument("--force", action="store_true", help="перезаписать уже вырезанные фрагменты (на их месте)")
    args = ap.parse_args()

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    bad = [t for t in tasks if t not in TASK_SPECS]
    if bad:
        print(f"Неизвестные задачи: {bad}; есть: {list(TASK_SPECS)}")
        return 2
    specs = {t: dict(TASK_SPECS[t]) for t in tasks}
    if "safe_lock" in specs:
        specs["safe_lock"]["region"] = tuple(args.lock_region)
    task_classes = {t: cls_task_classes(t) for t in tasks}
    for t in tasks:
        for _, _, c in SEEDS.get(t, []):
            if c not in task_classes[t]:
                print(f"Ошибка: класс {c!r} из каталога нет в train.yaml → cls.tasks.{t}.classes {task_classes[t]}")
                return 2

    videos = {common.norm_video_id(v) for v in args.videos.split(",")} if args.videos else None
    try:
        frames = load_frames(videos)
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}")
        return 2
    if not frames:
        print("Нет кадров для обработки (проверьте data/frames/index.csv и --videos).")
        return 2

    crops_root = common.data_path("crops")
    task_dirs = {t: crops_root / t for t in tasks}
    for t in tasks:
        for d in [UNSORTED, *task_classes[t]]:
            (task_dirs[t] / d).mkdir(parents=True, exist_ok=True)
    existing = {t: existing_crops(task_dirs[t]) for t in tasks}
    spec_path = write_crop_spec(specs)

    needed_classes = {specs[t]["source_class"] for t in tasks}
    model = None
    if args.weights:
        try:
            weights = wts.resolve_weight(args.weights)
        except FileNotFoundError as exc:
            print(f"Ошибка: {exc}")
            return 2
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        try:
            from ultralytics import YOLO
        except ImportError:
            print("Нет ultralytics. Активируйте окружение: source .venv-train/bin/activate")
            return 2
        model = YOLO(weights)
        names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
        wanted = {int(i): n for i, n in names.items() if n in needed_classes}
        missing = needed_classes - set(wanted.values())
        if missing:
            print(f"Внимание: в детекторе нет классов {sorted(missing)} — соответствующие задачи будут пустыми.")
        if not wanted:
            print(f"Ошибка: детектор {weights} не знает ни одного из классов {sorted(needed_classes)}.")
            return 2
        imgsz = args.imgsz or wts.read_run_args(weights).get("imgsz") or 864
        device = common.pick_device(args.device)
        print(f"Детектор {weights}: imgsz {imgsz}, conf {args.conf}, {device}; кадров {len(frames)}")
    else:
        names = dict(enumerate(common.class_names()))
        wanted = {i: n for i, n in names.items() if n in needed_classes}
        print(f"Рамки из разметки ({args.from_labels}); кадров {len(frames)}")

    from PIL import Image

    stats = {t: Counter() for t in tasks}

    def handle(frame: dict, img, boxes: dict) -> None:
        for t in tasks:
            st = stats[t]
            box = boxes.get(specs[t]["source_class"])
            if box is None:
                st["no_box"] += 1
                continue
            stem = frame["stem"]
            seed = seed_class(t, frame["video_id"], frame["t_sec"])
            cur = existing[t].get(stem)
            if cur is not None:
                # уже вырезан: известный случай из _unsorted переносим в папку класса, ручную сортировку не трогаем
                if seed and cur.parent.name == UNSORTED:
                    target = task_dirs[t] / seed / cur.name
                    os.replace(cur, target)
                    existing[t][stem] = target
                    cur = target
                    st[f"seed:{seed}"] += 1
                if not args.force:
                    st["skipped"] += 1
                    continue
                target = cur
            else:
                target = task_dirs[t] / (seed or UNSORTED) / f"{stem}.jpg"
            crop = crop_box(img, box, specs[t])
            if crop is None:
                st["too_small"] += 1
                continue
            crop.save(target, quality=95)
            existing[t][stem] = target
            st["written"] += 1
            if seed and cur is None:
                st[f"seed:{seed}"] += 1

    def load(frame: dict):
        with Image.open(frame["path"]) as im:
            return im.convert("RGB")

    errors = 0
    if model is not None:
        for i in range(0, len(frames), args.batch):
            chunk = frames[i:i + args.batch]
            imgs, ok = [], []
            for fr in chunk:
                try:
                    imgs.append(load(fr))
                    ok.append(fr)
                except OSError as exc:
                    print(f"Не прочитан кадр {fr['path']}: {exc}")
                    errors += 1
            if not imgs:
                continue
            results = model.predict(source=imgs, imgsz=imgsz, conf=args.conf, device=device,
                                    classes=sorted(wanted), verbose=False)
            for fr, img, res in zip(ok, imgs, results):
                handle(fr, img, boxes_from_result(res, wanted))
            done = min(i + args.batch, len(frames))
            if done % (args.batch * 10) == 0 or done == len(frames):
                print(f"  {done}/{len(frames)} кадров")
    else:
        for fr in frames:
            if label_file_for(fr, args.from_labels) is None:
                for t in tasks:
                    stats[t]["no_labels"] += 1
                continue
            try:
                img = load(fr)
            except OSError as exc:
                print(f"Не прочитан кадр {fr['path']}: {exc}")
                errors += 1
                continue
            handle(fr, img, boxes_from_labels(fr, img.size, args.from_labels, wanted))

    print("\n=== Итог ===")
    total_unsorted = 0
    for t in tasks:
        st = stats[t]
        per_class = {c: len(list((task_dirs[t] / c).glob("*.jpg"))) for c in task_classes[t]}
        unsorted = len(list((task_dirs[t] / UNSORTED).glob("*.jpg")))
        total_unsorted += unsorted
        seeded = ", ".join(f"{k.split(':', 1)[1]} {v}" for k, v in sorted(st.items()) if k.startswith("seed:"))
        print(f"{t}: записано фрагментов {st['written']}, пропущено (уже есть) {st['skipped']}, "
              f"кадров без рамки {specs[t]['source_class']} {st['no_box'] + st['no_labels']}"
              + (f", слишком мелких {st['too_small']}" if st["too_small"] else ""))
        if seeded:
            print(f"    разложено по каталогу: {seeded}")
        print("    по классам: " + ", ".join(f"{c} {n}" for c, n in per_class.items())
              + f"; ждут ручной сортировки: {unsorted} ({task_dirs[t] / UNSORTED})")
        empty = [c for c, n in per_class.items() if n == 0]
        if empty:
            print(f"    нет ни одного примера: {', '.join(empty)} — нужно разложить вручную до 12_train_cls.py")
    if errors:
        print(f"Не прочитано кадров: {errors}")
    print(f"Осталось разложить вручную: {total_unsorted}. Геометрия фрагментов: {spec_path}")
    return 1 if errors >= len(frames) else 0


if __name__ == "__main__":
    sys.exit(main())
