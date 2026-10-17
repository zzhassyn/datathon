"""Шаг A3: автоматическая предварительная разметка кадров → data/prelabels/.

Что делает (для каждого кадра из data/frames/index.csv):
  1. COCO-модель YOLO26 (pipeline.yaml → prelabel.coco_model, по умолчанию yolo26x.pt) находит
     классы, у которых в configs/classes.yaml есть подсказка `coco` (люди, ноутбук, телефон,
     посуда, сумки). Имена классов COCO переводятся в наши по спискам `prelabel.coco`.
  2. Grounding DINO (prelabel.gdino_model, --gdino-model) ищет остальные классы по текстовым
     подсказкам `prelabel.gdino`. Подсказки склеиваются в один запрос «a. b. c.»; если он длиннее
     лимита токенов модели (config.max_text_len), запрос режется на части. Каждая рамка
     относится к той подсказке, у токенов которой среднее по логитам модели выше, и через неё —
     к нашему классу. Если лучшая подсказка обгоняет лучшую подсказку другого класса меньше чем
     на CLASS_MARGIN (0.05), класс решают только различающие токены — те, которых нет в подсказках
     других классов запроса («open» против «closed»/«grey», а не общие «metal», «safe», «door»).
     Уверенность рамки — максимум по различающим токенам её подсказки (если таких нет — среднее):
     общий с другим классом токен ничего не говорит о том, какой это класс.
     Запасной путь — processor.post_process_grounded_object_detection
     (поддерживаются обе версии сигнатуры: threshold / box_threshold, text_labels).
  3. Внутри каждого класса — NMS (prelabel.nms_iou); совсем мелкие рамки
     (< prelabel.min_box_area_frac площади кадра) и рамки DINO почти на весь кадр отбрасываются.
     Взаимоисключающие классы (safe_open / safe_closed): из двух рамок с IoU > 0.5 остаётся
     более уверенная.
  4. Неподвижные предметы (`static: true` в classes.yaml: сейф закрыт, ноутбук, дверь, машинка,
     детектор). По prelabel.static_keyframes кадрам, равномерно расставленным по времени ролика,
     берётся медианная рамка; она пишется во ВСЕ кадры ролика с source=static. Рамка нужна
     не меньше чем в --static-min-frac доле опорных кадров, иначе считается, что предмета нет.
     Больше `max_instances` (classes.yaml) рамок класса на ролик не бывает: остаются те, что
     находились чаще (затем — увереннее); так разные подсказки одного предмета не дают двух рамок.
     Сейф: если DINO в кадре видит открытый сейф (safe_open ≥ --safe-open-threshold,
     prelabel.safe_open_threshold), в кадре остаётся рамка safe_open этого кадра, а неподвижная
     рамка safe_closed туда не пишется.
  5. Для каждого кадра пишутся:
       data/prelabels/<video_id>/<stem>.txt   — YOLO: «cls cx cy w h» в долях кадра;
       data/prelabels/<video_id>/<stem>.json  — те же рамки в том же порядке, у каждой
                                                cls, name, score, source (coco|gdino|static),
                                                phrase, xywhn, xyxy (пиксели);
       data/prelabels/<video_id>/_static.json — кэш неподвижных рамок ролика.

Запуск из корня репозитория:
    python training/scripts/03_prelabel.py                              # все кадры
    python training/scripts/03_prelabel.py --videos X-1 X-13 --limit 20 # быстрый тест
    python training/scripts/03_prelabel.py --gdino-model IDEA-Research/grounding-dino-tiny --device cpu
    python training/scripts/03_prelabel.py --force                      # переразметить заново

Повторный запуск пропускает кадры, у которых уже есть .txt и .json; кэш неподвижных рамок
пересчитывается, только если поменялись параметры. --force — всё заново.
На MPS обе модели работают в float32; операции, которых нет в MPS, уходят на CPU
(PYTORCH_ENABLE_MPS_FALLBACK=1). Коды возврата: 0 — успех, 1 — часть кадров не прочиталась
или ошибка модели, 2 — нет index.csv / нет кадров / неверные аргументы.
"""
from __future__ import annotations

import sys, pathlib  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kassa_train import common

import argparse
import csv
import hashlib
import inspect
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

# Операции, которых нет в MPS, выполняются на CPU. Нужно выставить до импорта torch.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

STATIC_FILE = "_static.json"
SCHEMA = 2                  # 2: различающие токены DINO, взаимоисключающие классы, max_instances
DEFAULT_SAFE_OPEN_THRESHOLD = 0.45  # если в pipeline.yaml нет prelabel.safe_open_threshold
CLASS_MARGIN = 0.05         # отрыв (по среднему токенов) лучшей подсказки от лучшей подсказки другого класса
EXCLUSIVE_IOU = 0.5         # safe_open и safe_closed с IoU больше этого — один предмет, остаётся более уверенная
EXCLUSIVE_CLASSES = [("safe_open", "safe_closed")]
STATIC_MATCH_IOU = 0.3      # рамки опорных кадров с IoU ≥ этого считаются одним неподвижным предметом
STATIC_CONTAIN = 0.8        # неподвижная рамка почти целиком внутри другой рамки того же класса — дубль
OPEN_SAFE_OVERLAP = 0.3     # открытый сейф перекрывает неподвижный safe_closed хотя бы на эту долю
GDINO_MAX_AREA_FRAC = 0.9   # рамка DINO почти на весь кадр — типичный ложный ответ


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
    """['X-1,Х-13', 'X-2'] → {'X-1', 'X-13', 'X-2'}; дубли (X-47) → оригинал."""
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


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return im.convert("RGB")


# --------------------------------------------------------------------------- index

@dataclass
class Frame:
    rel_path: str     # относительно data_dir, POSIX
    video_id: str
    t_sec: float
    reason: str       # base | event
    scene: str
    width: int
    height: int

    @property
    def stem(self) -> str:
        return Path(self.rel_path).stem

    @property
    def path(self) -> Path:
        return common.data_dir() / self.rel_path


