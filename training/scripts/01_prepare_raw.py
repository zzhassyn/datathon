"""Шаг A1: манифест исходных роликов → data/manifest.csv.

Что делает:
  1. находит ролики в data/raw/open/videos (pipeline.yaml → paths.raw_videos);
  2. читает open_labels.xlsx организаторов (имя файла, «22 секунда» / «1минута 8 секунда»,
     «нарушение» / «без нарушения»);
  3. для каждого ролика считает sha256 и параметры потока: длительность, fps, число кадров,
     ширину и высоту с учётом поворота (PyAV, при сбое — OpenCV);
  4. находит побайтные дубли (ожидаются Х-47 = Х-4 и Х-48 = Х-5, см. common.DUPLICATES);
  5. сверяет всё с каталогом analysis/labels_typed.csv (40 уникальных роликов): расхождения
     печатаются, но работа продолжается. Исключение: если в каталоге нет и половины роликов с диска
     (имена испорчены при распаковке zip, например «╒-13.mov»), шаг останавливается с кодом 2
     и подсказкой `unar -e cp866 open.zip`;
  6. пишет data/manifest.csv (UTF-8) с колонками
     video_id,file_name,path,sha256,duration_s,fps,n_frames,width,height,label,events,scene,duplicate_of

Источник меток, событий и сцен — каталог (он выверен по кадрам); xlsx — для сверки и для роликов,
которых нет в каталоге. У оригинала events — все события каталога, включая события его дублей
(у Х-4: 57;68). У дубля events — его собственные события из xlsx, а duplicate_of — id оригинала;
дубли никогда не извлекаются отдельно. path — относительно data_dir, в стиле POSIX.

Запуск из корня репозитория:
    python training/scripts/01_prepare_raw.py
    python training/scripts/01_prepare_raw.py --force                 # пересчитать всё заново
    python training/scripts/01_prepare_raw.py --videos-dir /path/videos --labels /path/open_labels.xlsx

Повторный запуск ничего не делает, если манифест новее роликов, xlsx, каталога и кода
и набор файлов не изменился. Коды возврата: 0 — успех, 1 — часть роликов не прочиталась
(манифест записан без них), 2 — нет роликов или неверные аргументы.
"""
from __future__ import annotations

import sys, pathlib  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import csv
import datetime
import io
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_COLUMNS = [
    "video_id", "file_name", "path", "sha256", "duration_s", "fps", "n_frames",
    "width", "height", "label", "events", "scene", "duplicate_of",
]
VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v"}
EVENT_TOLERANCE_SEC = 1.0  # расхождение таймкодов xlsx и каталога больше этого — предупреждение


@dataclass
class VideoMeta:
    duration_s: float
    fps: float
    n_frames: int
    width: int
    height: int
    backend: str


@dataclass
class XlsxEntry:
    label: str | None = None
    events: list[float] = field(default_factory=list)
    raw_rows: int = 0


