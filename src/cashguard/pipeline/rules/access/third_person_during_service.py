"""THIRD_PERSON_DURING_SERVICE: два посетителя (не кассир) одновременно у рабочего места.

Числовой вариант по кадрам sidecar: люди с устойчивым track_id, кроме кассира и его
места (IoU > 0.5 с медианной рамкой кассира), одновременно ≥ min_duration_sec.
Дубли ID/рамок считаются одним человеком; рамки вне стола и у края двери
исключаются. По умолчанию adaptive оценивает масштаб по устойчивым трекам посетителей;
при недостаточной опоре сохраняется порог площади fixed. Без лиц.
"""

import math
from collections import defaultdict
from statistics import median

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import Candidate, EvidenceKind, EvidenceRequest, RuleContext
from cashguard.contracts.scene import Role, ZoneKind
from cashguard.pipeline.rules.base import BaseRule, register_rule
from cashguard.pipeline.timing import at_least, at_most

_MIN_ADAPTIVE_HEIGHT = 0.05


def _iou(a, b):
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _inside(box, roi):
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return roi[0] <= x <= roi[2] and roi[1] <= y <= roi[3]


def _overlaps(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def _eligible_people(frame, seat, entry, cashier_id, desk):
    eligible = []
    for detection in frame["detections"]:
        if (
            detection["class_name"] != "person"
            or detection["conf"] < 0.5
            or detection.get("track_id") is None
        ):
            continue
        box = detection["box"]
        if detection["track_id"] == cashier_id or _iou(box, seat) > 0.5:
            continue
        if desk is not None and not _overlaps(box, desk):
            continue
        if entry is not None and (box[0] < 0.01 or box[2] > 0.99) and _inside(box, entry):
            continue
        eligible.append(detection)
    return eligible


def _deduplicate_people(eligible, *, suppress_overlaps=True):
    # Confidence-first ordering makes overlap suppression independent of input order.
    people = []
    ids = set()
    for detection in sorted(eligible, key=lambda d: (-d["conf"], d["track_id"], tuple(d["box"]))):
        if detection["track_id"] in ids or (
            suppress_overlaps
            and any(_iou(detection["box"], person["box"]) > 0.5 for person in people)
        ):
            continue
        people.append(detection)
        ids.add(detection["track_id"])
    return people


def _supported_runs(observations, max_gap_sec, min_duration_sec, *, suppress_overlaps):
    runs = defaultdict(list)
    for t, people in observations:
        for person in _deduplicate_people(people, suppress_overlaps=suppress_overlaps):
            if person["conf"] < 0.6:
                continue
            track_runs = runs[person["track_id"]]
            if not track_runs or not at_most(t - track_runs[-1][-1][0], max_gap_sec):
                track_runs.append([])
            track_runs[-1].append((t, person["box"][3] - person["box"][1]))
    return {
        track_id: supported
        for track_id, track_runs in runs.items()
        if (
            supported := [
                run
                for run in track_runs
                if len(run) >= 3 and at_least(run[-1][0] - run[0][0], min_duration_sec / 2)
            ]
        )
    }


def _adaptive_scales(observations, max_gap_sec, min_duration_sec):
    """Only co-observed stable runs vote; later visitors cannot change earlier scale.

    NMS limits votes, while raw track support survives suppression of a duplicate.
    """
    reference = _supported_runs(observations, max_gap_sec, min_duration_sec, suppress_overlaps=True)
    support = _supported_runs(observations, max_gap_sec, min_duration_sec, suppress_overlaps=False)
    intervals = {
        track_id: [(run[0][0], run[-1][0]) for run in supported]
        for track_id, supported in support.items()
    }
    votes = defaultdict(list)
    for supported in reference.values():
        for run in supported:
            height = median(height for _, height in run)
            for t, _ in run:
                votes[t].append((height, len(run)))
    scales = {}
    for t, current in votes.items():
        if len(current) < 2 or sum(count for _, count in current) < 8:
            continue
        heights = sorted(height for height, _ in current)
        position = (len(heights) - 1) * 0.75
        low, high = math.floor(position), math.ceil(position)
        height = heights[low] + (heights[high] - heights[low]) * (position - low)
        scales[t] = (max(_MIN_ADAPTIVE_HEIGHT, 0.5 * height), intervals)
    return scales


def _observed_cashier_seat(frames, cashier_id, roi):
    boxes = []
    for frame in frames:
        people = [
            d
            for d in frame["detections"]
            if d["class_name"] == "person"
            and d.get("track_id") == cashier_id
            and d["conf"] >= 0.5
            and _inside(d["box"], roi)
        ]
        people = _deduplicate_people(people)
        if people:
            boxes.append(people[0]["box"])
    if not boxes:
        raise ValueError("Кассир не наблюдается в подтверждённой зоне")
    return [median(box[i] for box in boxes) for i in range(4)]


def evaluate_frames(
    frames,
    scene_cfg,
    *,
    min_duration_sec=2.0,
    max_gap_sec=0.75,
    min_area=0.02,
    desk=None,
    person_size_mode="adaptive",
):
    if not math.isfinite(min_duration_sec) or min_duration_sec <= 0:
        raise ValueError("Длительность должна быть положительной")
    if not math.isfinite(max_gap_sec) or max_gap_sec <= 0:
        raise ValueError("Допустимый разрыв должен быть положительным")
    if person_size_mode not in {"fixed", "adaptive"}:
        raise ValueError("Неизвестный режим размера посетителей")
    seat = scene_cfg.get("cashier_seat")
    entry = scene_cfg.get("entry_roi")
    cashier_id = scene_cfg.get("cashier_track_id")
    if cashier_id is None:
        return []
    if seat is None:
        roi = scene_cfg.get("cashier_roi")
        if roi is None:
            return []
        seat = _observed_cashier_seat(frames, cashier_id, roi)
    observations = [
        (frame["t_sec"], _eligible_people(frame, seat, entry, cashier_id, desk)) for frame in frames
    ]
    scales = (
        _adaptive_scales(observations, max_gap_sec, min_duration_sec)
        if person_size_mode == "adaptive"
        else {}
    )
    found = []
    run_start = None
    run_last = None
    run_session_start = None
    run_ids = set()
    session_start = None
    session_last = None
    for t, eligible in observations:
        scale = scales.get(t)
        people = []
        for person in eligible:
            box = person["box"]
            area = (box[2] - box[0]) * (box[3] - box[1])
            supported = (
                scale is not None
                and person["conf"] >= 0.6
                and any(start <= t <= end for start, end in scale[1].get(person["track_id"], []))
            )
            if scale is not None and box[3] - box[1] < scale[0]:
                continue
            if area >= min_area or supported:
                people.append(person)
        # A rejected small duplicate must not suppress a size-qualified person.
        people = _deduplicate_people(people)
        if people and (session_last is None or not at_most(t - session_last, max_gap_sec)):
            session_start = t
        if people:
            session_last = t
        if len(people) >= 2 and (run_last is None or at_most(t - run_last, max_gap_sec)):
            if run_start is None:
                run_start = t
                run_session_start = session_start
            run_last = t
            run_ids.update(p["track_id"] for p in people)
            continue
        if run_start is not None and at_least(run_last - run_start, min_duration_sec):
            found.append(
                _candidate(len(found), run_start, run_last, sorted(run_ids), run_session_start)
            )
        run_start = run_last = None
        run_session_start = None
        run_ids = set()
        if len(people) >= 2:
            run_start = run_last = t
            run_session_start = session_start
            run_ids.update(p["track_id"] for p in people)
    if run_start is not None and at_least(run_last - run_start, min_duration_sec):
        found.append(
            _candidate(len(found), run_start, run_last, sorted(run_ids), run_session_start)
        )
    return found


def _candidate(index, start, end, ids, session_start):
    return Candidate(
        id=f"third-person-{index + 1}",
        code=ViolationCode.THIRD_PERSON_DURING_SERVICE,
        t_sec=start,
        start_sec=start,
        end_sec=end,
        confidence=0.55,
        facts={
            "t_session_start": session_start if session_start is not None else start,
            "t_third": start,
            "interaction_ru": "",
            "n_people": len(ids),
        },
        evidence=[
            EvidenceRequest(kind=EvidenceKind.EVENT, t_sec=start, label="Второй посетитель"),
            EvidenceRequest(kind=EvidenceKind.CONTEXT, t_sec=end, label="Оба у стола"),
        ],
        rule_version="numeric-auto-zones-v4",
        verdict_impact=True,
    )


def _rectangle_zone(scene, kind, *, required=True):
    zones = [zone for zone in scene.layout.zones if zone.kind == kind]
    if not zones and not required:
        return None
    if len(zones) != 1 or zones[0].conf < 0.5:
        raise ValueError(f"Требуется однозначная подтверждённая зона {kind}")
    points = zones[0].polygon
    if len(points) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for p in points for v in p):
        raise ValueError(f"Неподдерживаемая геометрия зоны {kind}")
    xs, ys = sorted({p[0] for p in points}), sorted({p[1] for p in points})
    if (
        len(xs) != 2
        or len(ys) != 2
        or set(points) != {(x, y) for x in xs for y in ys}
        or not all(
            (a[0] == b[0]) != (a[1] == b[1])
            for a, b in zip(points, points[1:] + points[:1], strict=True)
        )
    ):
        raise ValueError(f"Требуется прямоугольная зона {kind}; её границы не расширяются")
    return [xs[0], ys[0], xs[1], ys[1]]