def load_index(index_path: Path) -> list[Frame]:
    frames: list[Frame] = []
    with open(index_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rel = (row.get("frame_path") or "").strip().replace("\\", "/")
            if not rel:
                continue
            vid = common.norm_video_id(row.get("video_id") or Path(rel).parent.name)
            if vid in common.DUPLICATES:
                continue  # дубли не размечаются отдельно
            frames.append(Frame(rel, vid, _to_float(row.get("t_sec")), (row.get("reason") or "").strip(),
                                (row.get("scene") or "").strip(), _to_int(row.get("width")),
                                _to_int(row.get("height"))))
    frames.sort(key=lambda fr: (vid_key(fr.video_id), fr.t_sec, fr.rel_path))
    return frames


def group_by_video(frames: Iterable[Frame]) -> dict[str, list[Frame]]:
    out: dict[str, list[Frame]] = defaultdict(list)
    for fr in frames:
        out[fr.video_id].append(fr)
    return dict(sorted(out.items(), key=lambda kv: vid_key(kv[0])))


# --------------------------------------------------------------------------- classes

def normalize_phrase(text: str) -> str:
    """Подсказка DINO: нижний регистр, без точек (точка — разделитель подсказок), один пробел."""
    return re.sub(r"\s+", " ", str(text).replace(".", " ")).strip().lower()


@dataclass
class ClassSpec:
    names: list[str]
    coco: dict[str, int]              # имя класса COCO → наш id
    gdino: list[tuple[str, int]]      # (подсказка, наш id)
    static: set[int]
    safe_closed: int | None
    safe_open: int | None
    max_instances: dict[int, int]         # неподвижный класс → не больше стольких рамок на ролик
    exclusive: list[tuple[int, int]]      # пары взаимоисключающих классов


def load_class_spec() -> ClassSpec:
    classes = common.load_classes()
    coco: dict[str, int] = {}
    gdino: list[tuple[str, int]] = []
    seen_phrases: dict[str, int] = {}
    for c in classes:
        pl = c.get("prelabel") or {}
        for name in pl.get("coco") or []:
            key = str(name).strip().lower()
            if key in coco and coco[key] != c["id"]:
                raise SystemExit(f"[x] classes.yaml: COCO-класс {name!r} указан у двух классов")
            coco[key] = c["id"]
        for raw in pl.get("gdino") or []:
            phrase = normalize_phrase(raw)
            if not phrase:
                continue
            if phrase in seen_phrases and seen_phrases[phrase] != c["id"]:
                raise SystemExit(f"[x] classes.yaml: подсказка {raw!r} указана у двух классов")
            if phrase not in seen_phrases:
                seen_phrases[phrase] = c["id"]
                gdino.append((phrase, c["id"]))
    names = [c["name"] for c in classes]

    def opt_id(name: str) -> int | None:
        return names.index(name) if name in names else None

    max_instances: dict[int, int] = {}
    for c in classes:
        if c.get("max_instances") is not None:
            n = int(c["max_instances"])
            if n < 1:
                raise SystemExit(f"[x] classes.yaml: max_instances у {c['name']} должен быть ≥ 1")
            max_instances[c["id"]] = n
    exclusive = [(opt_id(a), opt_id(b)) for a, b in EXCLUSIVE_CLASSES
                 if opt_id(a) is not None and opt_id(b) is not None]
    return ClassSpec(names, coco, gdino, {c["id"] for c in classes if c.get("static")},
                     opt_id("safe_closed"), opt_id("safe_open"), max_instances, exclusive)


# --------------------------------------------------------------------------- boxes

@dataclass
class Det:
    """Рамка в пикселях кадра."""
    cls: int
    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    source: str        # coco | gdino | static
    phrase: str = ""   # имя класса COCO или подсказка DINO

    def xyxy(self) -> tuple[float, float, float, float]:
        return self.x1, self.y1, self.x2, self.y2


def _xyxy(b) -> tuple[float, float, float, float]:
    return b.xyxy() if isinstance(b, Det) else (float(b[0]), float(b[1]), float(b[2]), float(b[3]))


def box_area(b) -> float:
    x1, y1, x2, y2 = _xyxy(b)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def intersection(a, b) -> float:
    ax1, ay1, ax2, ay2 = _xyxy(a)
    bx1, by1, bx2, by2 = _xyxy(b)
    return max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))


def iou(a, b) -> float:
    inter = intersection(a, b)
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def inter_over_min(a, b) -> float:
    small = min(box_area(a), box_area(b))
    return intersection(a, b) / small if small > 0 else 0.0


def clip_det(d: Det, w: int, h: int) -> Det:
    x1, x2 = sorted((min(max(d.x1, 0.0), w), min(max(d.x2, 0.0), w)))
    y1, y2 = sorted((min(max(d.y1, 0.0), h), min(max(d.y2, 0.0), h)))
    return Det(d.cls, x1, y1, x2, y2, d.score, d.source, d.phrase)


def nms_per_class(dets: list[Det], iou_thr: float) -> list[Det]:
    """Жадный NMS отдельно внутри каждого класса."""
    by_cls: dict[int, list[Det]] = defaultdict(list)
    for d in dets:
        by_cls[d.cls].append(d)
    kept: list[Det] = []
    for cls in sorted(by_cls):
        for d in sorted(by_cls[cls], key=lambda x: -x.score):
            if all(iou(d, k) < iou_thr for k in kept if k.cls == cls):
                kept.append(d)
    return kept


def exclusive_nms(dets: list[Det], pairs: Iterable[tuple[int, int]], iou_thr: float = EXCLUSIVE_IOU) -> list[Det]:
    """Взаимоисключающие классы (сейф открыт / закрыт): из пары рамок с IoU > iou_thr остаётся более уверенная."""
    conflict = {(a, b) for a, b in pairs} | {(b, a) for a, b in pairs}
    if not conflict:
        return dets
    kept: list[Det] = []
    for d in sorted(dets, key=lambda x: -x.score):
        if not any((k.cls, d.cls) in conflict and iou(d, k) > iou_thr for k in kept):
            kept.append(d)
    order = {id(d): i for i, d in enumerate(dets)}
    return sorted(kept, key=lambda d: order[id(d)])


