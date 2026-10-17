"""Шаг C3: оценка детектора — mAP и точность по каждому классу на сцене, которой не было в обучении.

Использование (из корня репозитория):
    python training/scripts/09_eval_detect.py --weights runs/detect/full/weights/best.pt
    python training/scripts/09_eval_detect.py --weights runs/detect/full/weights/best.pt --loso-root
    python training/scripts/09_eval_detect.py --weights runs/detect/pseudo/weights/best.pt \
        --data data/yolo/reviewed/dataset.yaml --split val

--data по умолчанию — dataset.yaml из args.yaml запуска, которому принадлежат веса;
--imgsz — тоже из args.yaml (иначе 864).

Результат (в папке запуска runs/detect/<name>/; для весов вне запуска — runs/detect/eval_<имя>/):
    eval_<split>.json, eval_<split>.md — mAP50, mAP50-95, P/R и AP50 по классам;
    классы с AP50 < 0.5 выделены — их разметку и данные стоит усилить в первую очередь;
    eval_<split>_plots/ — графики Ultralytics (PR-кривые, матрица ошибок).

--loso-root [ПАПКА] (по умолчанию data/yolo) дополнительно оценивает те же веса на каждом
наборе <ПАПКА>/*_loso_*/dataset.yaml (их собирает 07_build_dataset.py --loso) →
eval_loso.json/.md. Честная оценка — только для фолда, чья сцена не участвовала в обучении
этих весов (он помечен); остальные фолды показывают качество на уже виденных сценах.

Повторный запуск с теми же весами (sha256), данными и imgsz пропускается; --force — пересчитать.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from kassa_train import weights as wts

WEAK_AP50 = 0.5


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _f(x) -> float | None:
    try:
        return None if x is None else round(float(x), 4)
    except (TypeError, ValueError):
        return None


def _fmt(x) -> str:
    return "—" if x is None else f"{x:.3f}"


def heldout_scenes(train_data: str | None) -> list[str]:
    """Сцены, которых не было в обучении: суффикс *_loso_<S>, иначе val_scenes из stats.json набора
    (07_build_dataset.py, учитывает --val-scenes), иначе split.val_scenes из pipeline.yaml."""
    if train_data:
        parent = Path(train_data).parent.name
        if "_loso_" in parent:
            return [parent.split("_loso_", 1)[1]]
        try:
            stats = json.loads((Path(train_data).parent / "stats.json").read_text(encoding="utf-8"))
            if stats.get("val_scenes") and not stats.get("val_is_train"):
                return [str(s) for s in stats["val_scenes"]]
        except (OSError, ValueError, AttributeError):
            pass
    return list(common.pipeline_config().get("split", {}).get("val_scenes", []))


# ---------------------------------------------------------------- оценка

def evaluate(model, data_yaml: Path, split: str, imgsz, batch: int, device: str,
             project: Path, plots_name: str) -> dict:
    """model.val() → словарь с общими метриками и метриками по классам."""
    m = model.val(data=str(data_yaml), split=split, imgsz=imgsz, batch=batch, device=device,
                  project=str(project), name=plots_name, exist_ok=True, plots=True, verbose=False)
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    box = m.box
    ap_index = [int(i) for i in getattr(box, "ap_class_index", [])]

    instances: dict[int, int] = {}
    nt = getattr(m, "nt_per_class", None)
    if nt is not None:
        instances = {i: int(n) for i, n in enumerate(nt)}

    per_class = []
    for cid in sorted(names):
        entry = {"id": int(cid), "name": names[cid], "instances": instances.get(int(cid)),
                 "p": None, "r": None, "ap50": None, "ap50_95": None, "weak": False, "in_split": False}
        if cid in ap_index:
            p, r, ap50, ap = box.class_result(ap_index.index(cid))
            entry.update(p=_f(p), r=_f(r), ap50=_f(ap50), ap50_95=_f(ap), in_split=True)
            entry["weak"] = entry["ap50"] is not None and entry["ap50"] < WEAK_AP50
        elif instances.get(int(cid)):
            entry["in_split"] = True
        per_class.append(entry)

    return {
        "map50": _f(box.map50),
        "map50_95": _f(box.map),
        "precision": _f(getattr(box, "mp", None)),
        "recall": _f(getattr(box, "mr", None)),
        "per_class": per_class,
        "weak_classes": [c["name"] for c in per_class if c["weak"]],
        "missing_classes": [c["name"] for c in per_class if not c["in_split"]],
        "plots_dir": str(project / plots_name),
    }


def render_md(title: str, meta: dict, res: dict) -> str:
    lines = [f"# {title}", ""]
    for k, v in meta.items():
        lines.append(f"- {k}: `{v}`")
    lines += ["", f"**mAP50 = {_fmt(res['map50'])}, mAP50-95 = {_fmt(res['map50_95'])}**, "
                  f"P = {_fmt(res['precision'])}, R = {_fmt(res['recall'])}", "",
              "| id | класс | объектов | P | R | AP50 | AP50-95 | |",
              "|---:|---|---:|---:|---:|---:|---:|---|"]
    for c in res["per_class"]:
        ap50 = _fmt(c["ap50"])
        note = ""
        if c["weak"]:
            ap50, note = f"**{ap50}**", f"**СЛАБО: AP50 < {WEAK_AP50}**"
        elif not c["in_split"]:
            note = "нет в выборке"
        inst = "—" if c["instances"] is None else str(c["instances"])
        lines.append(f"| {c['id']} | {c['name']} | {inst} | {_fmt(c['p'])} | {_fmt(c['r'])} | "
                     f"{ap50} | {_fmt(c['ap50_95'])} | {note} |")
    lines.append("")
    if res["weak_classes"]:
        lines.append(f"**Слабые классы (AP50 < {WEAK_AP50}):** {', '.join(res['weak_classes'])}. "
                     "Проверьте их разметку (configs/classes.yaml → review) и добавьте примеров.")
    else:
        lines.append(f"Классов с AP50 < {WEAK_AP50} нет.")
    if res["missing_classes"]:
        lines.append("")
        lines.append(f"Нет в выборке (оценить нельзя): {', '.join(res['missing_classes'])}.")
    lines.append("")
    return "\n".join(lines)


def render_loso_md(meta: dict, folds: list[dict], class_names: list[str]) -> str:
    lines = ["# Оценка по фолдам leave-one-scene-out", ""]
    for k, v in meta.items():
        lines.append(f"- {k}: `{v}`")
    lines += ["", "Честная оценка — только у фолда со сценой, которой не было в обучении этих весов "
                  "(колонка «сцена новая»). Остальные фолды — качество на уже виденных сценах.", "",
              "| фолд | сцена | сцена новая | mAP50 | mAP50-95 | слабые классы (AP50 < 0.5) |",
              "|---|---|---|---:|---:|---|"]
    for f in folds:
        lines.append(f"| {f['fold']} | {f['scene']} | {'да' if f['unseen'] else 'нет'} | {_fmt(f['map50'])} | "
                     f"{_fmt(f['map50_95'])} | {', '.join(f['weak_classes']) or '—'} |")
    lines += ["", "AP50 по классам:", "",
              "| класс | " + " | ".join(f["scene"] for f in folds) + " |",
              "|---|" + "---:|" * len(folds)]
    for name in class_names:
        cells = []
        for f in folds:
            c = next((x for x in f["per_class"] if x["name"] == name), None)
            v = None if c is None else c["ap50"]
            cells.append(f"**{_fmt(v)}**" if v is not None and v < WEAK_AP50 else _fmt(v))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def print_summary(res: dict, label: str) -> None:
    print(f"{label}: mAP50 = {_fmt(res.get('map50'))}, mAP50-95 = {_fmt(res.get('map50_95'))}")
    weak = res.get("weak_classes") or []
    if weak:
        print(f"  слабые классы (AP50 < {WEAK_AP50}): {', '.join(weak)}")
    missing = res.get("missing_classes") or []
    if missing:
        print(f"  нет в выборке: {', '.join(missing)}")


# ---------------------------------------------------------------- main

def main() -> int:
    _setup_console()
    ap = argparse.ArgumentParser(
        description="Оценка детектора: mAP и AP50/P/R по классам → eval_<split>.json/.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--weights", required=True, help="веса .pt (обычно runs/detect/<run>/weights/best.pt)")
    ap.add_argument("--data", help="dataset.yaml (по умолчанию — из args.yaml запуска)")
    ap.add_argument("--split", default="val", help="split набора: val (по умолчанию) | test | train")
    ap.add_argument("--imgsz", type=int, help="размер входа (по умолчанию из args.yaml запуска, иначе 864)")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="auto", help="auto | mps | cpu | 0")
    ap.add_argument("--loso-root", nargs="?", const=str(common.data_path("yolo")), default=None,
                    help="оценить и на всех <папка>/*_loso_*/dataset.yaml (по умолчанию data/yolo)")
    ap.add_argument("--force", action="store_true", help="пересчитать, даже если результат уже есть")
    args = ap.parse_args()

    try:
        weights = Path(wts.resolve_weight(args.weights, download_to_pretrained=False))
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}")
        return 2
    if not weights.is_file():
        print(f"Нет файла весов: {weights}")
        return 2
    run_args = wts.read_run_args(weights)
    run_dir = wts.run_dir_of(weights) or (common.repo_path("runs") / "detect" / f"eval_{weights.stem}")
    run_dir.mkdir(parents=True, exist_ok=True)

    data = args.data or run_args.get("data")
    if not data:
        print("Не задан --data, и у весов нет args.yaml запуска с полем data.")
        return 2
    data_yaml = Path(data)
    if not data_yaml.is_absolute():
        data_yaml = data_yaml.resolve() if data_yaml.exists() else (common.REPO_ROOT / data_yaml)
    if not data_yaml.exists():
        print(f"Нет датасета {data_yaml}. Соберите его: python training/scripts/07_build_dataset.py")
        return 2
    imgsz = args.imgsz or run_args.get("imgsz") or 864
    device = common.pick_device(args.device)

    sha = common.sha256_file(weights)
    out_json = run_dir / f"eval_{args.split}.json"
    out_md = run_dir / f"eval_{args.split}.md"
    loso_json = run_dir / "eval_loso.json"
    loso_dirs: list[Path] = []
    stats_file = data_yaml.parent / "stats.json"
    if stats_file.exists():
        try:
            if json.loads(stats_file.read_text(encoding="utf-8")).get("val_is_train"):
                print(f"Внимание: в {data_yaml.parent.name} val = train (своих кадров для проверки нет) — "
                      "метрики завышены.")
        except (OSError, ValueError):
            pass
    if args.loso_root:
        loso_root = Path(args.loso_root)
        loso_dirs = sorted(p.parent for p in loso_root.glob("*_loso_*/dataset.yaml"))
        base = data_yaml.parent.name.split("_loso_", 1)[0]
        same_base = [d for d in loso_dirs if d.name.startswith(f"{base}_loso_")]
        loso_dirs = same_base or loso_dirs  # фолды того же набора, что и обучение (reviewed → reviewed_loso_*)
        if not loso_dirs:
            print(f"Внимание: в {loso_root} нет наборов *_loso_*/dataset.yaml "
                  "(соберите: 07_build_dataset.py --source reviewed --loso). LOSO пропущен.")

    def cached(path: Path, extra: dict) -> dict | None:
        if args.force or not path.exists():
            return None
        prev = json.loads(path.read_text(encoding="utf-8"))
        same = prev.get("weights_sha256") == sha and str(prev.get("imgsz")) == str(imgsz)
        return prev if same and all(prev.get(k) == v for k, v in extra.items()) else None

    main_prev = cached(out_json, {"data": str(data_yaml), "split": args.split})
    loso_prev = cached(loso_json, {"folds_dirs": [str(p) for p in loso_dirs]}) if loso_dirs else None
    need_main = main_prev is None
    need_loso = bool(loso_dirs) and loso_prev is None

    model = None
    ul_version = None
    if need_main or need_loso:
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        try:
            import ultralytics
            from ultralytics import YOLO
        except ImportError:
            print("Нет ultralytics. Активируйте окружение: source .venv-train/bin/activate")
            return 2
        model = YOLO(str(weights))
        ul_version = ultralytics.__version__

    meta_base = {
        "веса": weights, "sha256": sha[:16], "imgsz": imgsz,
        "дата": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # --- основная оценка
    if need_main:
        print(f"Оценка {weights} на {data_yaml} (split {args.split}, imgsz {imgsz}, {device})...")
        res = evaluate(model, data_yaml, args.split, imgsz, args.batch, device, run_dir, f"eval_{args.split}_plots")
        record = {"weights": str(weights), "weights_sha256": sha, "data": str(data_yaml), "split": args.split,
                  "imgsz": imgsz, "date": datetime.now().isoformat(timespec="seconds"),
                  "ultralytics_version": ul_version, "heldout_scenes": heldout_scenes(run_args.get("data")),
                  **res}
        _write_json(out_json, record)
        meta = dict(meta_base, данные=data_yaml, split=args.split)
        out_md.write_text(render_md(f"Оценка детектора: {run_dir.name}", meta, res), encoding="utf-8")
        main_prev = record
    else:
        print(f"Оценка уже есть ({out_json}), пропускаю (--force — пересчитать).")
    print_summary(main_prev, f"split {args.split}")

    # --- LOSO
    if need_loso:
        unseen = set(heldout_scenes(run_args.get("data")))
        folds = []
        for d in loso_dirs:
            scene = d.name.split("_loso_", 1)[1]
            print(f"LOSO {d.name}: оценка на сцене {scene}...")
            res = evaluate(model, d / "dataset.yaml", "val", imgsz, args.batch, device, run_dir,
                           f"eval_loso_{scene}_plots")
            folds.append({"fold": d.name, "scene": scene, "unseen": scene in unseen, **res})
        loso_prev = {"weights": str(weights), "weights_sha256": sha, "imgsz": imgsz,
                     "folds_dirs": [str(p) for p in loso_dirs],
                     "date": datetime.now().isoformat(timespec="seconds"),
                     "ultralytics_version": ul_version, "folds": folds}
        _write_json(loso_json, loso_prev)
        names = [c["name"] for c in folds[0]["per_class"]] if folds else []
        (run_dir / "eval_loso.md").write_text(render_loso_md(meta_base, folds, names), encoding="utf-8")
    elif loso_dirs:
        print(f"LOSO-оценка уже есть ({loso_json}), пропускаю.")
    if loso_prev:
        for f in loso_prev["folds"]:
            print_summary(f, f"LOSO {f['scene']}{' (новая сцена)' if f['unseen'] else ''}")

    print("\n=== Итог ===")
    print(f"Отчёт: {out_md}")
    if loso_prev:
        print(f"LOSO:  {run_dir / 'eval_loso.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
