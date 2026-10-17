"""Шаг A2: кадры из роликов → data/frames/<video_id>/*.jpg + data/frames/index.csv.

Для каждого ролика из data/manifest.csv, кроме дублей (duplicate_of не пусто):
  * общий шаг: frames.base_fps кадров в секунду по всему ролику (t = 0, 1/base_fps, ...);
  * плотнее у событий: frames.event_fps кадров в секунду в окне ±frames.event_window_sec
    вокруг каждого таймкода нарушения (колонка events манифеста = каталог событий);
  * для каждого целевого момента берётся ближайший по времени кадр. Ролик декодируется
    последовательно (PyAV, без перемотки): для H.264 .mov с B-кадрами и переменной частотой
    это надёжнее seek и всё равно быстро — ролики короткие, 464x848;
  * кадры сохраняются в родном разрешении (с учётом поворота из метаданных), JPEG с качеством
    frames.jpeg_quality, имя — common.frame_stem(video_id, t) + '.jpg', t — время кадра от начала ролика;
  * почти одинаковые кадры внутри ролика отбрасываются: pHash, расстояние Хэмминга
    <= frames.dedup_phash_distance до любого уже оставленного кадра этого ролика (отрицательное
    значение — без отсева). Размер хэша — frames.dedup_phash_size (по умолчанию 16, то есть 256 бит):
    у неподвижной камеры стандартный 64-битный pHash почти не меняется, когда люди двигаются
    (медиана расстояния между кадрами через 1 с — 2 бита), и порог 4 оставлял ~180 кадров на 40 роликов.
    С 256 битами и порогом 4 остаётся ~1,2 тыс. из ~1,7 тыс. кандидатов.
    Кадр, ближайший к таймкоду события, не отбрасывается никогда; остальные кадры окна события
    имеют приоритет перед кадрами общего шага.

index.csv: frame_path (относительно data_dir), video_id, t_sec, reason (base|event), scene, width, height.
reason=event — кадр попал в окно события.

Запуск из корня репозитория:
    python training/scripts/02_extract_frames.py
    python training/scripts/02_extract_frames.py --videos X-13,Х-14     # только эти ролики (Х можно кириллицей)
    python training/scripts/02_extract_frames.py --base-fps 2 --event-fps 6
    python training/scripts/02_extract_frames.py --force                 # извлечь заново

Повторный запуск пропускает ролики, извлечённые с теми же параметрами (метка
frames/<video_id>/_extract.json). При повторном извлечении строки ролика в index.csv
заменяются, а его старые кадры, не попавшие в новый набор, удаляются — кроме кадров,
для которых уже есть проверенная разметка data/labels/<video_id>/<stem>.txt: они остаются
на диске и в индексе. Коды возврата: 0 — успех, 1 — часть роликов не обработана,
2 — нет манифеста, неверные аргументы или не установлены нужные пакеты.
"""
from __future__ import annotations

import sys, pathlib  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import csv
import io
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

INDEX_COLUMNS = ["frame_path", "video_id", "t_sec", "reason", "scene", "width", "height"]
MARKER_NAME = "_extract.json"
ALGO_VERSION = 1             # увеличить при изменении логики отбора — ролики извлекутся заново
END_TOLERANCE_SEC = 0.5      # цель за последним кадром дальше этого — отбрасывается
DEFAULT_PHASH_SIZE = 16      # pHash 16x16 = 256 бит (frames.dedup_phash_size в pipeline.yaml переопределяет)


@dataclass(order=True)
class Target:
    t: float
    reason: str = field(compare=False)       # base | event
    anchor: bool = field(compare=False)      # кадр, ближайший к таймкоду события


@dataclass
class Candidate:
    t: float
    image_jpeg: bytes
    phash: object
    width: int
    height: int
    reasons: set[str] = field(default_factory=set)
    anchor: bool = False

    @property
    def reason(self) -> str:
        return "event" if "event" in self.reasons else "base"


@dataclass
class VideoResult:
    video_id: str
    rows: list[dict[str, str]]
    n_candidates: int
    n_dropped: int
    n_removed_stale: int
    n_kept_reviewed: int
    seconds: float
    backend: str


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