def clean_dets(dets: list[Det], w: int, h: int, min_area_frac: float, nms_iou: float,
               exclusive: Iterable[tuple[int, int]] = ()) -> list[Det]:
    """Обрезка по кадру, отсев мелких и «во весь кадр» рамок, NMS по классам и между взаимоисключающими."""
    frame_area = float(w * h)
    out: list[Det] = []
    for d in dets:
        d = clip_det(d, w, h)
        frac = box_area(d) / frame_area if frame_area > 0 else 0.0
        if frac <= 0 or frac < min_area_frac:
            continue
        if d.source == "gdino" and frac > GDINO_MAX_AREA_FRAC:
            continue
        out.append(d)
    return exclusive_nms(nms_per_class(out, nms_iou), exclusive)


# --------------------------------------------------------------------------- weights

def resolve_coco_weights(name: str) -> str:
    """yolo26x.pt → models/pretrained/yolo26x.pt, если скачан (download_models.sh), иначе — как есть."""
    try:
        from kassa_train.weights import resolve_weight
    except ImportError:
        local = common.repo_path("models") / "pretrained" / name
        return str(local) if local.exists() else name
    return resolve_weight(name)


# --------------------------------------------------------------------------- COCO YOLO26

class CocoDetector:
    def __init__(self, weights: str, device: str, conf: float, imgsz: int, coco_map: dict[str, int]):
        from ultralytics import YOLO

        self.model = YOLO(weights)
        self.device, self.conf, self.imgsz = device, conf, imgsz
        self.names = {int(k): str(v).strip().lower() for k, v in self.model.names.items()}
        self.id_map = {k: coco_map[v] for k, v in self.names.items() if v in coco_map}
        missing = sorted(set(coco_map) - set(self.names.values()))
        if missing:
            print(f"[!] в модели {weights} нет классов COCO: {', '.join(missing)}")
        if not self.id_map:
            raise RuntimeError(f"модель {weights} не знает ни одного COCO-класса из classes.yaml")

    def __call__(self, images: list[Image.Image]) -> list[list[Det]]:
        # half= не передаём: в Ultralytics 8.4 он устарел (quantize), по умолчанию и так float32
        results = self.model.predict(source=images, conf=self.conf, imgsz=self.imgsz, device=self.device,
                                     classes=sorted(self.id_map), verbose=False)
        out: list[list[Det]] = []
        for res in results:
            dets: list[Det] = []
            boxes = res.boxes
            if boxes is not None and len(boxes):
                xyxy = boxes.xyxy.float().cpu().numpy()
                conf = boxes.conf.float().cpu().numpy()
                cls = boxes.cls.int().cpu().numpy()
                for (x1, y1, x2, y2), score, k in zip(xyxy, conf, cls):
                    ours = self.id_map.get(int(k))
                    if ours is not None:
                        dets.append(Det(ours, float(x1), float(y1), float(x2), float(y2), float(score),
                                        "coco", self.names[int(k)]))
            out.append(dets)
        return out


# --------------------------------------------------------------------------- Grounding DINO

def find_sublist(seq: list[int], sub: list[int], start: int = 0) -> int:
    n = len(sub)
    for i in range(start, len(seq) - n + 1):
        if seq[i:i + n] == sub:
            return i
    return -1


def find_phrase_spans(input_ids: list[int], phrase_ids: list[list[int]]) -> list[list[int]] | None:
    """Позиции токенов каждой подсказки в запросе (подсказки идут по порядку). None — не нашлись."""
    spans: list[list[int]] = []
    cursor = 0
    for ids in phrase_ids:
        if not ids:
            return None
        pos = find_sublist(input_ids, ids, cursor)
        if pos < 0:
            return None
        spans.append(list(range(pos, pos + len(ids))))
        cursor = pos + len(ids)
    return spans


def discriminative_positions(input_ids: list[int], spans: list[list[int]], classes: list[int]) -> list[list[int]]:
    """Позиции токенов каждой подсказки, которых нет в подсказках ДРУГИХ классов того же запроса.

    «open safe door» среди «closed metal safe» (safe_closed) и «glass door» (door) → только «open».
    Пустой список — у подсказки нет своих слов (тогда уверенность считается по среднему).
    """
    out: list[list[int]] = []
    for k, span in enumerate(spans):
        other = {input_ids[p] for j, sp in enumerate(spans) if classes[j] != classes[k] for p in sp}
        out.append([p for p in span if input_ids[p] not in other])
    return out


def _take(arr: np.ndarray, idx: np.ndarray) -> np.ndarray:
    return np.take_along_axis(arr, idx[..., None], -1)[..., 0]


