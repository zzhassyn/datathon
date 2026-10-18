"""PERSONAL_ITEMS, PDF item 5: calibrated staff surface + persistent bag + work context.

The two-second persistence filter is an engineering debounce, not a bank time limit.
Outerwear is deliberately excluded: that output class has no training examples.
No candidates does NOT establish a clean video; the Windows handler enforces this.
"""

import math

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import Candidate, EvidenceKind, EvidenceRequest, RuleContext
from cashguard.contracts.scene import StateSubject, ZoneKind
from cashguard.pipeline.rules.base import BaseRule, register_rule


def _inside(box, roi):
    return roi[0] <= box[0] < box[2] <= roi[2] and roi[1] <= box[1] < box[3] <= roi[3]


def _iou(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union else 0


def evaluate_frames(frames, calibration, *, min_duration_sec=2.0, max_gap_sec=0.75):
    """Return evidence-backed candidates from numeric observations on a confirmed surface.

    Tracks here are temporary spatial event runs, never counts of unique objects.
    """
    if not all(
        calibration.get(key) is True
        for key in (
            "confirmed",
            "cash_area_confirmed",
            "staff_only_surface",
            "staff_ownership_confirmed",
        )
    ):
        raise ValueError("Нет подтверждённой калибровки кассового стола")
    width, height = calibration["width"], calibration["height"]
    roi = calibration["desk_roi"]
    if (
        width <= 0
        or height <= 0
        or len(roi) != 4
        or not 0 <= roi[0] < roi[2] <= width
        or not 0 <= roi[1] < roi[3] <= height
    ):
        raise ValueError("Некорректная калибровка поверхности стола")
    if not math.isfinite(min_duration_sec) or min_duration_sec <= 0:
        raise ValueError("Некорректный фильтр устойчивости")
    normalized = [roi[0] / width, roi[1] / height, roi[2] / width, roi[3] / height]
    active, found = [], []

    def finish(run):
        duration = run["last"] - run["start"]
        if duration + 1e-9 < min_duration_sec:
            return
        found.append(
            Candidate(
                id=f"personal-items-{len(found) + 1}",
                code=ViolationCode.PERSONAL_ITEMS,
                t_sec=run["start"],
                start_sec=run["start"],
                end_sec=run["last"],
                confidence=min(run["confidence"], 0.8),
                facts={"t_place": run["start"], "items_ru": "сумка", "dur_sec": duration},
                evidence=[
                    EvidenceRequest(
                        kind=EvidenceKind.EVENT, t_sec=run["start"], label="Начало размещения"
                    ),
                    EvidenceRequest(
                        kind=EvidenceKind.CONTEXT, t_sec=run["last"], label="Устойчивое размещение"
                    ),
                ],
                rule_version="windows-calibrated-technical-v1",
                verdict_impact=True,
            )
        )

    previous = -1.0
    for frame in frames:
        at = frame["t_sec"]
        if not math.isfinite(at) or at <= previous:
            raise ValueError("Немонотонные PTS наблюдений")
        previous = at
        people = [
            d for d in frame["detections"] if d["class_name"] == "person" and d["conf"] >= 0.5
        ]
        work = any(
            hand["conf"] >= 0.5
            and _inside(hand["box"], normalized)
            and any(_inside(hand["box"], person["box"]) for person in people)
            for hand in frame.get("hands", [])
        )
        bags = [
            d
            for d in frame["detections"]
            if work
            and d["class_name"] == "personal_bag"
            and d["conf"] >= 0.5
            and _inside(d["box"], normalized)
        ]
        remaining = list(bags)
        next_active = []
        for run in active:
            matches = [d for d in remaining if _iou(d["box"], run["box"]) >= 0.65]
            if at - run["last"] <= max_gap_sec and matches:
                match = max(matches, key=lambda d: _iou(d["box"], run["box"]))
                remaining.remove(match)
                run["last"] = at
                run["confidence"] = min(run["confidence"], match["conf"])
                next_active.append(run)
            else:
                finish(run)
        for bag in remaining:
            next_active.append(dict(start=at, last=at, box=bag["box"], confidence=bag["conf"]))
        active = next_active
    for run in active:
        finish(run)
    return sorted(found, key=lambda event: event.t_sec)


@register_rule
class PersonalItemsRule(BaseRule):
    """Frozen scene-port adapter; opt-in only after owner-supplied scene calibration.

    Windows uses the numeric sidecar above because hand boxes are not in PerceptionTrace.
    The adapter never enables an uncalibrated rule via its default RuleConfig.
    """

    code = ViolationCode.PERSONAL_ITEMS
    version = "windows-calibrated-technical-v1"

    def evaluate(self, ctx: RuleContext) -> list[Candidate]:
        params = ctx.cfg.params
        if not all(
            params.get(key) is True
            for key in (
                "camera_confirmed",
                "cash_area_confirmed",
                "staff_only_surface",
                "staff_ownership_confirmed",
            )
        ):
            raise ValueError("Не подключена подтверждённая калибровка и принадлежность вещей")
        minimum = float(params.get("min_duration_sec", 2))
        if not math.isfinite(minimum) or minimum <= 0:
            raise ValueError("Некорректный фильтр устойчивости")
        found = []
        for state in ctx.scene.states:
            if (
                state.subject != StateSubject.PERSONAL_ITEM_ON_DESK
                or state.value != "personal_bag"
                or state.zone != ZoneKind.DESK
                or state.conf < 0.5
                or state.end_t - state.start_t < minimum
            ):
                continue
            working = any(
                s.subject == StateSubject.CASHIER_PRESENT
                and s.value == "working"
                and s.conf >= 0.5
                and s.start_t <= state.start_t
                and s.end_t >= state.end_t
                for s in ctx.scene.states
            )
            if not working:
                continue
            found.append(
                Candidate(
                    id=f"personal-items-{len(found) + 1}",
                    code=self.code,
                    t_sec=state.start_t,
                    start_sec=state.start_t,
                    end_sec=state.end_t,
                    confidence=min(state.conf, 0.8),
                    facts={
                        "t_place": state.start_t,
                        "items_ru": "сумка",
                        "dur_sec": state.end_t - state.start_t,
                    },
                    evidence=[
                        EvidenceRequest(
                            kind=EvidenceKind.EVENT, t_sec=state.start_t, label="Начало наблюдения"
                        )
                    ],
                    rule_version=self.version,
                    verdict_impact=True,
                )
            )
        if not found:
            raise ValueError(
                "Нет подтверждённого события; слабые классы не доказывают чистый результат"
            )
        return found
