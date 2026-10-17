"""Шаг A5/A6/C2: обучение детектора YOLO26 по профилю из training/configs/train.yaml.

Использование (из корня репозитория):
    python training/scripts/08_train_detect.py --profile smoke        # 5-10 мин: проверка MPS и замер скорости
    python training/scripts/08_train_detect.py --profile calib_full   # 10-20 мин: замер в настройках full (время, память)
    python training/scripts/08_train_detect.py --profile pseudo       # базовая модель на автоматической разметке
    python training/scripts/08_train_detect.py --profile full         # основная модель (YOLO26l, 864)
    python training/scripts/08_train_detect.py --profile full --batch 4            # мало памяти
    python training/scripts/08_train_detect.py --profile full \
        --model runs/detect/ext/weights/best.pt --name full_ft                      # дообучение другой модели
    python training/scripts/08_train_detect.py --profile smoke --show-timing        # только показать прогноз

Профили (smoke | calib_full | pseudo | full | full_m) берутся из train.yaml; ключи `model` и `dataset`
профиля — не параметры Ultralytics: model — стартовые веса, dataset — имя набора
data/yolo/<dataset>/dataset.yaml (его собирает 07_build_dataset.py). Флаги --data, --model,
--epochs, --batch, --imgsz, --device переопределяют профиль; --name (по умолчанию = профиль)
задаёт папку запуска runs/detect/<name>/.

Результат:
    runs/detect/<name>/weights/best.pt, last.pt, args.yaml, results.csv, графики;
    runs/detect/<name>/timing.json — секунды на эпоху, ETA этого запуска и прогноз времени
    для остальных профилей: {sec_per_epoch_avg, epochs_done, eta_this_run_sec,
    projected: {pseudo_sec, full_sec, full_m_sec}, ...}. Время обучающей части эпохи делится на
    ожидание загрузчика (на MPS/CPU Ultralytics грузит кадры в основном потоке, workers=0; это
    время растёт только с числом кадров) и счёт сети (масштабируется по числу кадров, imgsz^2 и
    размеру модели: n=0.15, s=0.35, m=0.8, l=1.0, x=1.8). Прогноз печатается после 1-й эпохи.
    smoke (n@640) даёт грубый прогноз; для full/full_m точнее замер calib_full — он идёт ровно
    в настройках full (YOLO26l, 864, freeze=10) на 15 % кадров.

Память на Mac: PYTORCH_MPS_HIGH_WATERMARK_RATIO по умолчанию 1.0 (если не задан) — тогда при
нехватке памяти MPS выдаёт «out of memory», и Ultralytics в первой эпохе сам уменьшает batch
вдвое, а не уходит в swap. Во время первой эпохи смотрите «Мониторинг системы» → Память → swap.

Повторный запуск пропускает завершённое обучение с теми же параметрами; прерванное
(есть last.pt) продолжается с места остановки. --force — обучить заново.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import os

# До первого import torch (его делает уже common.pick_device): PyTorch читает эти переменные при загрузке.
# Без fallback операции, которых нет в MPS, падают; без watermark=1.0 MPS на 16 ГБ уходит в swap вместо OOM.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "1.0")

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from kassa_train import weights as wts

NON_ULTRALYTICS_KEYS = {"model", "dataset"}
PROBE_PROFILES = {"smoke", "calib_full"}   # замеры: для них прогноз не считается
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
MIN_RAM_GB = 6.0


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------- конфигурация

def load_train_config() -> dict:
    return common.load_yaml(common.CONFIG_DIR / "train.yaml")


def dataset_yaml_for(dataset: str) -> Path:
    return common.data_path("yolo") / dataset / "dataset.yaml"


def run_dir_for(name: str) -> Path:
    return common.repo_path("runs") / "detect" / name


def imgsz_area(imgsz) -> int:
    if isinstance(imgsz, (list, tuple)):
        if len(imgsz) == 1:
            return int(imgsz[0]) ** 2
        return int(imgsz[0]) * int(imgsz[1])
    return int(imgsz) ** 2


def count_split_images(dataset_yaml: Path, split: str) -> int | None:
    """Число кадров в split набора Ultralytics (папки или .txt-списки); None — не удалось."""
    if not dataset_yaml.exists():
        return None
    try:
        d = common.load_yaml(dataset_yaml) or {}
    except Exception:
        return None
    entries = d.get(split)
    if not entries:
        return None
    if isinstance(entries, str):
        entries = [entries]
    root = Path(d["path"]) if d.get("path") else dataset_yaml.parent
    if not root.is_absolute():
        root = dataset_yaml.parent / root
    total = 0
    for entry in entries:
        p = Path(entry)
        if not p.is_absolute():
            p = root / p
        if p.is_dir():
            total += sum(1 for f in p.rglob("*") if f.suffix.lower() in IMG_EXTS)
        elif p.is_file() and p.suffix.lower() == ".txt":
            total += sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
        else:
            return None
    return total


def config_key(config: dict | None) -> str:
    """Параметры, от которых зависит модель (устройство и число воркеров не считаются)."""
    if not config:
        return ""
    c = dict(config)
    c["train_args"] = {k: v for k, v in (c.get("train_args") or {}).items() if k not in ("device", "workers")}
    return json.dumps(c, sort_keys=True, ensure_ascii=False)


def filter_ultralytics_args(args: dict) -> dict:
    """Отбрасывает ключи, которых нет в настройках Ultralytics (с предупреждением)."""
    try:
        from ultralytics.cfg import DEFAULT_CFG_DICT
    except ImportError:
        return args
    unknown = [k for k in args if k not in DEFAULT_CFG_DICT]
    for k in unknown:
        print(f"Внимание: параметр {k!r} не поддерживается Ultralytics — пропущен.")
    return {k: v for k, v in args.items() if k not in unknown}


def available_ram_gb() -> tuple[float, float] | None:
    try:
        import psutil
    except ImportError:
        return None
    vm = psutil.virtual_memory()
    return vm.available / 1024 ** 3, vm.total / 1024 ** 3


# ---------------------------------------------------------------- время и прогноз

def fmt_duration(sec: float | None) -> str:
    if sec is None:
        return "?"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} ч {m:02d} мин"
    if m:
        return f"{m} мин {s:02d} с"
    return f"{s} с"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def project_profiles(measured: dict, cfg: dict) -> tuple[dict, dict]:
    """Прогноз полного времени для каждого профиля (кроме замеров smoke/calib_full) по этому запуску.

    Обучающая часть эпохи = ожидание загрузчика + счёт сети. Загрузчик (декодирование JPEG,
    мозаика) не зависит от размера модели и масштабируется только по числу train-кадров; счёт —
    по числу кадров, imgsz^2 и коэффициенту размера модели. Валидация — по числу val-кадров,
    imgsz^2 и размеру модели. freeze и мозаика (close_mosaic) не моделируются — поэтому для
    full/full_m точнее всего замер calib_full, сделанный в их настройках.
    """
    m_train_sec = measured["train_sec_per_epoch"]
    m_data_sec = min(m_train_sec, max(0.0, measured.get("data_sec_per_epoch") or 0.0))
    m_compute_sec = m_train_sec - m_data_sec
    m_val_sec = max(0.0, measured["sec_per_epoch"] - m_train_sec)
    m_train_imgs = measured.get("train_images") or 0
    m_val_imgs = measured.get("val_images") or 0
    m_area = imgsz_area(measured["imgsz"])
    m_factor = wts.size_factor(measured.get("scale"))
    fallback_train = measured.get("dataset_train_images") or m_train_imgs
    fallback_val = m_val_imgs

    projected, details = {}, {}
    m_freeze = measured.get("freeze")
    for pname, prof in cfg.get("profiles", {}).items():
        if pname in PROBE_PROFILES:
            continue
        dyaml = dataset_yaml_for(prof.get("dataset", pname))
        n_train = count_split_images(dyaml, "train")
        n_val = count_split_images(dyaml, "val")
        assumed = n_train is None
        if n_train is None:
            n_train = fallback_train
        if n_val is None:
            n_val = fallback_val
        n_train_eff = n_train * float(prof.get("fraction", 1.0))
        scale = wts.model_scale(prof.get("model"))
        k = (imgsz_area(prof.get("imgsz", 640)) / m_area) * (wts.size_factor(scale) / m_factor)
        count_ratio = n_train_eff / m_train_imgs if m_train_imgs > 0 else 1.0
        train_part = (m_compute_sec * k + m_data_sec) * count_ratio
        if m_val_imgs > 0:
            val_part = m_val_sec * (n_val / m_val_imgs) * k
        else:
            val_part = m_val_sec * k
        epoch_sec = train_part + val_part
        epochs = int(prof.get("epochs", 1))
        projected[f"{pname}_sec"] = round(epoch_sec * epochs)
        # замер сопоставим: тот же imgsz и freeze, модель того же класса тяжести (m/l/x, а не n/s)
        comparable = (imgsz_area(prof.get("imgsz", 640)) == m_area
                      and (prof.get("freeze") or None) == (m_freeze or None)
                      and measured.get("scale") in ("m", "l", "x"))
        details[pname] = {
            "epochs": epochs,
            "sec_per_epoch": round(epoch_sec, 1),
            "model": prof.get("model"),
            "scale": scale,
            "imgsz": prof.get("imgsz"),
            "freeze": prof.get("freeze"),
            "train_images": n_train,
            "val_images": n_val,
            "images_assumed": assumed,
            "comparable_measurement": comparable,
            "dataset_yaml": str(dyaml),
        }
    return projected, details


def print_projection(timing: dict) -> None:
    data_avg = timing.get("data_sec_per_epoch_avg")
    data_note = f", из них загрузка кадров {fmt_duration(data_avg)}" if data_avg is not None else ""
    print(f"Скорость запуска {timing.get('name')}: {fmt_duration(timing.get('sec_per_epoch_steady'))} на эпоху "
          f"(обучение {fmt_duration(timing.get('train_sec_per_epoch_avg'))}{data_note}, "
          f"валидация {fmt_duration(timing.get('val_sec_per_epoch_avg'))}); "
          f"эпох {timing.get('epochs_done')}/{timing.get('epochs_total')}, "
          f"осталось ~{fmt_duration(timing.get('eta_this_run_sec'))}.")
    details = timing.get("projection_details", {})
    if not details:
        return
    print("Прогноз полного обучения по этому замеру (точность ~±30 %; patience может остановить раньше):")
    rough = []
    for pname, d in details.items():
        sec = timing.get("projected", {}).get(f"{pname}_sec")
        note = " (число кадров предположено по текущему набору — датасета ещё нет)" if d.get("images_assumed") else ""
        print(f"  {pname:<7} ~{fmt_duration(sec):<13} = {d['epochs']} эп. x {fmt_duration(d['sec_per_epoch'])}; "
              f"{d.get('model')}, imgsz {d.get('imgsz')}, {d.get('train_images')} кадров train{note}")
        if d.get("comparable_measurement") is False and d.get("scale") in ("l", "m", "x"):
            rough.append(pname)
    if rough:
        print(f"  Для {', '.join(rough)} это грубая оценка (замер на другой модели/размере, freeze не учтён); "
              "точнее: python training/scripts/08_train_detect.py --profile calib_full")
    batch_used = (timing.get("measured") or {}).get("batch")
    batch_cfg = ((timing.get("config") or {}).get("train_args") or {}).get("batch")
    if batch_used and batch_cfg and int(batch_used) < int(batch_cfg):
        print(f"  Внимание: не хватило памяти — Ultralytics уменьшил batch {batch_cfg} → {batch_used}. "
              f"Для full/full_m поставьте batch: {batch_used} в configs/train.yaml (или --batch {batch_used}).")
    if timing.get("epochs_done", 0) <= 1:
        print("  Первая эпоха обычно медленнее (прогрев MPS, кэш данных); после следующих эпох "
              "прогноз в timing.json уточняется.")


class TimingRecorder:
    """Колбэки Ultralytics: замер секунд на эпоху и запись timing.json."""

    def __init__(self, name: str, profile: str, config: dict, cfg: dict, data_yaml: Path):
        self.name = name
        self.profile = profile
        self.config = config
        self.cfg = cfg
        self.data_yaml = data_yaml
        self.t_epoch_start: float | None = None
        self.t_first_start: float | None = None
        self.t_batch_end: float | None = None
        self.data_wait = 0.0
        self.data_secs: list[float] = []
        self.train_secs: list[float] = []
        self.full_secs: list[float] = []
        self.run_dir: Path | None = None
        self.measured_static: dict | None = None
        self.last: dict = {}

    # --- колбэки
    def on_train_epoch_start(self, trainer) -> None:
        self.t_epoch_start = time.perf_counter()
        self.t_batch_end = self.t_epoch_start
        self.data_wait = 0.0
        if self.t_first_start is None:
            self.t_first_start = self.t_epoch_start

    def on_train_batch_start(self, trainer) -> None:
        # от конца прошлого шага до начала следующего Ultralytics ждёт загрузчик (на MPS/CPU — в этом же потоке)
        if self.t_batch_end is not None:
            self.data_wait += time.perf_counter() - self.t_batch_end

    def on_train_batch_end(self, trainer) -> None:
        self.t_batch_end = time.perf_counter()

    def on_train_epoch_end(self, trainer) -> None:
        if self.t_epoch_start is not None:
            self.train_secs.append(time.perf_counter() - self.t_epoch_start)
            self.data_secs.append(self.data_wait)
        self.t_batch_end = None

    def on_fit_epoch_end(self, trainer) -> None:
        # Ultralytics вызывает on_fit_epoch_end ещё раз в final_eval (проверка best.pt после
        # обучения) — без нового on_train_epoch_start. Этот вызов не эпоха: не считаем его.
        if self.t_epoch_start is None:
            return
        self.full_secs.append(time.perf_counter() - self.t_epoch_start)
        self.t_epoch_start = None
        self.run_dir = Path(trainer.save_dir)
        try:
            timing = self.build(trainer, finished=False)
            _write_json(self.run_dir / "timing.json", timing)
            if len(self.full_secs) == 1:
                print()
                print_projection(timing)
                print()
        except Exception as exc:  # замер не должен ронять обучение
            print(f"Внимание: не удалось записать timing.json: {exc}")

    # --- расчёт
    def _static(self, trainer) -> dict:
        if self.measured_static is not None:
            return self.measured_static
        args = trainer.args
        train_imgs = val_imgs = None
        try:
            train_imgs = len(trainer.train_loader.dataset)
        except Exception:
            pass
        try:
            val_imgs = len(trainer.test_loader.dataset)
        except Exception:
            pass
        dataset_train = count_split_images(self.data_yaml, "train")
        if train_imgs is None and dataset_train is not None:
            train_imgs = round(dataset_train * float(getattr(args, "fraction", 1.0) or 1.0))
        if val_imgs is None:
            val_imgs = count_split_images(self.data_yaml, "val")
        scale = None
        try:
            net = trainer.model.module if hasattr(trainer.model, "module") else trainer.model
            scale = (getattr(net, "yaml", None) or {}).get("scale")
        except Exception:
            pass
        scale = scale or wts.scale_from_weights(self.config["model"]) or wts.scale_from_weights(str(args.model))
        if scale is None:
            print("Внимание: размер модели не определён, для прогноза принят l (коэффициент 1.0).")
        self.measured_static = {
            "model": self.config["model"],
            "scale": scale or "l",
            "imgsz": args.imgsz,
            "batch": args.batch,
            "freeze": getattr(args, "freeze", None),
            "fraction": getattr(args, "fraction", 1.0),
            "train_images": train_imgs,
            "val_images": val_imgs,
            "dataset_train_images": dataset_train,
        }
        return self.measured_static

    def build(self, trainer, finished: bool) -> dict:
        st = self._static(trainer)
        used_batch = getattr(trainer, "batch_size", None)  # после OOM в 1-й эпохе Ultralytics уменьшает batch
        if isinstance(used_batch, int) and used_batch > 0:
            st["batch"] = used_batch
        full = self.full_secs or [0.0]
        train = self.train_secs or full
        data = self.data_secs or [0.0]
        steady_full = statistics.mean(full[1:]) if len(full) >= 2 else full[0]
        steady_train = statistics.mean(train[1:]) if len(train) >= 2 else train[0]
        steady_data = statistics.mean(data[1:]) if len(data) >= 2 else data[0]
        epochs_total = int(getattr(trainer, "epochs", 0) or self.config["train_args"].get("epochs", 0))
        epochs_done = int(getattr(trainer, "epoch", len(full) - 1)) + 1
        remaining = 0 if finished else max(0, epochs_total - epochs_done)
        measured = dict(st, sec_per_epoch=steady_full, train_sec_per_epoch=steady_train,
                        data_sec_per_epoch=steady_data)
        projected, details = project_profiles(measured, self.cfg)
        timing = {
            "name": self.name,
            "profile": self.profile,
            "finished": finished,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "sec_per_epoch_avg": round(statistics.mean(full), 2),
            "sec_per_epoch_steady": round(steady_full, 2),
            "train_sec_per_epoch_avg": round(statistics.mean(train), 2),
            "data_sec_per_epoch_avg": round(statistics.mean(data), 2),
            "data_sec_per_epoch_steady": round(steady_data, 2),
            "val_sec_per_epoch_avg": round(max(0.0, steady_full - steady_train), 2),
            "epoch_secs": [round(s, 2) for s in full],
            "epochs_done": epochs_done,
            "epochs_measured": len(self.full_secs),
            "epochs_total": epochs_total,
            "elapsed_sec": round(time.perf_counter() - self.t_first_start, 1) if self.t_first_start else 0.0,
            "eta_this_run_sec": round(remaining * steady_full),
            "measured": st,
            "projected": projected,
            "projection_details": details,
            "config": self.config,
        }
        self.last = timing
        return timing


def show_timing(name: str) -> int:
    path = run_dir_for(name) / "timing.json"
    if not path.exists():
        print(f"Нет {path}: сначала запустите обучение (--profile {name}).")
        return 1
    timing = json.loads(path.read_text(encoding="utf-8"))
    print(f"Замер из {path}:")
    print_projection(timing)
    return 0


# ---------------------------------------------------------------- main

def build_parser(profiles: list[str]) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Обучение детектора YOLO26 по профилю train.yaml; пишет runs/detect/<name>/timing.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--profile", required=True, choices=profiles, help="профиль из configs/train.yaml")
    ap.add_argument("--data", help="dataset.yaml (по умолчанию data/yolo/<profile.dataset>/dataset.yaml)")
    ap.add_argument("--model", help="стартовые веса: yolo26l.pt или runs/detect/<run>/weights/best.pt")
    ap.add_argument("--epochs", type=int)
    ap.add_argument("--batch", type=int)
    ap.add_argument("--imgsz", type=int)
    ap.add_argument("--device", help="auto | mps | cpu | 0 (по умолчанию из профиля)")
    ap.add_argument("--name", help="папка запуска runs/detect/<name> (по умолчанию = профиль)")
    ap.add_argument("--force", action="store_true", help="обучить заново, даже если запуск уже завершён")
    ap.add_argument("--no-resume", action="store_true", help="не продолжать прерванный запуск, начать сначала")
    ap.add_argument("--show-timing", action="store_true", help="только показать timing.json запуска и выйти")
    return ap


def main() -> int:
    _setup_console()
    cfg = load_train_config()
    profiles = cfg.get("profiles", {})
    args = build_parser(list(profiles)).parse_args()
    name = args.name or args.profile
    run_dir = run_dir_for(name)

    if args.show_timing:
        return show_timing(name)

    prof = dict(profiles[args.profile])
    model_name = args.model or prof.get("model")
    if not model_name:
        print(f"В профиле {args.profile} нет model, укажите --model.")
        return 2
    dataset = prof.get("dataset", args.profile)
    data_yaml = Path(args.data).resolve() if args.data else dataset_yaml_for(dataset)
    if not data_yaml.exists():
        print(f"Нет датасета {data_yaml}.\n"
              f"Сначала соберите его: python training/scripts/07_build_dataset.py --source {dataset}")
        return 2

    train_args = {k: v for k, v in prof.items() if k not in NON_ULTRALYTICS_KEYS}
    for key in ("epochs", "batch", "imgsz"):
        if getattr(args, key) is not None:
            train_args[key] = getattr(args, key)
    train_args["device"] = common.pick_device(str(args.device or train_args.get("device", "auto")))
    config = {"model": str(model_name), "data": str(data_yaml), "train_args": train_args}

    # --- идемпотентность
    timing_path = run_dir / "timing.json"
    best = run_dir / "weights" / "best.pt"
    last = run_dir / "weights" / "last.pt"
    prev = json.loads(timing_path.read_text(encoding="utf-8")) if timing_path.exists() else None
    same_config = bool(prev) and config_key(prev.get("config")) == config_key(config)
    if prev and prev.get("finished") and best.exists() and not args.force:
        if same_config:
            print(f"Обучение {name} уже завершено: {best} (--force — обучить заново).")
            print_projection(prev)
            return 0
        print(f"Запуск {run_dir} уже есть и обучен с другими параметрами:\n"
              f"  было:  {json.dumps(prev.get('config'), ensure_ascii=False)}\n"
              f"  сейчас: {json.dumps(config, ensure_ascii=False)}\n"
              "Чтобы не затереть готовую модель, укажите другое --name или --force.")
        return 2
    resume = (last.exists() and same_config and not args.force and not args.no_resume
              and not (prev or {}).get("finished"))
    if not resume and run_dir.exists() and any(run_dir.iterdir()):
        # Ultralytics дописывает results.csv в существующую папку — старый запуск переносим целиком.
        backup = run_dir.with_name(f"{name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        run_dir.rename(backup)
        print(f"Предыдущий запуск перенесён в {backup}")

    # --- память
    ram = available_ram_gb()
    if ram:
        avail, total = ram
        print(f"Память: свободно {avail:.1f} ГБ из {total:.1f} ГБ.")
        if avail < MIN_RAM_GB:
            print(f"Внимание: свободно меньше {MIN_RAM_GB:.0f} ГБ. Закройте браузер, Label Studio и сервис; "
                  "при нехватке памяти уменьшите --batch до 4 или возьмите профиль full_m.")
    else:
        print("psutil не установлен — свободную память проверить нельзя.")

    try:
        import ultralytics
        from ultralytics import YOLO
    except ImportError:
        print("Нет ultralytics. Активируйте окружение: source .venv-train/bin/activate")
        return 2

    train_args = filter_ultralytics_args(train_args)
    recorder = TimingRecorder(name, args.profile, config, cfg, data_yaml)
    project = run_dir.parent
    project.mkdir(parents=True, exist_ok=True)

    print(f"Обучение {name}: профиль {args.profile}, модель {model_name}, данные {data_yaml}, "
          f"устройство {train_args['device']}, ultralytics {ultralytics.__version__}")
    t0 = time.perf_counter()
    try:
        if resume:
            print(f"Продолжаю прерванное обучение с {last} (--no-resume — начать сначала).")
            model = YOLO(str(last))
        else:
            model = YOLO(wts.resolve_weight(model_name))
        model.add_callback("on_train_epoch_start", recorder.on_train_epoch_start)
        model.add_callback("on_train_batch_start", recorder.on_train_batch_start)
        model.add_callback("on_train_batch_end", recorder.on_train_batch_end)
        model.add_callback("on_train_epoch_end", recorder.on_train_epoch_end)
        model.add_callback("on_fit_epoch_end", recorder.on_fit_epoch_end)
        if resume:
            metrics = model.train(resume=True, device=train_args["device"])
        else:
            metrics = model.train(data=str(data_yaml), project=str(project), name=name, exist_ok=True,
                                  **train_args)
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}")
        return 2
    except AssertionError as exc:
        if resume:
            print(f"Не удалось продолжить обучение ({exc}). Запустите с --force или --no-resume.")
            return 2
        raise
    except KeyboardInterrupt:
        print(f"\nОстановлено. Повторный запуск продолжит с {last}.")
        return 130

    trainer = getattr(model, "trainer", None)
    save_dir = Path(trainer.save_dir) if trainer is not None else run_dir
    timing = recorder.last
    if trainer is not None and recorder.full_secs:
        timing = recorder.build(trainer, finished=True)
        timing["elapsed_sec"] = round(time.perf_counter() - t0, 1)
        _write_json(save_dir / "timing.json", timing)

    print("\n=== Итог ===")
    print(f"Запуск: {save_dir}")
    print(f"Веса:   {save_dir / 'weights' / 'best.pt'}")
    print(f"Время:  {fmt_duration(time.perf_counter() - t0)}")
    box = getattr(metrics, "box", None)
    if box is not None:
        try:
            print(f"Валидация: mAP50 = {box.map50:.3f}, mAP50-95 = {box.map:.3f}")
        except Exception:
            pass
    if timing:
        print_projection(timing)
    if not (save_dir / "weights" / "best.pt").exists():
        print("Ошибка: best.pt не создан.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