def decode_phrase_logits(probs: np.ndarray, boxes: np.ndarray, spans: list[list[int]],
                         phrases: list[str], classes: list[int], sizes: list[tuple[int, int]],
                         box_thr: float, text_thr: float, discr: list[list[int]] | None = None,
                         min_margin: float = CLASS_MARGIN) -> list[list[Det]]:
    """Логиты DINO → рамки с подсказкой.

    probs: (B, Q, T) сигмоида логитов по токенам; boxes: (B, Q, 4) cx, cy, w, h в долях кадра;
    sizes: (h, w) каждого кадра; discr: различающие токены подсказок (discriminative_positions;
    None — все токены подсказки). Подсказка-кандидат — у которой хотя бы один токен ≥ text_thr.
    1. Из кандидатов берётся подсказка с наибольшим средним по своим токенам.
    2. Если она обгоняет лучшую подсказку другого класса меньше чем на min_margin («open metal
       cabinet» 0.43 против «grey metal cabinet» 0.42 — среднее держится на общих metal/cabinet),
       побеждает та из двух, у которой выше различающие токены.
    3. Уверенность рамки — максимум по различающим токенам выбранной подсказки (нет таких —
       среднее по подсказке); рамка остаётся, если уверенность ≥ box_thr.
    """
    if discr is None:
        discr = spans
    peak = np.stack([probs[..., idx].max(-1) for idx in spans], -1)    # (B, Q, K)
    mean = np.stack([probs[..., idx].mean(-1) for idx in spans], -1)   # (B, Q, K)
    disc = np.stack([probs[..., d].max(-1) if d else mean[..., k]
                     for k, d in enumerate(discr)], -1)                # (B, Q, K)
    cand = np.where(peak >= text_thr, mean, -1.0)
    best = cand.argmax(-1)                                             # (B, Q)
    cls_arr = np.asarray(classes)
    other_cand = np.where(cls_arr[best][..., None] == cls_arr, -1.0, cand)
    other = other_cand.argmax(-1)                                      # лучшая подсказка другого класса
    best_mean, other_mean = _take(cand, best), _take(other_cand, other)
    ambiguous = (other_mean >= 0) & (best_mean - other_mean < min_margin)
    chosen = np.where(ambiguous & (_take(disc, other) > _take(disc, best)), other, best)
    score = _take(disc, chosen)
    keep = (_take(cand, chosen) >= 0) & (score >= box_thr)
    out: list[list[Det]] = []
    for b, (h, w) in enumerate(sizes):
        dets: list[Det] = []
        for q in np.nonzero(keep[b])[0]:
            cx, cy, bw, bh = (float(v) for v in boxes[b, q])
            k = int(chosen[b, q])
            dets.append(Det(classes[k], (cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w,
                            (cy + bh / 2) * h, float(score[b, q]), "gdino", phrases[k]))
        out.append(dets)
    return out


_WORD_RE = re.compile(r"[a-z]+")


def match_phrase(label, phrases: list[str]) -> int | None:
    """Ответ post_process (строка или индекс) → индекс подсказки. Строка сравнивается по словам (F1)."""
    if isinstance(label, (int, np.integer)):
        return int(label) if 0 <= int(label) < len(phrases) else None
    words = set(_WORD_RE.findall(str(label).lower()))
    if not words:
        return None
    best, best_f1 = None, 0.0
    for i, phrase in enumerate(phrases):
        pw = set(_WORD_RE.findall(phrase))
        inter = len(words & pw)
        if not inter:
            continue
        prec, rec = inter / len(words), inter / len(pw)
        f1 = 2 * prec * rec / (prec + rec)
        if f1 > best_f1 + 1e-9:  # при равенстве — более ранняя подсказка
            best, best_f1 = i, f1
    return best


@dataclass
class PromptChunk:
    text: str
    phrases: list[str]
    classes: list[int]
    phrase_ids: list[list[int]]
    spans: list[list[int]] | None = None
    discr: list[list[int]] | None = None   # различающие токены каждой подсказки (позиции в запросе)
    spans_checked: bool = False


def build_chunks(prompts: list[tuple[str, int]], token_ids, max_len: int) -> list[PromptChunk]:
    """Подсказки → запросы «a. b. c.» не длиннее max_len токенов (с [CLS]/[SEP] и запасом)."""
    budget = max_len - 2 - 4
    chunks: list[PromptChunk] = []
    cur: list[tuple[str, int, list[int]]] = []
    cur_len = 0

    def flush() -> None:
        if cur:
            chunks.append(PromptChunk(" ".join(p + "." for p, _, _ in cur), [p for p, _, _ in cur],
                                      [c for _, c, _ in cur], [ids for _, _, ids in cur]))

    for phrase, cls in prompts:
        ids = list(token_ids(phrase))
        need = len(ids) + 1  # + точка
        if need > budget:
            raise ValueError(f"подсказка длиннее лимита модели ({max_len} токенов): {phrase!r}")
        if cur and cur_len + need > budget:
            flush()
            cur, cur_len = [], 0
        cur.append((phrase, cls, ids))
        cur_len += need
    flush()
    return chunks


class GDinoDetector:
    def __init__(self, model_id: str, device: str, box_thr: float, text_thr: float,
                 prompts: list[tuple[str, int]], decoder: str = "auto"):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_id)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(model_id)
        # transformers 5 может загрузить веса в dtype чекпойнта; на MPS нужен float32
        self.model = model.float().to(device).eval()
        self.device = device
        self.box_thr, self.text_thr = box_thr, text_thr
        self.decoder = decoder
        tokenizer = getattr(self.processor, "tokenizer", None)
        if tokenizer is None:
            raise RuntimeError(f"у процессора {model_id} нет tokenizer")
        max_len = int(getattr(self.model.config, "max_text_len", 256) or 256)
        self.chunks = build_chunks(
            prompts, lambda text: tokenizer(text, add_special_tokens=False)["input_ids"], max_len)
        self._pp_params = set(inspect.signature(
            self.processor.post_process_grounded_object_detection).parameters)

    def describe(self) -> str:
        return f"{len(self.chunks)} запрос(а) на кадр, decoder={self.decoder}"

    def __call__(self, images: list[Image.Image]) -> list[list[Det]]:
        out: list[list[Det]] = [[] for _ in images]
        sizes = [(im.height, im.width) for im in images]
        for chunk in self.chunks:
            inputs = self.processor(images=images, text=[chunk.text] * len(images), return_tensors="pt")
            inputs = inputs.to(self.device)
            with self.torch.inference_mode():
                outputs = self.model(**inputs)
            ids = [int(v) for v in inputs["input_ids"][0].tolist()]
            if self._use_logits(chunk, ids):
                probs = outputs.logits.float().sigmoid().cpu().numpy()
                boxes = outputs.pred_boxes.float().cpu().numpy()
                dets = decode_phrase_logits(probs, boxes, chunk.spans, chunk.phrases, chunk.classes,
                                            sizes, self.box_thr, self.text_thr, chunk.discr)
            else:
                dets = self._decode_hf(outputs, inputs, chunk, sizes)
            for i, d in enumerate(dets):
                out[i].extend(d)
        return out

    def _use_logits(self, chunk: PromptChunk, input_ids: list[int]) -> bool:
        if self.decoder == "hf":
            return False
        if not chunk.spans_checked:
            chunk.spans = find_phrase_spans(input_ids, chunk.phrase_ids)
            chunk.spans_checked = True
            if chunk.spans is not None:
                chunk.discr = discriminative_positions(input_ids, chunk.spans, chunk.classes)
            if chunk.spans is None:
                if self.decoder == "logits":
                    raise RuntimeError("токены подсказок не найдены в запросе DINO; запустите с --gdino-decoder hf")
                print("[!] токены подсказок не найдены в запросе — DINO разбирается через "
                      "post_process_grounded_object_detection")
        return chunk.spans is not None

    def _decode_hf(self, outputs, inputs, chunk: PromptChunk, sizes: list[tuple[int, int]]) -> list[list[Det]]:
        """Штатный разбор transformers. Сигнатура менялась: box_threshold → threshold (4.4x),
        добавился text_labels, в ответе text_labels вместо строковых labels."""
        params = self._pp_params
        kwargs: dict = {"target_sizes": sizes}
        if "input_ids" in params:
            kwargs["input_ids"] = inputs["input_ids"]
        if "threshold" in params:
            kwargs["threshold"] = self.box_thr
        elif "box_threshold" in params:
            kwargs["box_threshold"] = self.box_thr
        if "text_threshold" in params:
            kwargs["text_threshold"] = self.text_thr
        if "text_labels" in params:
            kwargs["text_labels"] = [list(chunk.phrases) for _ in sizes]
        results = self.processor.post_process_grounded_object_detection(outputs, **kwargs)
        out: list[list[Det]] = []
        for res in results:
            labels = res.get("text_labels")
            if labels is None:
                labels = res.get("labels")
            if hasattr(labels, "tolist"):
                labels = labels.tolist()
            dets: list[Det] = []
            for box, score, label in zip(res["boxes"].float().cpu().tolist(),
                                         res["scores"].float().cpu().tolist(), labels or []):
                k = match_phrase(label, chunk.phrases)
                if k is None or float(score) < self.box_thr:
                    continue
                dets.append(Det(chunk.classes[k], *map(float, box), float(score), "gdino", chunk.phrases[k]))
            out.append(dets)
        return out