@register_rule
class ThirdPersonDuringServiceRule(BaseRule):
    code = ViolationCode.THIRD_PERSON_DURING_SERVICE
    version = "numeric-auto-zones-v4"

    def evaluate(self, ctx: RuleContext) -> list[Candidate]:
        mode = ctx.cfg.params.get("person_size_mode", "adaptive")
        if not isinstance(mode, str):
            raise ValueError("Режим размера посетителей должен быть строкой")
        cashiers = [role for role in ctx.scene.roles if role.role == Role.CASHIER]
        if (
            len(cashiers) != 1
            or cashiers[0].conf < 0.5
            or not any(
                d.cls == "person" and d.conf >= 0.5 and d.track_id == cashiers[0].track_id
                for frame in ctx.trace.frames
                for d in frame.detections
            )
        ):
            raise ValueError("Требуется однозначный наблюдаемый кассир")
        scene_cfg = {
            "cashier_track_id": cashiers[0].track_id,
            "cashier_roi": _rectangle_zone(ctx.scene, ZoneKind.CASHIER_SIDE),
            "entry_roi": _rectangle_zone(ctx.scene, ZoneKind.DOOR, required=False),
        }
        desk = _rectangle_zone(ctx.scene, ZoneKind.DESK)
        frames = [
            dict(
                t_sec=frame.t_sec,
                detections=[
                    dict(
                        class_name=str(d.cls),
                        track_id=d.track_id,
                        conf=d.conf,
                        box=[d.box.x1, d.box.y1, d.box.x2, d.box.y2],
                    )
                    for d in frame.detections
                ],
            )
            for frame in ctx.trace.frames
        ]
        return evaluate_frames(
            frames,
            scene_cfg,
            desk=desk,
            min_duration_sec=float(ctx.cfg.params.get("min_duration_sec", 2)),
            max_gap_sec=float(ctx.cfg.params.get("max_observation_gap_sec", 0.75)),
            person_size_mode=mode,
        )
