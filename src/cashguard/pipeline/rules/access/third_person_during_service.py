"""THIRD_PERSON_DURING_SERVICE: два посетителя (не кассир) одновременно у рабочего места.

Числовой вариант по кадрам sidecar: люди с устойчивым track_id, не в зоне кассира и
не в зоне двери, одновременно ≥ min_duration_sec. Дубли рамок одного человека
(IoU > 0.5) считаются одним. Роли по зонам, без распознавания лиц.
"""

import math

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


def evaluate_frames(frames, scene_cfg, *, min_duration_sec=2.0, max_gap_sec=0.75):
    if not math.isfinite(min_duration_sec) or min_duration_sec <= 0:
        raise ValueError("Длительность должна быть положительной")
    cashier_roi = scene_cfg.get("cashier_roi")
    entry_roi = scene_cfg.get("entry_roi")
    cashier_id = scene_cfg.get("cashier_track_id")
    if cashier_roi is None:
        return []
    found = []
    run_start = None
    run_last = None
    run_ids = set()
    session_start = None
    session_last = None
    for frame in frames:
        t = frame["t_sec"]
        people = []
        for d in frame["detections"]:
            if d["class_name"] != "person" or d["conf"] < 0.5 or d.get("track_id") is None:
                continue
            if d["track_id"] == cashier_id or _inside(d["box"], cashier_roi):
                continue
            if entry_roi is not None and _inside(d["box"], entry_roi):
                continue
            if any(_iou(d["box"], p["box"]) > 0.5 for p in people):
                continue
            people.append(d)
        if people and (session_last is None or t - session_last > max_gap_sec):
            session_start = t
        if people:
            session_last = t
        if len(people) >= 2 and (run_last is None or t - run_last <= max_gap_sec):
            run_start = t if run_start is None else run_start
            run_last = t
            run_ids.update(p["track_id"] for p in people)
            continue
        if run_start is not None and run_last - run_start >= min_duration_sec:
            found.append(
                _candidate(len(found), run_start, run_last, sorted(run_ids), session_start)
            )
        run_start = run_last = None
        run_ids = set()
        if len(people) >= 2:
            run_start = run_last = t
            run_ids.update(p["track_id"] for p in people)
    if run_start is not None and run_last - run_start >= min_duration_sec:
        found.append(_candidate(len(found), run_start, run_last, sorted(run_ids), session_start))
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
        rule_version="numeric-auto-zones-v1",
        verdict_impact=True,
    )