# --------------------------------------------------------------------------- static objects

@dataclass
class StaticBox:
    cls: int
    box: tuple[float, float, float, float]   # x1, y1, x2, y2 в долях кадра
    score: float
    hits: int

    def to_det(self, w: int, h: int) -> Det:
        x1, y1, x2, y2 = self.box
        return Det(self.cls, x1 * w, y1 * h, x2 * w, y2 * h, self.score, "static")


def pick_keyframes(frames: list[Frame], k: int) -> list[Frame]:
    """k кадров, равномерно расставленных по времени ролика (по возможности из base-кадров)."""
    if k <= 0 or not frames:
        return []
    base = [f for f in frames if f.reason == "base"]
    pool = sorted(base if len(base) >= k else frames, key=lambda f: f.t_sec)
    if len(pool) <= k:
        return pool
    chosen: list[int] = []
    for target in np.linspace(pool[0].t_sec, pool[-1].t_sec, k):
        order = sorted(range(len(pool)), key=lambda i: (abs(pool[i].t_sec - target), i))
        chosen.append(next(i for i in order if i not in chosen))
    return [pool[i] for i in sorted(chosen)]


def compute_static(key_dets: list[tuple[list[Det], int, int]], spec: ClassSpec, min_frac: float,
                   safe_open_thr: float) -> list[StaticBox]:
    """Медианные рамки неподвижных предметов по опорным кадрам ролика."""
    result: list[StaticBox] = []
    for cls in sorted(spec.static):
        usable: list[list[tuple[float, float, float, float, float]]] = []
        for dets, w, h in key_dets:
            if cls == spec.safe_closed and spec.safe_open is not None and any(
                    d.cls == spec.safe_open and d.score >= safe_open_thr for d in dets):
                continue  # сейф в этом кадре открыт — для рамки закрытого сейфа кадр не годится
            usable.append([(d.x1 / w, d.y1 / h, d.x2 / w, d.y2 / h, d.score) for d in dets if d.cls == cls])
        if not usable:
            continue
        min_hits = max(1, math.ceil(min_frac * len(usable) - 1e-9))
        clusters: list[list[tuple[float, float, float, float, float]]] = []
        for boxes in usable:
            taken: set[int] = set()
            for b in sorted(boxes, key=lambda x: -x[4]):
                best_j, best_iou = None, STATIC_MATCH_IOU
                for j, members in enumerate(clusters):
                    if j in taken:
                        continue
                    ref = np.median(np.array(members)[:, :4], axis=0)
                    v = iou(b[:4], ref)
                    if v >= best_iou:
                        best_j, best_iou = j, v
                if best_j is None:
                    clusters.append([b])
                    taken.add(len(clusters) - 1)
                else:
                    clusters[best_j].append(b)
                    taken.add(best_j)
        cands: list[StaticBox] = []
        for members in clusters:
            if len(members) < min_hits:
                continue
            arr = np.array(members)
            med = tuple(float(v) for v in np.median(arr[:, :4], axis=0))
            cands.append(StaticBox(cls, med, float(np.median(arr[:, 4])), len(members)))
        cands.sort(key=lambda s: (s.hits, s.score), reverse=True)
        kept: list[StaticBox] = []
        for s in cands:
            if any(inter_over_min(s.box, k.box) >= STATIC_CONTAIN for k in kept):
                continue  # например, «door handle» внутри «glass door»
            kept.append(s)
        # «closed metal safe» и «grey metal cabinet» могут дать две несовпадающие рамки одного сейфа
        result.extend(kept[:spec.max_instances.get(cls, len(kept))])
    return result


