"""Шаг 0: проверка окружения обучения перед запуском пайплайна.

Печатает:
  * версии Python и пакетов (torch, ultralytics, transformers, av, cv2, ...) и сверяет минимальные версии;
  * доступность MPS (Mac) / CUDA и устройство, которое выберут скрипты (common.pick_device);
  * общий и свободный объём памяти (psutil);
  * свободное место на диске с data_dir (предупреждение, если меньше --min-free-gb, по умолчанию 20 ГБ);
  * наличие весов в models/pretrained/*.pt и Grounding DINO в кэше Hugging Face;
  * наличие исходных данных: data/raw/open/videos/*.mov и open_labels.xlsx;
  * состояние следующих шагов (manifest.csv, frames/index.csv), если они уже выполнялись.

Запуск из корня репозитория:
    python training/scripts/00_check_env.py
    python training/scripts/00_check_env.py --min-free-gb 30

Скрипт ничего не скачивает и не меняет (только создаёт пустую папку data_dir, если её нет).
Код возврата 1 — только если не установлен PyTorch (или PyYAML, без которого не читаются конфиги);
всё остальное — предупреждения.
"""
from __future__ import annotations

import sys, pathlib  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import argparse
import csv
import importlib
import importlib.metadata
import os
import platform
import re
import shutil
from pathlib import Path

try:
    from kassa_train import common
except ImportError as exc:  # нет PyYAML — конфиги не читаются, но версии пакетов покажем
    common = None
    _COMMON_IMPORT_ERROR = exc
else:
    _COMMON_IMPORT_ERROR = None

GB = 1024 ** 3
MB = 1024 ** 2

# (модуль для import, имя дистрибутива, минимальная версия, обязателен ли для обучения)
PACKAGES: list[tuple[str, str, str | None, bool]] = [
    ("torch", "torch", "2.7", True),
    ("torchvision", "torchvision", None, True),
    ("ultralytics", "ultralytics", "8.4.179", True),
    ("transformers", "transformers", "5.0", True),
    ("av", "av", "14", True),
    ("cv2", "opencv-python", "4.10", True),
    ("imagehash", "ImageHash", "4.3", True),
    ("PIL", "pillow", None, True),
    ("numpy", "numpy", None, True),
    ("pandas", "pandas", "2.2", True),
    ("openpyxl", "openpyxl", "3.1", True),
    ("yaml", "PyYAML", "6.0", True),
    ("tqdm", "tqdm", None, False),
    ("psutil", "psutil", None, False),
    ("huggingface_hub", "huggingface_hub", None, False),
]
if sys.platform == "darwin":
    PACKAGES.append(("coremltools", "coremltools", "9.0", False))

# Модули, которые дорого или нежелательно импортировать ради версии (ultralytics создаёт файл настроек).
_METADATA_ONLY = {"ultralytics", "transformers", "torchvision", "pandas", "coremltools", "huggingface_hub"}
# У OpenCV несколько имён дистрибутива, версию надёжнее брать из самого модуля.
_IMPORT_FOR_VERSION = {"cv2", "av", "PIL", "yaml", "imagehash", "numpy", "psutil", "tqdm", "openpyxl"}

# Веса, которые скачивает training/download_models.sh.
DOWNLOADED_WEIGHTS = [
    "yolo26n.pt", "yolo26s.pt", "yolo26m.pt", "yolo26l.pt", "yolo26x.pt",
    "yolo26l-pose.pt", "yolo26s-cls.pt", "yolo26n-cls.pt",
]
QWEN_DIRS = ["Qwen3.5-4B-8bit", "Qwen3.5-4B-4bit"]
VIDEO_SUFFIXES = {".mov"}


class Report:
    """Собирает строки отчёта и считает предупреждения и ошибки."""

    def __init__(self) -> None:
        self.warnings = 0
        self.errors = 0
        self.fatal = False

    @staticmethod
    def section(title: str) -> None:
        print(f"\n== {title}")

    @staticmethod
    def ok(msg: str) -> None:
        print(f"  [ok] {msg}")

    @staticmethod
    def info(msg: str) -> None:
        print(f"  [--] {msg}")

    def warn(self, msg: str) -> None:
        self.warnings += 1
        print(f"  [!!] {msg}")

    def error(self, msg: str, fatal: bool = False) -> None:
        self.errors += 1
        self.fatal = self.fatal or fatal
        print(f"  [ОШИБКА] {msg}")


