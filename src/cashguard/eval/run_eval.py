"""Numeric evaluation seam; scene/rules replay waits for owner implementations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from cashguard.eval.labels import LabelVideo, canonical_video_id, load_labels
from cashguard.eval.metrics import Prediction, PredictionEvent, calculate_metrics
from cashguard.eval.report import compare_baseline, summary_text, write_report


def evaluate_traces(
    labels: Mapping[str, LabelVideo],
    traces: Mapping[str, Mapping[str, Any]],
    evaluator: Callable[[Mapping[str, Any]], Prediction],
) -> dict[str, Any]:
    """Inject S0's scene/rules runner; never infer predictions from gold labels."""
    predictions = {}
    for video_id, trace in traces.items():
        video_id = canonical_video_id(video_id)
        if video_id in predictions:
            raise ValueError("Повторная трасса канонического видео")
        if video_id not in labels:
            raise ValueError("Трасса не имеет эталонной метки")
        predictions[video_id] = evaluator(trace)
    return calculate_metrics(labels, predictions)


def load_predictions(path: str | Path) -> dict[str, Prediction]:
    """Load a numeric-only intermediate file; reject unexpected content."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Предсказания должны быть объектом")
    output = {}
    for video_id, row in payload.items():
        if not isinstance(row, dict) or set(row) - {"verdict", "events", "jury_valid"}:
            raise ValueError("Предсказания содержат неизвестные поля")
        events = []
        for event in row.get("events", []):
            if not isinstance(event, dict) or set(event) != {"code", "timecode_sec"}:
                raise ValueError("Предсказание события содержит неизвестные поля")
            events.append(PredictionEvent(event["code"], event["timecode_sec"]))
        output[video_id] = Prediction(
            row["verdict"], tuple(events), row.get("jury_valid")
        )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Числовая оценка готовых предсказаний; не AI inference"
    )
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args(argv)
    report = calculate_metrics(
        load_labels(args.labels), load_predictions(args.predictions)
    )
    report["source"] = "numeric_predictions"
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        report["baseline_diff"] = compare_baseline(report, baseline)
    if args.output:
        write_report(args.output, report)
    print(summary_text(report))
    return 1 if report["metrics"]["missing_results"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