def assemble_frame(dets: list[Det], statics: list[StaticBox], w: int, h: int, spec: ClassSpec,
                   safe_open_thr: float) -> list[Det]:
    """Итоговые рамки кадра: подвижные классы — свои, неподвижные — из статики ролика."""
    out: list[Det] = []
    for d in dets:
        if d.cls in spec.static:
            continue
        if d.cls == spec.safe_open and d.score < safe_open_thr:
            continue
        out.append(d)
    opened = [d for d in out if d.cls == spec.safe_open]
    for s in statics:
        sd = s.to_det(w, h)
        if s.cls == spec.safe_closed and any(inter_over_min(o, sd) >= OPEN_SAFE_OVERLAP for o in opened):
            continue
        out.append(sd)
    out.sort(key=lambda d: (d.cls, -d.score))
    return out


# --------------------------------------------------------------------------- runner

@dataclass
class Settings:
    device: str
    coco_weights: str
    coco_conf: float
    coco_imgsz: int
    gdino_model: str
    box_thr: float
    text_thr: float
    decoder: str
    nms_iou: float
    min_area_frac: float
    static_keyframes: int
    static_min_frac: float
    safe_open_thr: float
    use_coco: bool
    use_gdino: bool
    batch: int

    def fingerprint(self, spec: ClassSpec) -> dict:
        params = {
            "schema": SCHEMA,
            "coco_model": Path(self.coco_weights).name if self.use_coco else None,
            "coco_conf": self.coco_conf, "coco_imgsz": self.coco_imgsz,
            "gdino_model": self.gdino_model if self.use_gdino else None,
            "gdino_box_threshold": self.box_thr, "gdino_text_threshold": self.text_thr,
            "gdino_decoder": self.decoder,
            "nms_iou": self.nms_iou, "min_box_area_frac": self.min_area_frac,
            "static_keyframes": self.static_keyframes, "static_min_frac": self.static_min_frac,
            "safe_open_threshold": self.safe_open_thr,
            "classes": spec.names, "coco_map": spec.coco, "gdino_prompts": spec.gdino,
            "static": sorted(spec.static),
            "max_instances": {str(k): v for k, v in sorted(spec.max_instances.items())},
            "exclusive": [list(pair) for pair in spec.exclusive], "exclusive_iou": EXCLUSIVE_IOU,
            "class_margin": CLASS_MARGIN,
        }
        blob = json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
        params["hash"] = hashlib.sha1(blob).hexdigest()[:12]
        return params


@dataclass
class Detected:
    frame: Frame
    dets: list[Det]          # после NMS, до подстановки статики
    width: int
    height: int
    safe_open_raw: float | None = None   # лучший safe_open до NMS (для подбора safe_open_threshold)


class Runner:
    """Ленивая загрузка моделей и прогон кадров пачками."""

    def __init__(self, s: Settings, spec: ClassSpec):
        self.s, self.spec = s, spec
        self._coco: CocoDetector | None = None
        self._gdino: GDinoDetector | None = None
        self.failed: list[tuple[str, str]] = []

    def ensure_models(self) -> tuple[CocoDetector | None, GDinoDetector | None]:
        if self.s.use_coco and self._coco is None:
            print(f"== COCO: {self.s.coco_weights} на {self.s.device}")
            self._coco = CocoDetector(self.s.coco_weights, self.s.device, self.s.coco_conf,
                                      self.s.coco_imgsz, self.spec.coco)
        if self.s.use_gdino and self._gdino is None:
            print(f"== Grounding DINO: {self.s.gdino_model} на {self.s.device} (float32)")
            self._gdino = GDinoDetector(self.s.gdino_model, self.s.device, self.s.box_thr, self.s.text_thr,
                                        self.spec.gdino, self.s.decoder)
            print(f"   {self._gdino.describe()}")
        return self._coco, self._gdino

    def detect(self, frames: list[Frame]) -> Iterator[Detected]:
        if not frames:
            return
        coco, gdino = self.ensure_models()
        for i in range(0, len(frames), max(1, self.s.batch)):
            batch = frames[i:i + max(1, self.s.batch)]
            ok: list[Frame] = []
            images: list[Image.Image] = []
            for fr in batch:
                try:
                    images.append(load_rgb(fr.path))
                    ok.append(fr)
                except (OSError, ValueError) as e:
                    self.failed.append((fr.rel_path, str(e)))
                    print(f"[!] не прочитан кадр {fr.rel_path}: {e}")
            if not images:
                continue
            # кадры одного ролика одного размера; разные размеры в одной пачке DINO не любит
            groups: dict[tuple[int, int], list[int]] = defaultdict(list)
            for j, im in enumerate(images):
                groups[im.size].append(j)
            raw: list[list[Det]] = [[] for _ in images]
            for idxs in groups.values():
                ims = [images[j] for j in idxs]
                for model in (coco, gdino):
                    if model is None:
                        continue
                    for j, dets in zip(idxs, model(ims)):
                        raw[j].extend(dets)
            for fr, im, dets in zip(ok, images, raw):
                w, h = im.size
                opened = [d.score for d in dets if d.cls == self.spec.safe_open]
                yield Detected(fr, clean_dets(dets, w, h, self.s.min_area_frac, self.s.nms_iou,
                                              self.spec.exclusive), w, h, max(opened) if opened else None)


def frame_outputs(out_root: Path, fr: Frame) -> tuple[Path, Path]:
    d = out_root / fr.video_id
    return d / f"{fr.stem}.txt", d / f"{fr.stem}.json"