def _setup_console() -> None:
    """Не падать на символах, которых нет в кодировке консоли (Windows, вывод в pipe)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def _version_tuple(version: str) -> tuple[int, ...]:
    """'2.7.1+cpu' → (2, 7, 1); '5.0.0rc1' → (5, 0, 0)."""
    core = version.split("+")[0]
    parts = []
    for piece in core.split("."):
        m = re.match(r"\d+", piece)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts)


def _version_ok(version: str, minimum: str | None) -> bool:
    if not minimum:
        return True
    have, need = _version_tuple(version), _version_tuple(minimum)
    width = max(len(have), len(need))
    return have + (0,) * (width - len(have)) >= need + (0,) * (width - len(need))


def _package_version(module: str, dist: str) -> str | None:
    """Версия пакета или None, если не установлен."""
    if module not in _IMPORT_FOR_VERSION:
        try:
            return importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            if module in _METADATA_ONLY:
                return None
    try:
        mod = importlib.import_module(module)
    except Exception:  # noqa: BLE001 — любой сбой импорта означает «пакет недоступен»
        return None
    version = getattr(mod, "__version__", None)
    if version is None:
        try:
            version = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            version = "?"
    return str(version)


def check_python(rep: Report) -> None:
    rep.section("Python и система")
    rep.info(f"Python {platform.python_version()} ({sys.executable})")
    rep.info(f"ОС: {platform.system()} {platform.release()}, архитектура {platform.machine()}")
    if sys.version_info[:2] != (3, 12):
        rep.warn("пайплайн рассчитан на Python 3.12 (uv venv --python 3.12 .venv-train)")
    if sys.platform == "darwin" and platform.machine() == "x86_64":
        rep.warn("Python запущен под Rosetta (x86_64): MPS будет недоступен. Нужен arm64-интерпретатор")
    if sys.prefix == sys.base_prefix:
        rep.warn("виртуальное окружение не активировано: source .venv-train/bin/activate")
    else:
        rep.ok(f"виртуальное окружение: {sys.prefix}")


def check_packages(rep: Report) -> dict[str, str | None]:
    rep.section("Пакеты")
    versions: dict[str, str | None] = {}
    for module, dist, minimum, required in PACKAGES:
        version = _package_version(module, dist)
        versions[module] = version
        label = f"{dist:<16}"
        if version is None:
            msg = f"{label} не установлен"
            if module == "torch":
                rep.error(msg + " — обучение невозможно. Запустите: bash training/setup_mac.sh", fatal=True)
            elif module == "yaml":
                rep.error(msg + " — конфиги не читаются: uv pip install -r training/requirements.txt",
                          fatal=True)
            elif required:
                rep.error(msg + " — uv pip install -r training/requirements.txt")
            else:
                rep.warn(msg)
        elif not _version_ok(version, minimum):
            rep.warn(f"{label} {version} — нужна версия >= {minimum}")
        else:
            rep.ok(f"{label} {version}" + (f" (нужно >= {minimum})" if minimum else ""))

    if versions.get("av"):
        try:
            import av
            libs = getattr(av, "library_versions", {}) or {}
            codec_ver = libs.get("libavcodec")
            if codec_ver:
                rep.info("PyAV: libavcodec " + ".".join(str(x) for x in codec_ver))
            av.codec.Codec("h264", "r")
            rep.ok("PyAV: декодер H.264 доступен")
        except Exception as exc:  # noqa: BLE001
            rep.warn(f"PyAV: декодер H.264 недоступен ({exc}); 02_extract_frames перейдёт на OpenCV")
    return versions


def check_torch(rep: Report, versions: dict[str, str | None]) -> None:
    rep.section("Ускорители (PyTorch)")
    if not versions.get("torch"):
        rep.error("PyTorch не установлен — проверка MPS/CUDA пропущена", fatal=True)
        return
    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        rep.error(f"import torch не удался: {exc}", fatal=True)
        return

    mps = getattr(torch.backends, "mps", None)
    mps_built = bool(mps and mps.is_built())
    mps_available = bool(mps and mps.is_available())
    if mps_available:
        try:
            x = torch.ones(4, device="mps")
            value = float((x * 2).sum().item())
            if abs(value - 8.0) < 1e-6:
                rep.ok("MPS доступен, тестовое вычисление на GPU прошло")
            else:
                rep.warn(f"MPS доступен, но тестовое вычисление дало {value} вместо 8.0")
        except Exception as exc:  # noqa: BLE001
            rep.warn(f"MPS доступен, но тестовое вычисление упало: {exc}")
        if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") != "1":
            rep.info("совет: export PYTORCH_ENABLE_MPS_FALLBACK=1 — неподдерживаемые MPS операции уйдут на CPU")
    elif sys.platform == "darwin":
        rep.warn(f"MPS недоступен (собран: {mps_built}). Обучение пойдёт на CPU и будет в разы медленнее")
    else:
        rep.info("MPS недоступен (не macOS) — для отладки это нормально")

    if torch.cuda.is_available():
        names = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        rep.ok(f"CUDA {torch.version.cuda}: {', '.join(names)}")
    else:
        rep.info("CUDA недоступна")

    if common is not None:
        try:
            pref = common.pipeline_config().get("prelabel", {}).get("device", "auto")
            rep.ok(f"устройство по умолчанию (pick_device('{pref}')): {common.pick_device(pref)}")
        except Exception as exc:  # noqa: BLE001
            rep.warn(f"не удалось определить устройство: {exc}")


def check_memory(rep: Report, versions: dict[str, str | None]) -> None:
    rep.section("Память")
    if not versions.get("psutil"):
        rep.warn("psutil не установлен — память не проверена")
        return
    import psutil
    vm = psutil.virtual_memory()
    rep.info(f"всего {vm.total / GB:.1f} ГБ, доступно {vm.available / GB:.1f} ГБ")
    if vm.total < 15 * GB:
        rep.warn("меньше 16 ГБ: в configs/train.yaml для full поставьте batch: 4 или профиль full_m")
    if vm.available < 4 * GB:
        rep.warn("доступно меньше 4 ГБ: закройте браузер, Docker, сервис и Qwen перед обучением")


def check_disk(rep: Report, min_free_gb: float) -> None:
    rep.section("Диск")
    if common is None:
        rep.warn("конфиги не прочитаны — диск не проверен")
        return
    data_dir = common.data_dir()
    source = "KASSA_DATA_DIR" if os.environ.get("KASSA_DATA_DIR") else "pipeline.yaml"
    rep.info(f"data_dir: {data_dir} (из {source})")
    usage = shutil.disk_usage(data_dir)
    free_gb = usage.free / GB
    msg = f"свободно {free_gb:.1f} ГБ из {usage.total / GB:.0f} ГБ"
    if free_gb < min_free_gb:
        rep.warn(msg + f" — меньше {min_free_gb:g} ГБ (кадры, датасеты, runs/, модели)")
    else:
        rep.ok(msg)


def _referenced_weights() -> list[str]:
    """Имена весов из configs/train.yaml и pipeline.yaml (prelabel.coco_model)."""
    names: list[str] = []
    try:
        train = common.load_yaml(common.CONFIG_DIR / "train.yaml") or {}
        for prof in (train.get("profiles") or {}).values():
            if isinstance(prof, dict) and prof.get("model"):
                names.append(str(prof["model"]))
        cls = train.get("cls") or {}
        for task in (cls.get("tasks") or {}).values():
            if isinstance(task, dict) and task.get("model"):
                names.append(str(task["model"]))
        coco = (common.pipeline_config().get("prelabel") or {}).get("coco_model")
        if coco:
            names.append(str(coco))
    except Exception:  # noqa: BLE001 — список только для отчёта
        pass
    return names


def _hf_hub_cache() -> Path:
    try:
        from huggingface_hub import constants
        return Path(constants.HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        pass
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "huggingface" / "hub"


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def check_models(rep: Report) -> None:
    rep.section("Веса моделей")
    if common is None:
        rep.warn("конфиги не прочитаны — веса не проверены")
        return
    pretrained = common.repo_path("models") / "pretrained"
    rep.info(f"папка: {pretrained}")
    wanted = list(dict.fromkeys(DOWNLOADED_WEIGHTS + _referenced_weights()))
    missing = []
    for name in wanted:
        path = pretrained / name
        if path.is_file() and path.stat().st_size > 0:
            rep.ok(f"{name:<18} {path.stat().st_size / MB:7.1f} МБ")
        else:
            missing.append(name)
            if (pretrained / (name + ".part")).exists():
                rep.warn(f"{name:<18} загрузка не завершена (.part) — перезапустите download_models.sh")
    if missing:
        rep.warn("нет: " + ", ".join(missing)
                 + " — bash training/download_models.sh (иначе Ultralytics скачает при первом запуске)")
    extra = sorted(p.name for p in pretrained.glob("*.pt") if p.name not in wanted) if pretrained.is_dir() else []
    if extra:
        rep.info("также есть: " + ", ".join(extra))

    gdino = (common.pipeline_config().get("prelabel") or {}).get("gdino_model", "IDEA-Research/grounding-dino-base")
    hub = _hf_hub_cache()
    repo_dir = hub / ("models--" + str(gdino).replace("/", "--"))
    weights_found = False
    if repo_dir.is_dir():
        for snap in (repo_dir / "snapshots").glob("*"):
            if any((snap / f).exists() for f in ("model.safetensors", "pytorch_model.bin")) \
                    and (snap / "config.json").exists():
                weights_found = True
                break
    if weights_found:
        rep.ok(f"Grounding DINO {gdino} в кэше HF ({_dir_size(repo_dir) / GB:.2f} ГБ): {repo_dir}")
    elif repo_dir.is_dir():
        rep.warn(f"Grounding DINO {gdino}: загрузка не завершена ({repo_dir}) — перезапустите download_models.sh")
    else:
        rep.warn(f"Grounding DINO {gdino} нет в кэше HF ({hub}) — bash training/download_models.sh "
                 "(иначе 03_prelabel скачает ~0,9 ГБ при первом запуске)")

    hf_local = common.repo_path("models") / "hf"
    present = [q for q in QWEN_DIRS if (hf_local / q).is_dir() and any((hf_local / q).glob("*.safetensors"))]
    if present:
        rep.ok("Qwen для сервиса: " + ", ".join(present))
    else:
        rep.info("Qwen для сервиса не скачан (для обучения не нужен)")


def _count_csv_rows(path: Path) -> int | None:
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))
    except (OSError, csv.Error):
        return None


def check_data(rep: Report) -> None:
    rep.section("Данные")
    if common is None:
        rep.warn("конфиги не прочитаны — данные не проверены")
        return
    videos_dir = common.data_path("raw_videos")
    labels = common.data_path("raw_labels")

    try:
        catalog = common.load_catalog()
    except Exception as exc:  # noqa: BLE001
        catalog = {}
        rep.warn(f"каталог {common.repo_path('catalog')} не прочитан: {exc}")
    expected_files = len(catalog) + len(common.DUPLICATES) if catalog else None

    if not videos_dir.is_dir():
        rep.warn(f"нет папки с роликами {videos_dir} — распакуйте open.zip так, "
                 "чтобы получилось data/raw/open/videos/*.mov")
    else:
        movs = sorted(p for p in videos_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)
        size_gb = sum(p.stat().st_size for p in movs) / GB
        msg = f"{videos_dir}: {len(movs)} файлов .mov, {size_gb:.2f} ГБ"
        if expected_files is not None and len(movs) != expected_files:
            rep.warn(msg + f" (ожидается {expected_files}: {len(catalog)} уникальных + "
                     f"{len(common.DUPLICATES)} дубля)")
        elif not movs:
            rep.warn(msg)
        else:
            rep.ok(msg)
        if catalog and movs:
            on_disk = {common.norm_video_id(p.name) for p in movs}
            want = set(catalog) | set(common.DUPLICATES)
            lost = sorted(want - on_disk, key=_vid_key)
            extra = sorted(on_disk - want, key=_vid_key)
            if lost:
                rep.warn("нет роликов из каталога: " + ", ".join(lost))
            if extra:
                rep.info("ролики вне каталога (будут без событий): " + ", ".join(extra))

    if labels.is_file():
        rep.ok(f"разметка организаторов: {labels}")
    else:
        rep.warn(f"нет {labels} — 01_prepare_raw возьмёт метки только из каталога analysis/labels_typed.csv")

    manifest = common.data_path("manifest")
    if manifest.is_file():
        rep.info(f"manifest.csv: {_count_csv_rows(manifest)} строк (шаг 01 выполнен)")
    index = common.data_path("frames") / "index.csv"
    if index.is_file():
        rep.info(f"frames/index.csv: {_count_csv_rows(index)} кадров (шаг 02 выполнен)")


def _vid_key(video_id: str) -> tuple[str, int, str]:
    m = re.match(r"^([A-Z]+)-(\d+)$", video_id)
    return (m.group(1), int(m.group(2)), "") if m else (video_id, 0, video_id)


def main() -> int:
    _setup_console()
    parser = argparse.ArgumentParser(
        description="Проверка окружения обучения: версии, MPS/CUDA, память, диск, веса, данные.")
    parser.add_argument("--min-free-gb", type=float, default=20.0,
                        help="порог свободного места на диске с data_dir, ГБ (по умолчанию 20)")
    args = parser.parse_args()

    rep = Report()
    check_python(rep)
    versions = check_packages(rep)
    if _COMMON_IMPORT_ERROR is not None:
        rep.error(f"kassa_train.common не импортируется: {_COMMON_IMPORT_ERROR}", fatal=True)

    for check in (lambda: check_torch(rep, versions), lambda: check_memory(rep, versions),
                  lambda: check_disk(rep, args.min_free_gb), lambda: check_models(rep), lambda: check_data(rep)):
        try:
            check()
        except Exception as exc:  # noqa: BLE001 — проверка не должна падать целиком
            rep.warn(f"проверка прервалась: {type(exc).__name__}: {exc}")

    print(f"\nИтог: ошибок {rep.errors}, предупреждений {rep.warnings}.", end=" ")
    if rep.fatal:
        print("Окружение не готово: установите зависимости (bash training/setup_mac.sh).")
        return 1
    if rep.errors or rep.warnings:
        print("Пайплайн можно запускать; предупреждения выше стоит просмотреть.")
    else:
        print("Всё готово к запуску этапа A.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