def rel_to_data(path: Path) -> str:
    return path.resolve().relative_to(common.data_dir().resolve()).as_posix()


def parse_events(text: str) -> list[float]:
    return sorted(float(x) for x in (text or "").split(";") if x.strip())


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_index(path: Path, rows: list[dict[str, str]]) -> None:
    rows = sorted(rows, key=lambda r: (vid_key(r["video_id"]), float(r["t_sec"])))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=INDEX_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomic(path, buf.getvalue())


# --------------------------------------------------------------------------- targets

def build_targets(duration: float, events: list[float], base_fps: float, event_fps: float,
                  window: float) -> tuple[list[Target], list[str]]:
    """Целевые моменты (сек от начала ролика) и предупреждения о событиях за пределами ролика."""
    warnings: list[str] = []
    targets: list[Target] = []
    n_base = max(1, math.ceil(duration * base_fps - 1e-6)) if duration > 0 else 0
    targets += [Target(i / base_fps, "base", False) for i in range(n_base)]

    half = int(round(window * event_fps))
    for e in events:
        if duration > 0 and e >= duration:
            warnings.append(f"событие {e:g} с за концом ролика ({duration:.2f} с) — берётся последний кадр")
        anchor_t = min(max(e, 0.0), max(duration - 1e-3, 0.0)) if duration > 0 else max(e, 0.0)
        targets.append(Target(anchor_t, "event", True))
        for k in range(-half, half + 1):
            if k == 0:
                continue
            t = e + k / event_fps
            if t < 0 or (duration > 0 and t >= duration):
                continue
            targets.append(Target(t, "event", False))
    targets.sort()
    return targets, warnings


# --------------------------------------------------------------------------- decoding

FrameIter = Iterator[tuple[float, Callable[[], "object"]]]


def _rotate(image, rotation: int):
    """Поворот PIL-изображения по матрице отображения (угол против часовой стрелки)."""
    from PIL import Image

    rotation %= 360
    if rotation == 90:
        return image.transpose(Image.Transpose.ROTATE_90)
    if rotation == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    if rotation == 270:
        return image.transpose(Image.Transpose.ROTATE_270)
    return image


def iter_frames_pyav(path: Path) -> FrameIter:
    """(t от первого кадра, ленивое получение RGB PIL-изображения) для каждого кадра по порядку показа."""
    import av

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        rate = stream.average_rate or stream.guessed_rate
        nominal_fps = float(rate) if rate else 30.0
        t0: float | None = None
        for idx, frame in enumerate(container.decode(stream)):
            t_abs = frame.time if frame.time is not None else idx / nominal_fps
            if t0 is None:
                t0 = t_abs
            try:
                rotation = int(round(float(getattr(frame, "rotation", 0) or 0)))
            except (TypeError, ValueError):
                rotation = 0
            yield t_abs - t0, (lambda f=frame, r=rotation: _rotate(f.to_image(), r))


def iter_frames_cv2(path: Path) -> FrameIter:
    import cv2
    from PIL import Image

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("OpenCV не открыл файл")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    try:
        idx = 0
        t0: float | None = None
        while True:
            ok, bgr = cap.read()
            if not ok or bgr is None:
                break
            pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            t_abs = pos_ms / 1000.0 if pos_ms and pos_ms > 0 else idx / fps
            if t0 is None:
                t0 = t_abs
            rgb = bgr[:, :, ::-1].copy()
            yield t_abs - t0, (lambda a=rgb: Image.fromarray(a))
            idx += 1
    finally:
        cap.release()


def pick_backend() -> tuple[str, Callable[[Path], FrameIter]]:
    try:
        import av  # noqa: F401
        return "pyav", iter_frames_pyav
    except ImportError:
        pass
    import cv2  # noqa: F401  — ImportError уйдёт наверх и станет понятной ошибкой
    return "cv2", iter_frames_cv2


# --------------------------------------------------------------------------- per-video work

