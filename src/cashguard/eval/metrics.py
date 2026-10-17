"""Deterministic numerical metrics from docs/eval.md, without video access."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from cashguard.eval.labels import LabelVideo, canonical_video_id


@dataclass(frozen=True)
class PredictionEvent:
    code: str
    timecode_sec: float

    def __post_init__(self) -> None:
        if (
            not self.code
            or isinstance(self.timecode_sec, bool)
            or not math.isfinite(self.timecode_sec)
            or self.timecode_sec < 0
        ):
            raise ValueError("Некорректное предсказанное событие")


@dataclass(frozen=True)
class Prediction:
    verdict: str
    events: tuple[PredictionEvent, ...] = ()
    jury_valid: bool | None = None

    def __post_init__(self) -> None:
        verdict_events = [
            event for event in self.events if not event.code.startswith("SIG_")
        ]
        if self.verdict not in {"violation", "no_violation"}:
            raise ValueError("Оценка требует конечного вердикта")
        if (self.verdict == "violation") != bool(verdict_events):
            raise ValueError("Вердикт не согласован с событиями")
        if len(verdict_events) > 5:
            raise ValueError("Не больше пяти нарушений в ролике")
        if self.jury_valid is not None and not isinstance(self.jury_valid, bool):
            raise ValueError("jury_valid должен быть bool или None")


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def percentile(values: Sequence[float], percent: float) -> float | None:
    """Linear interpolation (type 7), explicit and stable for small samples."""
    if not 0 <= percent <= 100:
        raise ValueError("Процентиль вне [0, 100]")
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def match_events(
    labels: Sequence[Any], predictions: Sequence[PredictionEvent]
) -> list[tuple[int, int]]:
    """Greedy one-to-one matching by ascending |dt|; code is not considered."""
    edges = sorted(
        (abs(pred.timecode_sec - label.timecode_sec), left, right)
        for left, label in enumerate(labels)
        for right, pred in enumerate(predictions)
    )
    used_labels: set[int] = set()
    used_predictions: set[int] = set()
    pairs = []
    for _, left, right in edges:
        if left not in used_labels and right not in used_predictions:
            used_labels.add(left)
            used_predictions.add(right)
            pairs.append((left, right))
    return sorted(pairs)


def calculate_metrics(
    labels: Mapping[str, LabelVideo], predictions: Mapping[str, Prediction]
) -> dict[str, Any]:
    """Missing results count as misses; unknown/duplicate video IDs are errors.

    Unmeasured jury validity remains null. No schema/evidence quality metric is
    inferred from numerical predictions, and no rule/model accuracy is claimed.
    """
    canonical: dict[str, Prediction] = {}
    for video_id, prediction in predictions.items():
        video_id = canonical_video_id(video_id)
        if video_id in canonical:
            raise ValueError("Два предсказания одного канонического видео")
        if video_id not in labels:
            raise ValueError("Предсказание не имеет эталонной метки")
        canonical[video_id] = prediction
    counts: Counter[str] = Counter()
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    signed_by_code: dict[str, list[float]] = defaultdict(list)
    absolute: list[float] = []
    per_event = []
    per_video = {}
    clean_firings = {}
    signal_counts: Counter[str] = Counter()
    total_events = sum(len(video.events) for video in labels.values())
    type_correct = type_found = hit_1s = hit_2s = 0
    schema_checked = schema_valid = 0
    for video_id in sorted(labels):
        label = labels[video_id]
        prediction = canonical.get(video_id)
        if prediction is None:
            counts["missing_results"] += 1
            predicted_events = []
            predicted_violation = False
        else:
            predicted_events = [
                event
                for event in prediction.events
                if not event.code.startswith("SIG_")
            ]
            predicted_violation = prediction.verdict == "violation"
            signal_counts.update(
                event.code
                for event in prediction.events
                if event.code.startswith("SIG_")
            )
            if prediction.jury_valid is not None:
                schema_checked += 1
                schema_valid += prediction.jury_valid
        actual_violation = bool(label.events)
        if prediction is None and not actual_violation:
            # A missing clean-video result is not a successful clean verdict.
            counts["unresolved_clean"] += 1
        else:
            counts[
                "tp"
                if actual_violation and predicted_violation
                else "fn"
                if actual_violation
                else "fp"
                if predicted_violation
                else "tn"
            ] += 1
        matches = dict(match_events(label.events, predicted_events))
        counts["unmatched_predictions"] += len(predicted_events) - len(matches)
        type_found += sum(
            event.code in {pred.code for pred in predicted_events}
            for event in label.events
        )
        for index, event in enumerate(label.events):
            predicted = predicted_events[matches[index]] if index in matches else None
            pred_code = predicted.code if predicted else "MISS"
            confusion[event.code][pred_code] += 1
            delta = (
                None
                if predicted is None
                else predicted.timecode_sec - event.timecode_sec
            )
            if delta is not None:
                absolute.append(abs(delta))
                signed_by_code[event.code].append(delta)
                hit_1s += abs(delta) <= 1
                hit_2s += abs(delta) <= 2
                type_correct += predicted.code == event.code
            else:
                counts["missed_events"] += 1
            per_event.append(
                {
                    "video_id": video_id,
                    "label_code": event.code,
                    "label_time": event.timecode_sec,
                    "prediction_code": pred_code,
                    "prediction_time": None
                    if predicted is None
                    else predicted.timecode_sec,
                    "delta_sec": delta,
                }
            )
        if not actual_violation:
            clean_firings[video_id] = sorted(event.code for event in predicted_events)
        per_video[video_id] = {
            "scene": label.scene,
            "expected_verdict": "violation" if actual_violation else "no_violation",
            "verdict": None if prediction is None else prediction.verdict,
            "codes": [[event.code, event.timecode_sec] for event in predicted_events],
        }
    tp, fn, fp, tn = (counts[name] for name in ("tp", "fn", "fp", "tn"))
    metrics = {
        "videos": len(labels),
        "events": total_events,
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "video_recall": _ratio(tp, tp + fn),
        "video_precision": _ratio(tp, tp + fp),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "clean_rate": _ratio(tn, tn + fp + counts["unresolved_clean"]),
        "dt_median": median(absolute) if absolute else None,
        "dt_p90": percentile(absolute, 90),
        "hit_1s": _ratio(hit_1s, total_events),
        "hit_2s": _ratio(hit_2s, total_events),
        "type_accuracy": _ratio(type_correct, total_events),
        "type_found": _ratio(type_found, total_events),
        "jury_valid_rate": _ratio(schema_valid, schema_checked),
        "jury_checked": schema_checked,
        "jury_total": len(labels),
        "missing_results": counts["missing_results"],
        "unresolved_clean": counts["unresolved_clean"],
        "missed_events": counts["missed_events"],
        "unmatched_predictions": counts["unmatched_predictions"],
        "clean_firings": sum(bool(codes) for codes in clean_firings.values()),
    }
    return {
        "metrics": metrics,
        "per_video": per_video,
        "per_event": per_event,
        "clean_firings": clean_firings,
        "confusion": {code: dict(counts) for code, counts in sorted(confusion.items())},
        "signed_dt_median": {
            code: median(values) for code, values in sorted(signed_by_code.items())
        },
        "signals": dict(sorted(signal_counts.items())),
    }
