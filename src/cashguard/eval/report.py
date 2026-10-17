"""Numerical reports and explicit baseline comparison, without auto-updates."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_HIGHER_BETTER = {
    "video_recall",
    "video_precision",
    "f1",
    "hit_1s",
    "hit_2s",
    "type_accuracy",
    "jury_valid_rate",
}
_LOWER_BETTER = {"dt_median", "dt_p90", "clean_firings"}


def compare_baseline(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Report regressions without inventing S0's not-yet-approved tolerances."""
    tolerance = baseline.get("tolerance", {})
    deltas = {}
    regressions = []
    unavailable = []
    for name in sorted(_HIGHER_BETTER | _LOWER_BETTER):
        old = baseline["metrics"].get(name)
        new = current["metrics"].get(name)
        if old is None or new is None:
            unavailable.append(name)
            continue
        delta = new - old
        deltas[name] = delta
        allowance = float(tolerance.get(name, 0.0))
        if allowance < 0:
            raise ValueError("Допуск не может быть отрицательным")
        if (name in _HIGHER_BETTER and delta < -allowance) or (
            name in _LOWER_BETTER and delta > allowance
        ):
            regressions.append(name)
    new_clean = {
        video_id: sorted(set(codes) - set(baseline.get("clean_firings", {}).get(video_id, [])))
        for video_id, codes in current.get("clean_firings", {}).items()
    }
    new_clean = {video_id: codes for video_id, codes in new_clean.items() if codes}
    metrics = current["metrics"]
    incomplete = bool(metrics.get("missing_results", 0)) or (
        metrics.get("jury_checked", 0) < metrics.get("jury_total", 0)
    )
    return {
        "deltas": deltas,
        "regressions": regressions,
        "new_clean_firings": new_clean,
        "unavailable": unavailable,
        "incomplete": incomplete,
        "passed": not regressions and not new_clean and not unavailable and not incomplete,
    }


def summary_text(report: Mapping[str, Any]) -> str:
    """Print counts only: this intentionally never serializes per-video payloads."""
    metrics = report["metrics"]
    names = (
        "videos",
        "events",
        "video_recall",
        "video_precision",
        "f1",
        "dt_median",
        "dt_p90",
        "hit_1s",
        "hit_2s",
        "type_accuracy",
        "jury_valid_rate",
        "jury_checked",
        "missing_results",
    )
    lines = ["CashGuard: числовая оценка готовых предсказаний"]
    lines.extend(f"{name}: {metrics.get(name)}" for name in names)
    if "baseline_diff" in report:
        lines.append(f"baseline_passed: {report['baseline_diff']['passed']}")
    return "\n".join(lines)


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_report(directory: str | Path, report: Mapping[str, Any]) -> None:
    directory = Path(directory)
    _write_atomic(
        directory / "metrics.json",
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    _write_atomic(directory / "report.md", summary_text(report) + "\n")