def collect_candidates(frames: FrameIter, targets: list[Target], jpeg_quality: int,
                       phash_size: int) -> dict[float, Candidate]:
    """Один проход по кадрам: каждой цели — ближайший кадр. Ключ — время кадра."""
    import imagehash

    selected: dict[float, Candidate] = {}

    def assign(t: float, get_image: Callable[[], object], target: Target) -> None:
        cand = selected.get(t)
        if cand is None:
            image = get_image()
            if image.mode != "RGB":
                image = image.convert("RGB")
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=jpeg_quality)
            cand = Candidate(t, buf.getvalue(), imagehash.phash(image, hash_size=phash_size), image.width, image.height)
            selected[t] = cand
        cand.reasons.add(target.reason)
        cand.anchor = cand.anchor or target.anchor

    ti = 0
    prev: tuple[float, Callable[[], object]] | None = None
    for t, get_image in frames:
        while ti < len(targets) and targets[ti].t <= t:
            target = targets[ti]
            if prev is not None and (target.t - prev[0]) <= (t - target.t):
                assign(prev[0], prev[1], target)
            else:
                assign(t, get_image, target)
            ti += 1
        prev = (t, get_image)
        if ti >= len(targets):
            break
    # цели после последнего кадра (длительность в контейнере чуть больше реальной)
    while ti < len(targets) and prev is not None:
        target = targets[ti]
        if target.anchor or target.t - prev[0] <= END_TOLERANCE_SEC:
            assign(prev[0], prev[1], target)
        ti += 1
    return selected


def dedup(candidates: list[Candidate], max_distance: int) -> tuple[list[Candidate], int]:
    """Оставить кадры, далёкие по pHash от уже оставленных. Якоря событий остаются всегда."""
    if max_distance < 0:
        return sorted(candidates, key=lambda c: c.t), 0
    order = sorted(candidates, key=lambda c: (not c.anchor, c.reason != "event", c.t))
    kept: list[Candidate] = []
    dropped = 0
    for cand in order:
        if cand.anchor or all((cand.phash - k.phash) > max_distance for k in kept):
            kept.append(cand)
        else:
            dropped += 1
    return sorted(kept, key=lambda c: c.t), dropped