def write_frame(out_root: Path, item: Detected, final: list[Det], spec: ClassSpec, params: dict) -> None:
    txt, js = frame_outputs(out_root, item.frame)
    w, h = item.width, item.height
    boxes = [common.Box.from_xyxy(d.cls, d.x1, d.y1, d.x2, d.y2, w, h, d.score, d.source) for d in final]
    common.write_yolo_labels(txt, boxes)
    open_scores = [d.score for d in item.dets if d.cls == spec.safe_open]
    payload = {
        "frame": item.frame.rel_path, "video_id": item.frame.video_id, "t_sec": item.frame.t_sec,
        "reason": item.frame.reason, "scene": item.frame.scene, "width": w, "height": h,
        "params_hash": params["hash"],
        "safe_open_score": round(max(open_scores), 4) if open_scores else None,
        "safe_open_score_raw": round(item.safe_open_raw, 4) if item.safe_open_raw is not None else None,
        "boxes": [
            {"cls": b.cls, "name": spec.names[b.cls], "score": round(b.score, 4), "source": b.source,
             "phrase": d.phrase, "xywhn": [round(b.cx, 6), round(b.cy, 6), round(b.w, 6), round(b.h, 6)],
             "xyxy": [round(d.x1, 1), round(d.y1, 1), round(d.x2, 1), round(d.y2, 1)]}
            for b, d in zip(boxes, final)
        ],
    }
    write_json_atomic(js, payload)  # json последним: пара .txt + .json = кадр готов


def load_static_cache(path: Path, params_hash: str) -> list[StaticBox] | None:
    data = read_json(path)
    if not data or data.get("params_hash") != params_hash:
        return None
    try:
        return [StaticBox(int(b["cls"]), tuple(float(v) for v in b["box"]), float(b["score"]), int(b["hits"]))
                for b in data.get("boxes", [])]
    except (KeyError, TypeError, ValueError):
        return None


def save_static_cache(path: Path, video_id: str, keyframes: list[Frame], statics: list[StaticBox],
                      spec: ClassSpec, params_hash: str) -> None:
    write_json_atomic(path, {
        "video_id": video_id, "params_hash": params_hash,
        "keyframes": [f.stem for f in keyframes],
        "boxes": [{"cls": s.cls, "name": spec.names[s.cls], "box": [round(v, 6) for v in s.box],
                   "score": round(s.score, 4), "hits": s.hits} for s in statics],
    })


def process_video(vid: str, todo: list[Frame], all_frames: list[Frame], runner: Runner, out_root: Path,
                  params: dict, force: bool) -> int:
    """Разметка нужных кадров одного ролика. Возвращает число записанных кадров."""
    s, spec = runner.s, runner.spec
    runner.ensure_models()  # загрузка моделей — до индикатора прогресса
    static_path = out_root / vid / STATIC_FILE
    statics = None if force else load_static_cache(static_path, params["hash"])
    cached: dict[str, Detected] = {}
    if statics is None:
        keyframes = pick_keyframes(all_frames, s.static_keyframes) if spec.static else []
        key_items = list(runner.detect(keyframes))
        for item in key_items:
            cached[item.frame.stem] = item
        statics = compute_static([(it.dets, it.width, it.height) for it in key_items], spec,
                                 s.static_min_frac, s.safe_open_thr)
        save_static_cache(static_path, vid, keyframes, statics, spec, params["hash"])
        found = ", ".join(f"{spec.names[x.cls]}×{x.hits}" for x in statics) or "нет"
        print(f"   {vid}: неподвижные предметы по {len(key_items)} опорным кадрам: {found}")

    written = 0

    def emit(item: Detected) -> None:
        nonlocal written
        final = assemble_frame(item.dets, statics, item.width, item.height, spec, s.safe_open_thr)
        write_frame(out_root, item, final, spec, params)
        written += 1

    rest: list[Frame] = []
    for fr in todo:
        if fr.stem in cached:
            emit(cached[fr.stem])
        else:
            rest.append(fr)
    for item in progress(runner.detect(rest), total=len(rest), desc=vid):
        emit(item)
    return written


def progress(it: Iterable, total: int, desc: str) -> Iterable:
    try:
        from tqdm import tqdm
    except ImportError:
        return it
    return tqdm(it, total=total, desc=desc, unit="кадр", leave=False)


# --------------------------------------------------------------------------- summary

def print_summary(selected: list[Frame], out_root: Path, spec: ClassSpec, params: dict, n_written: int,
                  n_skipped: int, failed: list[tuple[str, str]], elapsed: float) -> None:
    per_cls: Counter = Counter()
    per_cls_frames: Counter = Counter()
    per_src: dict[int, Counter] = defaultdict(Counter)
    stale = 0
    present = 0
    for fr in selected:
        _, js = frame_outputs(out_root, fr)
        data = read_json(js)
        if not data:
            continue
        present += 1
        if data.get("params_hash") != params["hash"]:
            stale += 1
        seen: set[int] = set()
        for b in data.get("boxes", []):
            c = int(b["cls"])
            per_cls[c] += 1
            per_src[c][b.get("source", "?")] += 1
            seen.add(c)
        for c in seen:
            per_cls_frames[c] += 1

    print("\n=== Предварительная разметка: итог ===")
    print(f"кадров выбрано: {len(selected)}; размечено сейчас: {n_written}; "
          f"уже было: {n_skipped}; не прочитано: {len(failed)}; время: {elapsed / 60:.1f} мин")
    print(f"разметка в {out_root} (кадров с разметкой: {present})")
    print(f"{'класс':<18}{'рамок':>7}{'кадров':>8}   источники")
    for c, name in enumerate(spec.names):
        src = ", ".join(f"{k} {v}" for k, v in sorted(per_src[c].items())) or "—"
        print(f"{c:>2} {name:<15}{per_cls[c]:>7}{per_cls_frames[c]:>8}   {src}")
    print(f"всего рамок: {sum(per_cls.values())}")
    empty = [spec.names[c] for c in range(len(spec.names)) if per_cls[c] == 0]
    if empty and present:
        print(f"[!] без рамок: {', '.join(empty)} — проверьте подсказки в classes.yaml или пороги")
    if stale:
        print(f"[!] {stale} кадров размечены с другими параметрами — для единообразия перезапустите с --force")
    for rel, err in failed[:10]:
        print(f"[x] {rel}: {err}")


# --------------------------------------------------------------------------- main