class Issues:
    """Предупреждения и ошибки с печатью по мере появления."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"  [!!] {msg}")

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        print(f"  [ОШИБКА] {msg}")


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


def fmt_num(value: float, digits: int = 3) -> str:
    """22.0 → '22'; 29.9229 → '29.923'."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def rel_to_data(path: Path) -> str:
    """Путь относительно data_dir (POSIX); вне data_dir — абсолютный."""
    try:
        return path.resolve().relative_to(common.data_dir().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def parse_label(value: object) -> str | None:
    """'нарушение' → violation; 'без нарушения' → no_violation; иначе None."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text.startswith("без") or "без нарушен" in text or "нет нарушен" in text:
        return "no_violation"
    if "нарушен" in text:
        return "violation"
    if text in ("violation", "no_violation"):
        return text
    return None


def parse_time_cell(value: object) -> float | None:
    """Таймкод из ячейки: '22 секунда', '1минута 8 секунда' (common.parse_ru_timestamp),
    а также '0:22' / '00:01:08', время или число секунд, если ячейку переформатируют в Excel."""
    if value is None:
        return None
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, datetime.datetime):  # время в Excel хранится как дата 1899-12-30 + время
        value = value.time()
    if isinstance(value, datetime.time):
        return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", text)
    if m:
        parts = [int(g) for g in m.groups() if g is not None]
        if len(parts) == 2:
            return float(parts[0] * 60 + parts[1])
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    return common.parse_ru_timestamp(text)


# --------------------------------------------------------------------------- probing

def _rotation_of(frame) -> int:
    try:
        return int(round(float(getattr(frame, "rotation", 0) or 0))) % 360
    except (TypeError, ValueError):
        return 0


def probe_pyav(path: Path) -> VideoMeta:
    import av

    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("в файле нет видеопотока")
        stream = container.streams.video[0]
        rate = stream.average_rate or stream.guessed_rate or stream.base_rate
        fps = float(rate) if rate else 0.0
        if stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        elif container.duration:
            duration = container.duration / av.time_base
        else:
            duration = 0.0
        n_frames = int(stream.frames or 0)
        width, height = stream.codec_context.width, stream.codec_context.height
        rotation = 0
        decoded = False
        for frame in container.decode(stream):  # первый кадр: файл декодируется + поворот
            width, height = frame.width, frame.height
            rotation = _rotation_of(frame)
            decoded = True
            break
        if not decoded:
            raise ValueError("не декодировался ни один кадр")

    if n_frames <= 0:  # в контейнере нет числа кадров — считаем пакеты без декодирования
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            n_frames = sum(1 for packet in container.demux(stream) if packet.size)
    if rotation in (90, 270):
        width, height = height, width
    if fps <= 0 and duration > 0 and n_frames > 0:
        fps = n_frames / duration
    if duration <= 0 and fps > 0 and n_frames > 0:
        duration = n_frames / fps
    return VideoMeta(duration, fps, n_frames, int(width), int(height), "pyav")


def probe_cv2(path: Path) -> VideoMeta:
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("OpenCV не открыл файл")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise ValueError("OpenCV не декодировал ни одного кадра")
        height, width = frame.shape[:2]  # OpenCV сам применяет поворот из метаданных
    finally:
        cap.release()
    duration = n_frames / fps if fps > 0 else 0.0
    return VideoMeta(duration, fps, n_frames, int(width), int(height), "cv2")


def probe_video(path: Path) -> VideoMeta:
    errors = []
    for probe in (probe_pyav, probe_cv2):
        try:
            meta = probe(path)
            if meta.n_frames > 0 and meta.width > 0 and meta.height > 0:
                return meta
            errors.append(f"{probe.__name__}: пустые параметры потока")
        except ImportError as exc:
            errors.append(f"{probe.__name__}: нет модуля ({exc.name})")
        except Exception as exc:  # noqa: BLE001 — пробуем следующий способ
            errors.append(f"{probe.__name__}: {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


# --------------------------------------------------------------------------- xlsx

def _find_col(header: list[str], keys: tuple[str, ...], default: int) -> int:
    for i, cell in enumerate(header):
        if any(k in cell for k in keys):
            return i
    return default


def read_xlsx(path: Path, issues: Issues) -> dict[str, XlsxEntry]:
    """open_labels.xlsx → {video_id: XlsxEntry}. Нет файла или openpyxl — пустой словарь."""
    if not path.is_file():
        issues.warn(f"нет {path} — метки берутся только из каталога")
        return {}
    try:
        import openpyxl
    except ImportError:
        issues.warn("openpyxl не установлен — xlsx пропущен, метки только из каталога")
        return {}

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    finally:
        wb.close()
    if not rows:
        issues.warn(f"{path.name}: пустой лист")
        return {}

    header = [str(c or "").strip().lower() for c in rows[0]]
    has_header = any("файл" in h or "нарушен" in h for h in header)
    col_file = _find_col(header, ("файл", "наимен", "file"), 0) if has_header else 0
    col_time = _find_col(header, ("тайм", "врем", "метк", "time"), 1) if has_header else 1
    col_label = _find_col(header, ("нарушен", "label"), 2) if has_header else 2

    entries: dict[str, XlsxEntry] = {}
    width = max(col_file, col_time, col_label) + 1
    for line_no, row in enumerate(rows[1:] if has_header else rows, start=2 if has_header else 1):
        cells = list(row) + [None] * max(0, width - len(row))
        name, time_cell, label_cell = cells[col_file], cells[col_time], cells[col_label]
        if name is None or str(name).strip() == "":
            continue
        vid = common.norm_video_id(str(name))
        entry = entries.setdefault(vid, XlsxEntry())
        entry.raw_rows += 1
        label = parse_label(label_cell)
        if label is None and str(label_cell or "").strip():
            issues.warn(f"{path.name}, строка {line_no}: непонятная метка {label_cell!r}")
        if label == "violation" or entry.label is None:
            entry.label = label or entry.label
        t = parse_time_cell(time_cell)
        if t is None and str(time_cell or "").strip():
            issues.warn(f"{path.name}, строка {line_no}: таймкод не разобран {time_cell!r}")
        if t is not None:
            entry.events.append(t)
    for entry in entries.values():
        entry.events.sort()
    return entries


# --------------------------------------------------------------------------- main logic

def list_videos(videos_dir: Path, issues: Issues) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for p in sorted(videos_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_SUFFIXES or p.name.startswith("."):
            continue
        vid = common.norm_video_id(p.name)
        if vid in found:
            issues.error(f"два файла с одним id {vid}: {found[vid].name} и {p.name} — второй пропущен")
            continue
        found[vid] = p
    return found


def manifest_is_fresh(manifest: Path, videos: dict[str, Path], extra_inputs: list[Path]) -> bool:
    """Манифест новее всех входов и описывает тот же набор файлов."""
    if not manifest.is_file():
        return False
    mtime = manifest.stat().st_mtime
    inputs = list(videos.values()) + [p for p in extra_inputs if p.exists()]
    if any(p.stat().st_mtime > mtime for p in inputs):
        return False
    try:
        with open(manifest, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != MANIFEST_COLUMNS:
                return False
            names = {row["file_name"] for row in reader}
    except (OSError, csv.Error, KeyError):
        return False
    return names == {p.name for p in videos.values()}


def assign_duplicates(videos: dict[str, Path], sha: dict[str, str], issues: Issues) -> dict[str, str]:
    """{дубль: оригинал}. Побайтные копии + common.DUPLICATES (даже если файлы различаются)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for vid in sorted(sha, key=vid_key):
        groups[sha[vid]].append(vid)

    dup_of: dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        # оригинал — тот, кто не числится дублем в common.DUPLICATES, затем меньший номер
        members.sort(key=lambda v: (v in common.DUPLICATES, vid_key(v)))
        original = members[0]
        for vid in members[1:]:
            dup_of[vid] = original
            expected = common.DUPLICATES.get(vid)
            if expected is None:
                issues.warn(f"{vid} побайтно совпадает с {original}, но не описан в common.DUPLICATES — "
                            "помечен как дубль; добавьте его в DUPLICATES и каталог")
            elif expected != original:
                issues.warn(f"{vid}: по common.DUPLICATES дубль {expected}, а побайтно совпадает с {original}")

    for vid, original in common.DUPLICATES.items():
        if vid not in videos:
            continue
        if original not in videos:
            issues.warn(f"{vid} — дубль {original} по common.DUPLICATES, но {original} нет на диске")
        if vid not in dup_of:
            if original in videos:
                issues.warn(f"{vid} и {original} различаются по sha256, но по соглашению {vid} — дубль "
                            f"{original} и отдельно не извлекается")
            dup_of[vid] = original
    return dup_of


def cross_check(videos: dict[str, Path], dup_of: dict[str, str], catalog: dict[str, common.VideoInfo],
                xlsx: dict[str, XlsxEntry], issues: Issues) -> None:
    unique = {v for v in videos if v not in dup_of}
    expected_unique = len(catalog)
    if len(unique) != expected_unique:
        issues.warn(f"уникальных роликов {len(unique)}, в каталоге {expected_unique}")
    missing = sorted(set(catalog) - unique, key=vid_key)
    extra = sorted(unique - set(catalog), key=vid_key)
    if missing:
        issues.warn("ролики каталога, которых нет на диске: " + ", ".join(missing))
    if extra:
        issues.warn("ролики на диске, которых нет в каталоге (без сцены; события из xlsx): " + ", ".join(extra))

    if xlsx:
        no_row = sorted(set(videos) - set(xlsx), key=vid_key)
        no_file = sorted(set(xlsx) - set(videos), key=vid_key)
        if no_row:
            issues.warn("ролики без строки в xlsx: " + ", ".join(no_row))
        if no_file:
            issues.warn("строки xlsx без файла: " + ", ".join(no_file))
        for vid, entry in xlsx.items():
            if entry.raw_rows > 1:
                issues.warn(f"в xlsx {entry.raw_rows} строки для {vid} — события объединены")

    # метки и таймкоды: события дублей xlsx относятся к оригиналу (так же сливает load_catalog)
    xlsx_events: dict[str, list[float]] = defaultdict(list)
    xlsx_label: dict[str, str | None] = {}
    for vid, entry in xlsx.items():
        target = dup_of.get(vid, common.DUPLICATES.get(vid, vid))
        xlsx_events[target].extend(entry.events)
        if vid == target:
            xlsx_label[vid] = entry.label
    for vid in sorted(unique & set(catalog), key=vid_key):
        info = catalog[vid]
        if vid in xlsx_label and xlsx_label[vid] and xlsx_label[vid] != info.label:
            issues.warn(f"{vid}: метка в xlsx {xlsx_label[vid]}, в каталоге {info.label} (берётся каталог)")
        if vid not in xlsx_events and vid not in xlsx_label:
            continue
        a, b = sorted(xlsx_events.get(vid, [])), sorted(info.events)
        if len(a) != len(b) or any(abs(x - y) > EVENT_TOLERANCE_SEC for x, y in zip(a, b, strict=True)):
            issues.warn(f"{vid}: события в xlsx {[fmt_num(x) for x in a]}, в каталоге "
                        f"{[fmt_num(x) for x in b]} (берётся каталог)")


def build_rows(videos: dict[str, Path], sha: dict[str, str], meta: dict[str, VideoMeta],
               dup_of: dict[str, str], catalog: dict[str, common.VideoInfo],
               xlsx: dict[str, XlsxEntry]) -> list[dict[str, str]]:
    rows = []
    for vid in sorted(meta, key=vid_key):
        path, m = videos[vid], meta[vid]
        own = xlsx.get(vid)
        original = dup_of.get(vid, "")
        if original:
            info = catalog.get(original)
            label = (own.label if own else None) or (info.label if info else "")
            events = own.events if own else []
            scene = info.scene if info else ""
        else:
            info = catalog.get(vid)
            label = info.label if info else ((own.label if own else None) or "")
            events = info.events if info else (own.events if own else [])
            scene = info.scene if info else ""
        rows.append({
            "video_id": vid,
            "file_name": path.name,
            "path": rel_to_data(path),
            "sha256": sha[vid],
            "duration_s": fmt_num(m.duration_s),
            "fps": fmt_num(m.fps),
            "n_frames": str(m.n_frames),
            "width": str(m.width),
            "height": str(m.height),
            "label": label,
            "events": ";".join(fmt_num(e) for e in sorted(events)),
            "scene": scene,
            "duplicate_of": original,
        })
    return rows


def render_csv(rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def read_manifest(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def print_summary(rows: list[dict[str, str]], manifest: Path, issues: Issues | None, elapsed: float) -> None:
    unique = [r for r in rows if not r["duplicate_of"]]
    dups = [r for r in rows if r["duplicate_of"]]
    labels = Counter(r["label"] or "?" for r in unique)
    n_events = sum(len([e for e in r["events"].split(";") if e]) for r in unique)
    scenes = Counter(r["scene"] or "?" for r in unique)
    total_min = sum(float(r["duration_s"] or 0) for r in unique) / 60
    sizes = Counter(f"{r['width']}x{r['height']}" for r in unique)

    print("\nИтог:")
    print(f"  роликов {len(rows)}: уникальных {len(unique)}, дублей {len(dups)}"
          + (" (" + ", ".join(f"{r['video_id']}→{r['duplicate_of']}" for r in dups) + ")" if dups else ""))
    print(f"  нарушений {labels.get('violation', 0)} (событий {n_events}), "
          f"без нарушений {labels.get('no_violation', 0)}"
          + (f", без метки {labels['?']}" if labels.get("?") else ""))
    print("  сцены: " + ", ".join(f"{k} {v}" for k, v in sorted(scenes.items())))
    print(f"  длительность уникальных: {total_min:.1f} мин; размеры кадра: "
          + ", ".join(f"{k} ({v})" for k, v in sizes.most_common()))
    if issues is not None:
        print(f"  предупреждений {len(issues.warnings)}, ошибок {len(issues.errors)}")
    print(f"  манифест: {manifest}  ({elapsed:.1f} с)")


def names_look_broken(videos: dict[str, Path], catalog: dict[str, common.VideoInfo]) -> bool:
    """Меньше половины роликов на диске есть в каталоге — почти наверняка имена испорчены при распаковке.

    Так бывает, если open.zip собран встроенным архиватором Windows (имена в CP866/CP1251 без флага
    UTF-8), а распакован на macOS: «Х-13.mov» превращается в «╒-13.mov» и т. п. Тогда у роликов нет
    сцен и событий, и этап A падает только на A4 («в val нет кадров») — лучше остановиться сразу.
    """
    if not catalog or not videos:
        return False
    known = set(catalog) | set(common.DUPLICATES)
    matched = sum(1 for vid in videos if vid in known)
    if matched * 2 >= len(videos):
        return False
    sample = [p.name for vid, p in sorted(videos.items()) if vid not in known][:5]
    print(f"[ОШИБКА] из {len(videos)} роликов на диске в каталоге {common.repo_path('catalog').name} "
          f"нашлось только {matched}. Скорее всего, имена файлов испорчены при распаковке:")
    for name in sample:
        print(f"    {name!r}")
    print("Ожидаются имена вида 'Х-13.mov'. Распакуйте архив заново с явной кодировкой имён:")
    print("    brew install unar")
    print("    unar -e cp866 open.zip      # если имена всё ещё битые: unar -e cp1251 open.zip")
    print("и положите ролики в data/raw/open/videos/ (старые файлы удалите).")
    return True


def main() -> int:
    setup_console()
    parser = argparse.ArgumentParser(
        description="Шаг A1: sha256, параметры видео, дубли и метки → data/manifest.csv")
    parser.add_argument("--videos-dir", type=Path, default=None,
                        help="папка с роликами (по умолчанию data_dir/raw/open/videos)")
    parser.add_argument("--labels", type=Path, default=None,
                        help="open_labels.xlsx организаторов (по умолчанию data_dir/raw/open/open_labels.xlsx)")
    parser.add_argument("--force", action="store_true", help="пересчитать, даже если манифест актуален")
    args = parser.parse_args()

    started = time.perf_counter()
    issues = Issues()
    videos_dir = (args.videos_dir or common.data_path("raw_videos")).expanduser()
    labels_path = (args.labels or common.data_path("raw_labels")).expanduser()
    manifest_path = common.data_path("manifest")

    if not videos_dir.is_dir():
        print(f"[ОШИБКА] нет папки с роликами {videos_dir}.\n"
              "Распакуйте open.zip так, чтобы получилось data/raw/open/videos/*.mov "
              "(или задайте KASSA_DATA_DIR / --videos-dir).")
        return 2
    print(f"Ролики: {videos_dir}")
    videos = list_videos(videos_dir, issues)
    if not videos:
        print(f"[ОШИБКА] в {videos_dir} нет файлов {', '.join(sorted(VIDEO_SUFFIXES))}")
        return 2

    try:
        catalog = common.load_catalog()
    except Exception as exc:  # noqa: BLE001
        issues.error(f"каталог {common.repo_path('catalog')} не прочитан: {exc}")
        catalog = {}
    if names_look_broken(videos, catalog):
        return 2

    code_inputs = [Path(__file__).resolve(), Path(common.__file__).resolve(), common.CONFIG_DIR / "pipeline.yaml"]
    if not args.force and manifest_is_fresh(manifest_path, videos,
                                            [labels_path, common.repo_path("catalog"), *code_inputs]):
        print("Манифест актуален — пересчёт не нужен (--force, чтобы пересчитать).")
        print_summary(read_manifest(manifest_path), manifest_path, None, time.perf_counter() - started)
        return 0

    print(f"Разметка: {labels_path}")
    xlsx = read_xlsx(labels_path, issues)

    print(f"Считаю sha256 и параметры {len(videos)} роликов...")
    sha: dict[str, str] = {}
    meta: dict[str, VideoMeta] = {}
    for vid in sorted(videos, key=vid_key):
        path = videos[vid]
        try:
            sha[vid] = common.sha256_file(path)
            meta[vid] = probe_video(path)
        except Exception as exc:  # noqa: BLE001 — один битый файл не останавливает остальные
            sha.pop(vid, None)
            issues.error(f"{path.name}: не прочитан ({exc})")
            continue
        m = meta[vid]
        print(f"  {vid:<6} {path.name:<10} {m.duration_s:7.2f} с  {m.fps:6.2f} к/с  {m.n_frames:5d} кадров  "
              f"{m.width}x{m.height}  [{m.backend}]")
    if not meta:
        print("[ОШИБКА] ни один ролик не прочитан — проверьте установку PyAV/OpenCV (00_check_env.py)")
        return 2
    readable = {v: p for v, p in videos.items() if v in meta}

    dup_of = assign_duplicates(readable, sha, issues)
    cross_check(readable, dup_of, catalog, xlsx, issues)
    rows = build_rows(readable, sha, meta, dup_of, catalog, xlsx)

    text = render_csv(rows)
    if manifest_path.is_file() and manifest_path.read_text(encoding="utf-8") == text:
        os.utime(manifest_path)  # содержимое то же — отмечаем, что манифест проверен
        print("Содержимое манифеста не изменилось.")
    else:
        write_atomic(manifest_path, text)
        print(f"Записан {manifest_path}")

    print_summary(rows, manifest_path, issues, time.perf_counter() - started)
    return 1 if issues.errors else 0


if __name__ == "__main__":
    sys.exit(main())
