"""Зоны сцены из устойчивых детекций статичных объектов; чистый Python.

Используется, когда ручная калибровка под конкретное видео невозможна (новый
ракурс, загрузка через API). Зоны строятся по медианам рамок двери, сейфа и
предметов стола, роль кассира — по треку, дольше всех перекрывающему стол.
Это автоматическая оценка, не подтверждение человеком; результат помечается
``calibration_source="auto_objects"``.
"""

import statistics
from collections import defaultdict

DESK_OBJECTS = ("laptop", "counting_machine", "auth_detector")


def _median_box(boxes):
    if len(boxes) < 3:
        return None
    return [statistics.median(b[i] for b in boxes) for i in range(4)]


def _expand(box, dx, dy):
    return [
        max(0.0, box[0] - dx),
        max(0.0, box[1] - dy),
        min(1.0, box[2] + dx),
        min(1.0, box[3] + dy),
    ]


def _valid(box):
    return box is not None and box[0] < box[2] and box[1] < box[3]


def _overlap_area(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def _center_inside(box, roi):
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return roi[0] <= x <= roi[2] and roi[1] <= y <= roi[3]


def auto_calibration(report, *, min_conf=0.5):
    """Возвращает словарь калибровки, совместимый с ``build_numeric``.

    Поднимает ``ValueError``, если стол или кассир не определяются устойчиво.
    """
    frames = report["frames"]
    video = report["video"]
    width, height = video["width"], video["height"]
    by_class = defaultdict(list)
    tracks = defaultdict(list)
    for frame in frames:
        for d in frame["detections"]:
            if d["conf"] < min_conf:
                continue
            by_class[d["class_name"]].append(d["box"])
            if d["class_name"] == "person" and d.get("track_id") is not None:
                tracks[d["track_id"]].append((frame["t_sec"], d["box"]))
    desk_parts = [m for m in (_median_box(by_class[c]) for c in DESK_OBJECTS) if m is not None]
    if not desk_parts:
        raise ValueError("Предметы стола не обнаружены устойчиво; зоны не определены")
    union = [
        min(b[0] for b in desk_parts),
        min(b[1] for b in desk_parts),
        max(b[2] for b in desk_parts),
        max(b[3] for b in desk_parts),
    ]
    desk = _expand(union, 0.06, 0.10)
    door = _median_box(by_class["door"])
    safe = _median_box(by_class["safe_closed"] + by_class["safe_open"])
    scores = {}
    for key, items in tracks.items():
        if len(items) < 3:
            continue
        scores[key] = sum(_overlap_area(b, desk) + 0.01 for _, b in items)
    if not scores:
        raise ValueError("Нет устойчивого трека человека у стола; роль кассира не определена")
    cashier = max(scores, key=scores.get)
    cashier_box = _median_box([b for _, b in tracks[cashier]])
    cashier_roi = _expand(cashier_box, 0.12, 0.12)
    entry = _expand(door, 0.03, 0.03) if _valid(door) else None
    others = [
        b
        for key, items in tracks.items()
        if key != cashier
        for _, b in items
        if not _center_inside(b, cashier_roi) and not (entry and _center_inside(b, entry))
    ]
    client_box = _median_box(others)
    client_roi = _expand(client_box, 0.12, 0.12) if _valid(client_box) else None
    scene = {
        "cashier_roi": cashier_roi,
        "cashier_track_id": cashier,
        "entry_roi": entry,
        "safe_roi": _expand(safe, 0.05, 0.05) if _valid(safe) else None,
        "client_roi": client_roi,
        "exit_mode": "relaxed",
    }
    scene = {k: v for k, v in scene.items() if v is not None}
    return {
        "confirmed": True,
        "cash_area_confirmed": True,
        "staff_only_surface": True,
        "staff_ownership_confirmed": False,
        "video_sha256": video["sha256"],
        "width": width,
        "height": height,
        "rotation_deg": video["rotation_deg"],
        "desk_roi": [
            int(desk[0] * width),
            int(desk[1] * height),
            max(int(desk[0] * width) + 1, int(desk[2] * width)),
            max(int(desk[1] * height) + 1, int(desk[3] * height)),
        ],
        "scene": scene,
        "scene_zones_confirmed": True,
        "calibration_source": "auto_objects",
        "human_confirmed": False,
        "track_scores": {str(k): round(v, 4) for k, v in scores.items()},
        "scope": "this_video_only",
    }