def parse_args(cfg: dict) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--videos", nargs="+", metavar="ID",
                   help="только эти ролики: X-1 X-13 или X-1,X-13 (можно кириллицей Х-13)")
    p.add_argument("--limit", type=int, default=0, metavar="N",
                   help="не больше N кадров (после фильтра --videos) — для проверки; опорные кадры "
                        "неподвижных предметов ролика прогоняются дополнительно")
    p.add_argument("--force", action="store_true", help="переразметить кадры и пересчитать неподвижные рамки")
    p.add_argument("--device", default=None,
                   help=f"cpu | mps | cuda:0 | auto (по умолчанию prelabel.device = {cfg.get('device', 'auto')})")
    p.add_argument("--coco-model", default=None,
                   help=f"веса COCO YOLO26 (по умолчанию prelabel.coco_model = {cfg.get('coco_model')})")
    p.add_argument("--coco-imgsz", type=int, default=864, help="размер входа COCO-модели (кратно 32), по умолчанию 864")
    p.add_argument("--gdino-model", default=None,
                   help=f"модель Grounding DINO (по умолчанию {cfg.get('gdino_model')}; "
                        "для слабого ПК: IDEA-Research/grounding-dino-tiny)")
    p.add_argument("--gdino-decoder", choices=["auto", "logits", "hf"], default="auto",
                   help="как разбирать ответ DINO: logits — по токенам подсказок (точнее), "
                        "hf — post_process_grounded_object_detection, auto — logits, а если не вышло — hf")
    p.add_argument("--batch", type=int, default=2, help="кадров за один прогон модели (по умолчанию 2)")
    p.add_argument("--safe-open-threshold", type=float, default=None,
                   help="порог DINO для «сейф открыт» (по умолчанию prelabel.safe_open_threshold "
                        f"или {DEFAULT_SAFE_OPEN_THRESHOLD})")
    p.add_argument("--static-min-frac", type=float, default=0.5,
                   help="доля опорных кадров, где неподвижный предмет должен найтись (по умолчанию 0.5)")
    p.add_argument("--no-coco", action="store_true", help="не запускать COCO-модель (для отладки)")
    p.add_argument("--no-gdino", action="store_true", help="не запускать Grounding DINO (для отладки)")
    args = p.parse_args()
    if args.limit < 0 or args.batch < 1:
        p.error("--limit ≥ 0, --batch ≥ 1")
    if args.no_coco and args.no_gdino:
        p.error("нельзя одновременно --no-coco и --no-gdino")
    if not 0.0 < args.static_min_frac <= 1.0:
        p.error("--static-min-frac должен быть в (0, 1]")
    return args


def main() -> int:
    setup_console()
    t0 = time.time()
    cfg = common.pipeline_config().get("prelabel", {})
    args = parse_args(cfg)
    spec = load_class_spec()

    index_path = common.data_path("frames") / "index.csv"
    if not index_path.exists():
        print(f"[x] нет {index_path}. Сначала: python training/scripts/02_extract_frames.py")
        return 2
    frames_all = load_index(index_path)
    wanted = parse_videos(args.videos)
    if wanted:
        known = {f.video_id for f in frames_all}
        missing = sorted(wanted - known, key=vid_key)
        if missing:
            print(f"[!] в index.csv нет роликов: {', '.join(missing)}")
        frames_all = [f for f in frames_all if f.video_id in wanted]
    if not frames_all:
        print("[x] нет кадров для разметки (проверьте --videos и data/frames/index.csv)")
        return 2
    selected = frames_all[:args.limit] if args.limit else frames_all

    settings = Settings(
        device=common.pick_device(args.device or cfg.get("device", "auto")),
        coco_weights=resolve_coco_weights(args.coco_model or cfg.get("coco_model", "yolo26x.pt")),
        coco_conf=float(cfg.get("coco_conf", 0.30)),
        coco_imgsz=args.coco_imgsz,
        gdino_model=args.gdino_model or cfg.get("gdino_model", "IDEA-Research/grounding-dino-base"),
        box_thr=float(cfg.get("gdino_box_threshold", 0.30)),
        text_thr=float(cfg.get("gdino_text_threshold", 0.25)),
        decoder=args.gdino_decoder,
        nms_iou=float(cfg.get("nms_iou", 0.6)),
        min_area_frac=float(cfg.get("min_box_area_frac", 0.0005)),
        static_keyframes=int(cfg.get("static_keyframes", 5)),
        static_min_frac=args.static_min_frac,
        safe_open_thr=float(args.safe_open_threshold if args.safe_open_threshold is not None
                            else cfg.get("safe_open_threshold", DEFAULT_SAFE_OPEN_THRESHOLD)),
        use_coco=not args.no_coco and bool(spec.coco),
        use_gdino=not args.no_gdino and bool(spec.gdino),
        batch=args.batch,
    )
    params = settings.fingerprint(spec)
    out_root = common.data_path("prelabels")
    by_video_all = group_by_video(frames_all)
    by_video_sel = group_by_video(selected)
    print(f"== Предварительная разметка: {len(selected)} кадров, {len(by_video_sel)} роликов, "
          f"устройство {settings.device}, параметры {params['hash']}")

    runner = Runner(settings, spec)
    n_written = n_skipped = 0
    try:
        for vid, vframes in by_video_sel.items():
            todo = [f for f in vframes if args.force or not all(p.exists() for p in frame_outputs(out_root, f))]
            n_skipped += len(vframes) - len(todo)
            if not todo:
                continue
            print(f"-- {vid}: к разметке {len(todo)} из {len(vframes)} кадров")
            n_written += process_video(vid, todo, by_video_all[vid], runner, out_root, params, args.force)
    except KeyboardInterrupt:
        print("\n[!] прервано; готовые кадры сохранены, повторный запуск продолжит с места остановки")
        return 130

    print_summary(selected, out_root, spec, params, n_written, n_skipped, runner.failed, time.time() - t0)
    return 1 if runner.failed else 0


if __name__ == "__main__":
    sys.exit(main())
