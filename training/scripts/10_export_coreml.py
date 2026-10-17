"""Шаг C4/D: экспорт моделей YOLO26 в Core ML (.mlpackage, Neural Engine) и запись models/manifest.json.

Использование (только macOS; на других ОС скрипт сообщает об этом и выходит с кодом 0):
    python training/scripts/10_export_coreml.py --weights runs/detect/full/weights/best.pt
    python training/scripts/10_export_coreml.py --weights models/pretrained/yolo26l-pose.pt   # готовая поза
    python training/scripts/10_export_coreml.py --weights runs/classify/screen/weights/best.pt
    python training/scripts/10_export_coreml.py --weights runs/detect/full/weights/best.pt --imgsz 864 480

Параметры:
    --weights  .pt-файл; голое имя (yolo26l-pose.pt) ищется в models/pretrained/
    --imgsz    размер входа: одно число (квадрат) или «высота ширина»; по умолчанию из args.yaml
               запуска, иначе detect 864, pose 640, classify 224
    --task     auto (из модели) | detect | pose | classify
    --name     имя модели (по умолчанию имя запуска runs/<task>/<name>, иначе имя файла весов)
    --date     дата экспорта для manifest.json (по умолчанию сегодня, ГГГГ-ММ-ДД)

Экспорт: model.export(format="coreml", imgsz, nms=True для detect). В Ultralytics 8.4 это обычная
голова + NMS Apple в конвейере Core ML (end2end=false в manifest.json); у позы nms=False — голова
YOLO26 без NMS (end2end=true). Поле end2end в manifest.json берётся из метаданных .mlpackage.
Пакет копируется в models/<task>/<name>.mlpackage; в models/manifest.json добавляется или
обновляется запись: name, task, path, classes, imgsz, sha256 исходного .pt, source_run,
export_date, версии ultralytics и coremltools (для классификаторов — ещё параметры вырезания
фрагмента из data/crops/crop_spec.json, их должен повторять сервис).
Повторный запуск с тем же .pt (sha256) и imgsz пропускается; --force — экспортировать заново.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import json
import os
import shutil
from datetime import date
from pathlib import Path

from kassa_train import weights as wts

DEFAULT_IMGSZ = {"detect": 864, "pose": 640, "classify": 224, "segment": 864, "obb": 864}


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def manifest_path() -> Path:
    return common.repo_path("models") / "manifest.json"


def load_manifest() -> dict:
    path = manifest_path()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("models", [])
        return data
    return {"schema": 1, "models": []}


def save_manifest(data: dict) -> None:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["models"].sort(key=lambda e: (e.get("task", ""), e.get("name", "")))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def rel_to_repo(p: Path) -> str:
    try:
        return p.resolve().relative_to(common.REPO_ROOT).as_posix()
    except ValueError:
        return str(p.resolve())


def norm_imgsz(value) -> list[int]:
    """864 → [864, 864]; [864, 480] → [864, 480] (высота, ширина)."""
    if isinstance(value, (list, tuple)):
        vals = [int(v) for v in value]
        return [vals[0], vals[0]] if len(vals) == 1 else vals[:2]
    return [int(value), int(value)]


def exported_end2end(out_path: Path) -> bool | None:
    """end2end из метаданных экспортированного .mlpackage (Ultralytics пишет их в user_defined_metadata)."""
    try:
        import coremltools as ct
        meta = ct.models.MLModel(str(out_path), skip_model_load=True).user_defined_metadata
        value = meta.get("end2end")
    except Exception:
        return None
    if value is None:
        return None
    return str(value).strip().lower() == "true"


def model_extras(model, task: str, nms: bool, out_path: Path) -> dict:
    """Формат выхода экспортированной модели для сервиса.

    Ultralytics 8.4: nms=True — у detect обычная голова + NMS Apple в конвейере Core ML (end2end
    выключается); nms=False — голова YOLO26 без NMS (end2end), если модель её поддерживает.
    Голова исходного .pt об этом не говорит, поэтому берём метаданные .mlpackage.
    """
    extras: dict = {}
    net = getattr(model, "model", None)
    try:
        head = net.model[-1]
        if hasattr(head, "end2end"):
            e2e = exported_end2end(out_path)
            extras["end2end"] = e2e if e2e is not None else (bool(head.end2end) and not nms)
    except Exception:
        pass
    if task == "pose":
        kpt = getattr(net, "kpt_shape", None) or (getattr(net, "yaml", None) or {}).get("kpt_shape")
        if kpt is not None:
            extras["kpt_shape"] = [int(k) for k in kpt]
    return extras


def crop_spec_for(name: str, run_dir: Path | None) -> dict | None:
    """Геометрия фрагмента классификатора: копия в запуске (12_train_cls.py) или data/crops/crop_spec.json."""
    try:
        if run_dir is not None and (run_dir / "crop_spec.json").exists():
            return json.loads((run_dir / "crop_spec.json").read_text(encoding="utf-8"))
        path = common.data_path("crops") / "crop_spec.json"
        if path.exists():
            return (json.loads(path.read_text(encoding="utf-8")).get("tasks") or {}).get(name)
    except (OSError, ValueError):
        pass
    return None


def main() -> int:
    _setup_console()
    ap = argparse.ArgumentParser(
        description="Экспорт YOLO26 (.pt) в Core ML (.mlpackage) и запись models/manifest.json (только macOS).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--weights", required=True, help="веса .pt или имя из models/pretrained (yolo26l-pose.pt)")
    ap.add_argument("--imgsz", type=int, nargs="+", help="размер входа: N или H W")
    ap.add_argument("--task", default="auto", choices=["auto", "detect", "pose", "classify", "segment", "obb"])
    ap.add_argument("--name", help="имя модели в models/<task>/<name>.mlpackage")
    ap.add_argument("--date", default=date.today().isoformat(), help="дата экспорта для manifest.json")
    ap.add_argument("--force", action="store_true", help="экспортировать заново, даже если уже есть")
    args = ap.parse_args()

    if sys.platform != "darwin":
        print("Экспорт в Core ML выполняется только на macOS (нужен coremltools и Neural Engine). "
              f"Текущая ОС: {sys.platform} — шаг пропущен.")
        return 0

    try:
        weights = Path(wts.resolve_weight(args.weights))
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}")
        return 2

    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    try:
        import ultralytics
        from ultralytics import YOLO
    except ImportError:
        print("Нет ultralytics. Активируйте окружение: source .venv-train/bin/activate")
        return 2
    try:
        import coremltools
        ct_version = coremltools.__version__
    except ImportError:
        print("Нет coremltools. Установите: uv pip install 'coremltools>=9.0'")
        return 2

    model = YOLO(str(weights))  # при отсутствии файла Ultralytics скачает его в models/pretrained
    weights = weights.resolve()
    if not weights.is_file():
        print(f"Нет файла весов: {weights}")
        return 2
    task = model.task if args.task == "auto" else args.task
    run_dir = wts.run_dir_of(weights)
    run_args = wts.read_run_args(weights)
    imgsz = norm_imgsz(args.imgsz or run_args.get("imgsz") or DEFAULT_IMGSZ.get(task, 640))
    export_imgsz = imgsz[0] if imgsz[0] == imgsz[1] else imgsz
    name = args.name or (run_dir.name if run_dir else weights.stem)
    dest = common.repo_path("models") / task / f"{name}.mlpackage"
    sha = common.sha256_file(weights)

    manifest = load_manifest()
    prev = next((e for e in manifest["models"] if e.get("task") == task and e.get("name") == name), None)
    if (prev and not args.force and dest.exists() and prev.get("sha256_pt") == sha
            and prev.get("imgsz") == imgsz):
        print(f"Уже экспортировано: {dest} (sha256 и imgsz совпадают; --force — заново).")
        return 0

    nms = task == "detect"
    print(f"Экспорт {weights} → Core ML: task {task}, imgsz {imgsz}, nms={nms} ...")
    # half/int8 в Ultralytics 8.4 устарели (quantize); по умолчанию точность выбирает экспорт
    out = model.export(format="coreml", imgsz=export_imgsz, nms=nms)
    out_path = Path(str(out))
    if not out_path.exists():
        print(f"Ошибка: экспорт не создал {out_path}")
        return 1

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    if out_path.is_dir():
        shutil.copytree(out_path, tmp)
    else:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_path, tmp)
    if dest.exists():  # предыдущий экспорт этой же модели заменяется новым
        shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
    tmp.rename(dest)

    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    entry = {
        "name": name,
        "task": task,
        "path": rel_to_repo(dest),
        "classes": [names[i] for i in sorted(names)],
        "nc": len(names),
        "imgsz": imgsz,
        "nms": nms,
        "sha256_pt": sha,
        "source_weights": rel_to_repo(weights),
        "source_run": rel_to_repo(run_dir) if run_dir else None,
        "export_date": args.date,
        "ultralytics_version": ultralytics.__version__,
        "coremltools_version": ct_version,
        **model_extras(model, task, nms, out_path),
    }
    if task == "classify":
        crop = crop_spec_for(name, run_dir)
        if crop:
            entry["crop"] = crop
    manifest["models"] = [e for e in manifest["models"] if not (e.get("task") == task and e.get("name") == name)]
    manifest["models"].append(entry)
    save_manifest(manifest)

    print("\n=== Итог ===")
    print(f"Core ML: {dest}")
    print(f"Манифест: {manifest_path()} ({len(manifest['models'])} моделей)")
    print(f"Классы ({len(names)}): {', '.join(entry['classes'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