def extract_video(row: dict[str, str], params: dict, frames_root: Path, labels_root: Path,
                  old_rows: list[dict[str, str]], backend: tuple[str, Callable[[Path], FrameIter]]
                  ) -> VideoResult:
    started = time.perf_counter()
    vid = row["video_id"]
    video_path = Path(row["path"])
    if not video_path.is_absolute():
        video_path = common.data_dir() / video_path
    if not video_path.is_file():
        raise FileNotFoundError(f"нет файла {video_path}")

    duration = float(row.get("duration_s") or 0)
    targets, warnings = build_targets(duration, params["events"], params["base_fps"],
                                      params["event_fps"], params["event_window_sec"])
    for w in warnings:
        print(f"  [!!] {vid}: {w}")

    backend_name, iter_frames = backend
    frames = iter_frames(video_path)
    try:
        candidates = collect_candidates(frames, targets, params["jpeg_quality"], params["dedup_phash_size"])
    finally:
        frames.close()  # декодирование могло остановиться раньше конца — закрыть файл сразу
    if not candidates:
        raise RuntimeError("не получено ни одного кадра")
    kept, n_dropped = dedup(list(candidates.values()), params["dedup_phash_distance"])

    out_dir = frames_root / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    scene = row.get("scene", "")
    rows: list[dict[str, str]] = []
    new_names: set[str] = set()
    for cand in kept:
        stem = common.frame_stem(vid, cand.t)
        out = out_dir / f"{stem}.jpg"
        write_bytes_atomic(out, cand.image_jpeg)
        new_names.add(out.name)
        rows.append({
            "frame_path": rel_to_data(out),
            "video_id": vid,
            "t_sec": f"{cand.t:.3f}",
            "reason": cand.reason,
            "scene": scene,
            "width": str(cand.width),
            "height": str(cand.height),
        })

    # старые кадры этого ролика, не вошедшие в новый набор
    old_by_name = {Path(r["frame_path"]).name: r for r in old_rows}
    stem_re = re.compile(rf"^{re.escape(vid)}_t(\d{{7}})\.jpg$")
    removed = kept_reviewed = 0
    for old in sorted(out_dir.glob("*.jpg")):
        m = stem_re.match(old.name)
        if not m or old.name in new_names:
            continue
        if (labels_root / vid / f"{old.stem}.txt").exists():
            kept_reviewed += 1
            prev_row = old_by_name.get(old.name)
            if prev_row is None:
                from PIL import Image
                with Image.open(old) as im:
                    w, h = im.size
                prev_row = {"frame_path": rel_to_data(old), "video_id": vid,
                            "t_sec": f"{int(m.group(1)) / 1000:.3f}", "reason": "base",
                            "scene": scene, "width": str(w), "height": str(h)}
            rows.append(prev_row)
        else:
            old.unlink()
            removed += 1

    marker = {
        "video_id": vid,
        "sha256": row.get("sha256", ""),
        "params": params,
        "backend": backend_name,
        "n_frames_saved": len(rows),
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    write_text_atomic(out_dir / MARKER_NAME, json.dumps(marker, ensure_ascii=False, indent=2) + "\n")
    return VideoResult(vid, rows, len(candidates), n_dropped, removed, kept_reviewed,
                       time.perf_counter() - started, backend_name)


def is_up_to_date(row: dict[str, str], params: dict, frames_root: Path,
                  index_rows: list[dict[str, str]]) -> bool:
    marker_path = frames_root / row["video_id"] / MARKER_NAME
    if not marker_path.is_file() or not index_rows:
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if marker.get("params") != params or marker.get("sha256") != row.get("sha256", ""):
        return False
    data_dir = common.data_dir()
    return all((data_dir / r["frame_path"]).is_file() for r in index_rows)


# --------------------------------------------------------------------------- main

def main() -> int:
    setup_console()
    cfg = common.pipeline_config().get("frames", {})
    parser = argparse.ArgumentParser(
        description="Шаг A2: кадры из роликов манифеста → data/frames/<video_id>/*.jpg + index.csv")
    parser.add_argument("--videos", default="",
                        help="только эти ролики через запятую, например X-1,X-2 (Х можно кириллицей)")
    parser.add_argument("--base-fps", type=float, default=None,
                        help=f"кадров в секунду по всему ролику (по умолчанию {cfg.get('base_fps', 1.0)})")
    parser.add_argument("--event-fps", type=float, default=None,
                        help=f"кадров в секунду в окне события (по умолчанию {cfg.get('event_fps', 4.0)})")
    parser.add_argument("--force", action="store_true", help="извлечь заново, даже если кадры уже есть")
    args = parser.parse_args()

    params = {
        "base_fps": float(args.base_fps if args.base_fps is not None else cfg.get("base_fps", 1.0)),
        "event_fps": float(args.event_fps if args.event_fps is not None else cfg.get("event_fps", 4.0)),
        "event_window_sec": float(cfg.get("event_window_sec", 3.0)),
        "jpeg_quality": int(cfg.get("jpeg_quality", 92)),
        "dedup_phash_distance": int(cfg.get("dedup_phash_distance", 4)),
        "dedup_phash_size": int(cfg.get("dedup_phash_size", DEFAULT_PHASH_SIZE)),
        "algo": ALGO_VERSION,
    }
    if params["base_fps"] <= 0 or params["event_fps"] <= 0:
        print("[ОШИБКА] --base-fps и --event-fps должны быть больше 0")
        return 2
    if not 1 <= params["jpeg_quality"] <= 100:
        print("[ОШИБКА] frames.jpeg_quality должно быть от 1 до 100")
        return 2
    if params["dedup_phash_size"] < 2:
        print("[ОШИБКА] frames.dedup_phash_size должно быть не меньше 2")
        return 2
    if cfg.get("keep_native_resolution", True) is False:
        print("  [!!] keep_native_resolution: false не поддерживается — кадры сохраняются в родном разрешении")

    missing = []
    for module in ("imagehash", "PIL"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    try:
        backend = pick_backend()
    except ImportError:
        missing.append("av (или opencv-python)")
        backend = None
    if missing:
        print("[ОШИБКА] не установлены: " + ", ".join(missing)
              + " — uv pip install -r training/requirements.txt")
        return 2
    if backend[0] != "pyav":
        print("  [!!] PyAV не установлен — декодирование через OpenCV")

    manifest_path = common.data_path("manifest")
    if not manifest_path.is_file():
        print(f"[ОШИБКА] нет {manifest_path} — сначала запустите training/scripts/01_prepare_raw.py")
        return 2
    manifest = read_csv(manifest_path)
    by_id = {r["video_id"]: r for r in manifest}

    selected_ids = [r["video_id"] for r in manifest]
    if args.videos.strip():
        requested = [common.norm_video_id(v) for v in args.videos.split(",") if v.strip()]
        unknown = [v for v in requested if v not in by_id]
        if unknown:
            print(f"[ОШИБКА] нет в манифесте: {', '.join(unknown)}")
            return 2
        selected_ids = list(dict.fromkeys(requested))
    todo = []
    for vid in sorted(selected_ids, key=vid_key):
        original = by_id[vid].get("duplicate_of", "")
        if original:
            if args.videos.strip():
                print(f"  {vid} — дубль {original}, отдельно не извлекается (кадры берутся из {original})")
            continue
        todo.append(by_id[vid])

    frames_root = common.data_path("frames")
    labels_root = common.data_path("labels")
    index_path = frames_root / "index.csv"
    index_rows = read_csv(index_path) if index_path.is_file() else []

    print(f"Кадры: {frames_root}")
    print(f"Параметры: {params['base_fps']:g} к/с по ролику, {params['event_fps']:g} к/с в окне "
          f"±{params['event_window_sec']:g} с вокруг событий, JPEG {params['jpeg_quality']}, "
          f"pHash{params['dedup_phash_size'] ** 2} <= {params['dedup_phash_distance']}, декодер {backend[0]}")

    started = time.perf_counter()
    done: list[VideoResult] = []
    skipped: list[str] = []
    failed: list[str] = []
    for row in todo:
        vid = row["video_id"]
        vparams = dict(params, events=parse_events(row.get("events", "")))
        old_rows = [r for r in index_rows if r["video_id"] == vid]
        if not args.force and is_up_to_date(row, vparams, frames_root, old_rows):
            skipped.append(vid)
            continue
        try:
            result = extract_video(row, vparams, frames_root, labels_root, old_rows, backend)
        except Exception as exc:  # noqa: BLE001 — один ролик не останавливает остальные
            failed.append(vid)
            print(f"  [ОШИБКА] {vid}: {type(exc).__name__}: {exc}")
            continue
        done.append(result)
        index_rows = [r for r in index_rows if r["video_id"] != vid] + result.rows
        write_index(index_path, index_rows)  # после каждого ролика: прерванный запуск продолжится
        n_event = sum(1 for r in result.rows if r["reason"] == "event")
        extra = []
        if result.n_removed_stale:
            extra.append(f"удалено старых {result.n_removed_stale}")
        if result.n_kept_reviewed:
            extra.append(f"оставлено с проверенной разметкой {result.n_kept_reviewed}")
        print(f"  {vid:<6} {float(row.get('duration_s') or 0):6.1f} с: кандидатов {result.n_candidates:4d}, "
              f"сохранено {len(result.rows):4d} (у событий {n_event:3d}), дублей отброшено "
              f"{result.n_dropped:4d}  {result.seconds:5.1f} с" + (" — " + ", ".join(extra) if extra else ""))

    total = len(index_rows)
    total_event = sum(1 for r in index_rows if r["reason"] == "event")
    n_videos = len({r["video_id"] for r in index_rows})
    elapsed = time.perf_counter() - started
    print("\nИтог:")
    print(f"  обработано роликов {len(done)}, пропущено (уже извлечены) {len(skipped)}, с ошибкой {len(failed)}"
          + (f": {', '.join(failed)}" if failed else ""))
    if done:
        print(f"  в этом запуске: кандидатов {sum(r.n_candidates for r in done)}, сохранено "
              f"{sum(len(r.rows) for r in done)}, дублей отброшено {sum(r.n_dropped for r in done)}")
    print(f"  index.csv: {total} кадров из {n_videos} роликов (у событий {total_event}, общий шаг "
          f"{total - total_event})  ({elapsed:.1f} с)")
    print(f"  {index_path}")
    if skipped and not done and not failed:
        print("  Всё уже извлечено — --force, чтобы извлечь заново.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
