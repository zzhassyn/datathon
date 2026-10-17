"""Шаг D2: обучение классификатора состояния YOLO26-cls по разложенным фрагментам.

Использование (из корня репозитория):
    python training/scripts/12_train_cls.py --task screen        # active / locked / off
    python training/scripts/12_train_cls.py --task safe_lock     # key / no_key
    python training/scripts/12_train_cls.py --task door          # closed / open
    python training/scripts/12_train_cls.py --task screen --epochs 50 --val-scenes L4,L5

Вход: data/crops/<task>/<class>/*.jpg (готовит 11_extract_crops.py + ручная сортировка из _unsorted).
Параметры модели — train.yaml → cls.tasks.<task> (model, imgsz, epochs, patience, batch, classes);
флаги --model, --epochs, --batch, --imgsz, --device переопределяют их.

Деление: фрагменты сцен из pipeline.yaml → split.val_scenes (или --val-scenes) идут в val,
остальные — в train (сцена — по ролику из имени файла и каталогу analysis/labels_typed.csv).
Собирается data/crops/<task>_split/{train,val}/<class>/. Если у класса нет ни одного
train-фрагмента — скрипт останавливается. Если во всей val нет ни одного фрагмента
(например, на сцене L4 нет сейфа), в val уходит каждый --holdout-every-й фрагмент каждого класса
(оценка тогда оптимистична: соседние кадры похожи).

Результат: runs/classify/<name>/weights/best.pt (name = task), cls_info.json (состав набора),
crop_spec.json (геометрия фрагмента — её повторяет сервис). Повторный запуск с тем же набором
фрагментов и параметрами пропускается; --force — обучить заново.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import hashlib
import json
import os
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from kassa_train import weights as wts

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
NON_ULTRALYTICS_KEYS = {"model", "classes"}
UNSORTED = "_unsorted"


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


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def video_of(stem: str) -> str:
    """'X-22_t0014250' → 'X-22'."""
    return common.norm_video_id(stem.rsplit("_t", 1)[0]) if "_t" in stem else common.norm_video_id(stem)


def plan_split(src_dir: Path, classes: list[str], val_scenes: set[str], holdout_every: int):
    """→ ({'train'|'val': {class: [Path]}}, сообщения). Сцена фрагмента — по ролику из имени файла."""
    catalog = common.load_catalog()
    split = {"train": defaultdict(list), "val": defaultdict(list)}
    notes = []
    unknown_videos = set()
    for c in classes:
        for p in sorted((src_dir / c).glob("*")):
            if p.suffix.lower() not in IMG_EXTS:
                continue
            vid = video_of(p.stem)
            info = catalog.get(common.DUPLICATES.get(vid, vid))
            if info is None:
                unknown_videos.add(vid)
            scene = info.scene if info else None
            split["val" if scene in val_scenes else "train"][c].append(p)
    if unknown_videos:
        notes.append(f"роликов нет в каталоге (их фрагменты — в train): {', '.join(sorted(unknown_videos))}")

    if sum(len(v) for v in split["val"].values()) == 0:
        notes.append(f"на сценах {sorted(val_scenes)} нет ни одного фрагмента — в val уходит каждый "
                     f"{holdout_every}-й фрагмент класса (оценка оптимистична)")
        for c in classes:
            files = split["train"][c]
            if len(files) < 2:
                continue
            val = files[holdout_every - 1::holdout_every] or files[-1:]
            val_set = set(val)
            keep = [f for f in files if f not in val_set]
            split["train"][c], split["val"][c] = keep, val
    return split, notes


def sync_split(split: dict, dst: Path, classes: list[str]) -> tuple[int, int]:
    """Копирует фрагменты в dst/{train,val}/<class>/, убирает устаревшие. → (скопировано, удалено)."""
    expected: dict[Path, Path] = {}
    for part in ("train", "val"):
        for c in classes:
            (dst / part / c).mkdir(parents=True, exist_ok=True)
            for src in split[part][c]:
                expected[dst / part / c / src.name] = src
    copied = removed = 0
    for target, src in expected.items():
        s = src.stat()
        if target.exists():
            t = target.stat()
            if t.st_size == s.st_size and int(t.st_mtime) == int(s.st_mtime):
                continue
        shutil.copy2(src, target)
        copied += 1
    for p in dst.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMG_EXTS and p not in expected:
            p.unlink()  # производный набор: фрагмент переложили в другой класс или удалили
            removed += 1
    return copied, removed


def fingerprint(split: dict, classes: list[str]) -> str:
    h = hashlib.sha1()
    for part in ("train", "val"):
        for c in classes:
            for p in sorted(split[part][c]):
                h.update(f"{part}/{c}/{p.name}:{p.stat().st_size}\n".encode("utf-8"))
    return h.hexdigest()


def main() -> int:
    _setup_console()
    cfg_all = common.load_yaml(common.CONFIG_DIR / "train.yaml")
    tasks = (cfg_all.get("cls") or {}).get("tasks") or {}
    ap = argparse.ArgumentParser(
        description="Обучение YOLO26-cls по фрагментам data/crops/<task>/<class>/ → runs/classify/<task>.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--task", required=True, choices=list(tasks), help="задача из train.yaml → cls.tasks")
    ap.add_argument("--model", help="стартовые веса (по умолчанию из train.yaml, yolo26s-cls.pt)")
    ap.add_argument("--epochs", type=int)
    ap.add_argument("--batch", type=int)
    ap.add_argument("--imgsz", type=int)
    ap.add_argument("--device", help="auto | mps | cpu | 0")
    ap.add_argument("--name", help="папка запуска runs/classify/<name> (по умолчанию = task)")
    ap.add_argument("--val-scenes", help="сцены для val через запятую (по умолчанию split.val_scenes)")
    ap.add_argument("--holdout-every", type=int, default=5,
                    help="если на val-сценах нет фрагментов: каждый N-й фрагмент класса → val (по умолчанию 5)")
    ap.add_argument("--force", action="store_true", help="обучить заново, даже если набор не изменился")
    args = ap.parse_args()
    if args.holdout_every < 2:
        ap.error("--holdout-every должен быть >= 2")

    task, name = args.task, args.name or args.task
    tcfg = dict(tasks[task])
    classes = [yaml_label(c) for c in tcfg.get("classes", [])]
    if len(classes) < 2:
        print(f"В train.yaml → cls.tasks.{task}.classes нужно минимум 2 класса, сейчас: {classes}")
        return 2
    val_scenes = ({s.strip() for s in args.val_scenes.split(",") if s.strip()} if args.val_scenes
                  else set(common.pipeline_config().get("split", {}).get("val_scenes", [])))

    crops_root = common.data_path("crops")
    src_dir = crops_root / task
    if not src_dir.exists():
        print(f"Нет {src_dir}. Сначала: python training/scripts/11_extract_crops.py --weights <детектор>")
        return 2
    extra = [p.name for p in src_dir.iterdir() if p.is_dir() and p.name not in classes and p.name != UNSORTED]
    if extra:
        print(f"Внимание: папки {extra} в {src_dir} не входят в классы {classes} и пропускаются.")

    split, notes = plan_split(src_dir, classes, val_scenes, args.holdout_every)
    for n in notes:
        print(f"Внимание: {n}")
    counts = {part: {c: len(split[part][c]) for c in classes} for part in ("train", "val")}
    no_train = [c for c in classes if counts["train"][c] == 0]
    if no_train:
        print(f"Остановлено: у классов {', '.join(no_train)} нет ни одного фрагмента для обучения.\n"
              f"Разложите фрагменты из {src_dir / UNSORTED} по папкам {', '.join(str(src_dir / c) for c in no_train)}\n"
              f"(фрагменты сцен {sorted(val_scenes)} идут только в val). Сейчас: train {counts['train']}, "
              f"val {counts['val']}.")
        return 2
    no_val = [c for c in classes if counts["val"][c] == 0]
    if no_val:
        print(f"Внимание: в val нет классов {', '.join(no_val)} — их точность оценена не будет.")

    dst = crops_root / f"{task}_split"
    copied, removed = sync_split(split, dst, classes)
    print(f"Набор {dst}: train {counts['train']}, val {counts['val']} (скопировано {copied}, убрано {removed})")

    model_name = args.model or tcfg.get("model") or "yolo26s-cls.pt"
    train_args = {k: v for k, v in tcfg.items() if k not in NON_ULTRALYTICS_KEYS}
    for key in ("epochs", "batch", "imgsz"):
        if getattr(args, key) is not None:
            train_args[key] = getattr(args, key)
    train_args["device"] = common.pick_device(str(args.device or train_args.get("device", "auto")))
    fp = fingerprint(split, classes)
    config = {"model": str(model_name), "classes": classes, "val_scenes": sorted(val_scenes),
              "train_args": {k: v for k, v in train_args.items() if k not in ("device", "workers")}}

    run_dir = common.repo_path("runs") / "classify" / name
    info_path = run_dir / "cls_info.json"
    best = run_dir / "weights" / "best.pt"
    if info_path.exists() and best.exists() and not args.force:
        prev = json.loads(info_path.read_text(encoding="utf-8"))
        if prev.get("fingerprint") == fp and prev.get("config") == config and prev.get("finished"):
            print(f"Классификатор {name} уже обучен на этом наборе: {best} (--force — заново).")
            if prev.get("top1") is not None:
                print(f"top1 на val = {prev['top1']:.3f}")
            return 0
        print("Набор фрагментов или параметры изменились — обучаю заново.")

    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    try:
        import ultralytics
        from ultralytics import YOLO
    except ImportError:
        print("Нет ultralytics. Активируйте окружение: source .venv-train/bin/activate")
        return 2
    try:
        from ultralytics.cfg import DEFAULT_CFG_DICT
        unknown = [k for k in train_args if k not in DEFAULT_CFG_DICT]
        for k in unknown:
            print(f"Внимание: параметр {k!r} не поддерживается Ultralytics — пропущен.")
            train_args.pop(k)
    except ImportError:
        pass

    if run_dir.exists() and any(run_dir.iterdir()):
        backup = run_dir.with_name(f"{name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        run_dir.rename(backup)
        print(f"Предыдущий запуск перенесён в {backup}")

    try:
        weights = wts.resolve_weight(model_name)
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}")
        return 2
    print(f"Обучение {name}: {model_name}, классы {classes}, {train_args}, ultralytics {ultralytics.__version__}")
    model = YOLO(weights)
    metrics = model.train(data=str(dst), project=str(run_dir.parent), name=name, exist_ok=True, **train_args)

    save_dir = Path(model.trainer.save_dir) if getattr(model, "trainer", None) else run_dir
    top1 = top5 = None
    try:
        top1, top5 = float(metrics.top1), float(metrics.top5)
    except Exception:
        pass
    finished = (save_dir / "weights" / "best.pt").exists()
    _write_json(save_dir / "cls_info.json", {
        "task": task, "name": name, "finished": finished, "fingerprint": fp, "config": config,
        "counts": counts, "notes": notes, "top1": top1, "top5": top5,
        "date": datetime.now().isoformat(timespec="seconds"), "ultralytics_version": ultralytics.__version__,
    })
    spec_file = crops_root / "crop_spec.json"
    if spec_file.exists():
        try:
            spec = (json.loads(spec_file.read_text(encoding="utf-8")).get("tasks") or {}).get(task)
            if spec:
                _write_json(save_dir / "crop_spec.json", spec)
        except (OSError, ValueError):
            pass

    print("\n=== Итог ===")
    print(f"Запуск: {save_dir}")
    print(f"Веса:   {save_dir / 'weights' / 'best.pt'}")
    print(f"Набор:  train {sum(counts['train'].values())}, val {sum(counts['val'].values())} фрагментов")
    if top1 is not None:
        print(f"Точность на val: top1 = {top1:.3f}")
    print(f"Экспорт в Core ML: python training/scripts/10_export_coreml.py --weights {save_dir / 'weights' / 'best.pt'}")
    if not finished:
        print("Ошибка: best.pt не создан.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
