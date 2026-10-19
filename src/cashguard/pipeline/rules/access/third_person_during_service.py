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
from cashguard.contracts.findings import Candidate, EvidenceKind, EvidenceRequest


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


def _deduplicate_people(eligible):
    # Confidence-first ordering makes overlap suppression independent of input order.
    people = []
    ids = set()
    for detection in sorted(eligible, key=lambda d: (-d["conf"], d["track_id"], tuple(d["box"]))):
        if detection["track_id"] in ids or any(
            _iou(detection["box"], person["box"]) > 0.5 for person in people
        ):
            continue
        people.append(detection)
        ids.add(detection["track_id"])
    return people


def _adaptive_scale(observations, max_gap_sec, min_duration_sec):
    """One median per stable visitor, so a long background track gets one vote.

    Engineering starting values from the reference's relative-height approach;
    they are not thresholds validated on a held-out camera.
    """
    runs = defaultdict(list)
    for t, people in observations:
        for person in _deduplicate_people(people):
            if person["conf"] < 0.6:
                continue
            track_runs = runs[person["track_id"]]
            if not track_runs or t - track_runs[-1][-1][0] > max_gap_sec:
                track_runs.append([])
            track_runs[-1].append((t, person["box"][3] - person["box"][1]))
    stable = {}
    counts = {}
    intervals = {}
    for track_id, track_runs in runs.items():
        supported = [
            run
            for run in track_runs
            if len(run) >= 3 and run[-1][0] - run[0][0] >= min_duration_sec / 2
        ]
        if supported:
            best = max(supported, key=lambda run: (run[-1][0] - run[0][0], len(run)))
            stable[track_id] = median(height for _, height in best)
            counts[track_id] = len(best)
            intervals[track_id] = [(run[0][0], run[-1][0]) for run in supported]
    if len(stable) < 2 or sum(counts.values()) < 8:
        return None
    heights = sorted(stable.values())
    position = (len(heights) - 1) * 0.75
    low, high = math.floor(position), math.ceil(position)
    reference = heights[low] + (heights[high] - heights[low]) * (position - low)
    return max(0.05, 0.5 * reference), intervals


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
    if person_size_mode not in {"fixed", "adaptive"}:
        raise ValueError("Неизвестный режим размера посетителей")
    seat = scene_cfg.get("cashier_seat") or scene_cfg.get("cashier_roi")
    entry = scene_cfg.get("entry_roi")
    cashier_id = scene_cfg.get("cashier_track_id")
    if seat is None or cashier_id is None:
        return []
    observations = [
        (frame["t_sec"], _eligible_people(frame, seat, entry, cashier_id, desk)) for frame in frames
    ]
    scale = (
        _adaptive_scale(observations, max_gap_sec, min_duration_sec)
        if person_size_mode == "adaptive"
        else None
    )
    found = []
    run_start = None
    run_last = None
    run_session_start = None
    run_ids = set()
    session_start = None
    session_last = None
    for t, eligible in observations:
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
        if people and (session_last is None or t - session_last > max_gap_sec):
            session_start = t
        if people:
            session_last = t
        if len(people) >= 2 and (run_last is None or t - run_last <= max_gap_sec):
            if run_start is None:
                run_start = t
                run_session_start = session_start
            run_last = t
            run_ids.update(p["track_id"] for p in people)
            continue
        if run_start is not None and run_last - run_start >= min_duration_sec:
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
    if run_start is not None and run_last - run_start >= min_duration_sec:
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
        rule_version="numeric-auto-zones-v3",
        verdict_impact=True,
    )
