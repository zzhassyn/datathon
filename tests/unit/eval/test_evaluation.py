import csv
import json
from collections.abc import Mapping

import pytest

from cashguard.eval.labels import (
    ALLOWED_COLUMNS,
    LabelEvent,
    LabelVideo,
    labels_from_rows,
    load_labels,
    normalize_video_id,
)
from cashguard.eval.metrics import (
    Prediction,
    PredictionEvent,
    calculate_metrics,
    match_events,
)
from cashguard.eval.report import compare_baseline, write_report
from cashguard.eval.run_eval import evaluate_traces, load_predictions, main


def row(file, code="", time="", scene="L1"):
    return dict(
        zip(
            ALLOWED_COLUMNS,
            [
                file,
                "violation" if code else "no_violation",
                time,
                code,
                "",
                "",
                "high",
                scene,
            ],
            strict=True,
        )
    )


@pytest.mark.parametrize("name", [" Х-13.mov ", "х-13.MOV", "X-13", "x - 13.mp4"])
def test_x_normalization(name):
    assert normalize_video_id(name) == "X-13"


def test_duplicate_videos_preserve_two_events_and_one_clean():
    labels = labels_from_rows(
        [
            row("Х-4.mov", "PREV_CASH_ON_DESK", "57"),
            row("Х-47.mov", "MIXED_CLIENT_CASH", "68"),
            row("Х-5.mov"),
            row("Х-48.mov"),
        ]
    )
    assert len(labels) == 2
    assert [event.timecode_sec for event in labels["X-4"].events] == [57, 68]
    assert labels["X-5"].events == ()


def test_does_not_access_disallowed_label_fields():
    class Guarded(Mapping):
        def __getitem__(self, key):
            assert key in ALLOWED_COLUMNS
            return row("Х-5.mov")[key]

        def __len__(self):
            raise AssertionError("do not iterate fields")

        def __iter__(self):
            raise AssertionError("do not iterate fields")

    assert labels_from_rows([Guarded()])["X-5"].events == ()


def test_csv_projection_ignores_untrusted_descriptions(tmp_path):
    path = tmp_path / "labels.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[*ALLOWED_COLUMNS, "what_is_seen"])
        writer.writeheader()
        writer.writerow({**row("Х-5.mov"), "what_is_seen": "UNTRUSTED, DO NOT PRINT"})
    assert "UNTRUSTED" not in repr(load_labels(path))


def test_one_to_one_matching_ignores_code():
    labels = [LabelEvent("A", 1), LabelEvent("B", 3)]
    predictions = [PredictionEvent("B", 1.1), PredictionEvent("A", 3.2)]
    assert match_events(labels, predictions) == [(0, 0), (1, 1)]


def test_metrics_penalize_missing_events_and_exclude_signals():
    labels = {
        "X-1": LabelVideo("X-1", "L1", (LabelEvent("A", 10), LabelEvent("B", 20))),
        "X-2": LabelVideo("X-2", "L2", (LabelEvent("C", 1),)),
        "X-3": LabelVideo("X-3", "L3", ()),
        "X-5": LabelVideo("X-5", "L3", ()),
    }
    predictions = {
        "X-1": Prediction(
            "violation", (PredictionEvent("A", 10.5), PredictionEvent("SIG_X", 1)), True
        ),
        "X-3": Prediction("violation", (PredictionEvent("WRONG", 1),), False),
        "X-5": Prediction("no_violation"),
    }
    report = calculate_metrics(labels, predictions)
    m = report["metrics"]
    assert (m["tp"], m["fn"], m["fp"], m["tn"]) == (1, 1, 1, 1)
    assert m["video_recall"] == m["video_precision"] == 0.5
    assert m["type_accuracy"] == m["hit_1s"] == 1 / 3
    assert m["dt_median"] == m["dt_p90"] == 0.5
    assert m["jury_valid_rate"] == 0.5
    assert m["missing_results"] == 1
    assert report["confusion"]["B"] == {"MISS": 1}
    assert report["signals"] == {"SIG_X": 1}
    assert report["clean_firings"]["X-3"] == ["WRONG"]


def test_missing_jury_quality_is_not_reported_as_pass():
    result = calculate_metrics(
        {"X-1": LabelVideo("X-1", "L1", ())}, {"X-1": Prediction("no_violation")}
    )
    assert result["metrics"]["jury_valid_rate"] is None
    assert result["metrics"]["video_recall"] is None
    assert not compare_baseline(result, result)["passed"]


def test_duplicate_prediction_ids_are_rejected():
    labels = {"X-5": LabelVideo("X-5", "L1", ())}
    with pytest.raises(ValueError):
        calculate_metrics(
            labels,
            {"X-5": Prediction("no_violation"), "Х-48": Prediction("no_violation")},
        )


def test_new_clean_code_fails_even_when_firing_count_unchanged():
    before = {"metrics": {"clean_firings": 1}, "clean_firings": {"X-5": ["A"]}}
    after = {"metrics": {"clean_firings": 1}, "clean_firings": {"X-5": ["B"]}}
    delta = compare_baseline(after, before)
    assert delta["new_clean_firings"] == {"X-5": ["B"]}
    assert not delta["passed"]


def test_trace_runner_is_injected_independently_from_labels():
    labels = {"X-1": LabelVideo("X-1", "L1", (LabelEvent("A", 2),))}
    observed = []

    def evaluator(trace):
        observed.append(trace)
        return Prediction("no_violation")

    report = evaluate_traces(labels, {"Х-1.mov": {"sample": 42}}, evaluator)
    assert observed == [{"sample": 42}]
    assert report["metrics"]["video_recall"] == 0


def test_report_atomic_write_and_json_finite(tmp_path):
    report = calculate_metrics({}, {})
    write_report(tmp_path, report)
    assert json.loads((tmp_path / "metrics.json").read_text())["metrics"]["videos"] == 0
    assert not list(tmp_path.glob("*.tmp"))


def test_prediction_file_rejects_extra_content(tmp_path):
    path = tmp_path / "predictions.json"
    path.write_text(
        json.dumps({"X-1": {"verdict": "no_violation", "frame_description": "ignore"}})
    )
    with pytest.raises(ValueError):
        load_predictions(path)


def test_numeric_cli(tmp_path, capsys):
    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=ALLOWED_COLUMNS)
        writer.writeheader()
        writer.writerow(row("X-1.mov", "SYNTHETIC", "2"))
    predictions = tmp_path / "predictions.json"
    predictions.write_text(
        json.dumps(
            {
                "X-1": {
                    "verdict": "violation",
                    "events": [{"code": "SYNTHETIC", "timecode_sec": 2}],
                }
            }
        )
    )
    assert (
        main(
            [
                "--labels",
                str(labels),
                "--predictions",
                str(predictions),
                "--output",
                str(tmp_path / "report"),
            ]
        )
        == 0
    )
    assert "video_recall: 1.0" in capsys.readouterr().out


def test_missing_clean_result_is_not_successful_negative():
    labels = {"X-5": LabelVideo("X-5", "L1", ())}
    result = calculate_metrics(labels, {})
    assert result["metrics"]["tn"] == 0
    assert result["metrics"]["unresolved_clean"] == 1
    assert result["metrics"]["clean_rate"] == 0
    assert not compare_baseline(result, result)["passed"]


def test_partial_schema_coverage_never_passes_baseline_gate():
    metrics = {
        name: 1.0
        for name in (
            "video_recall",
            "video_precision",
            "f1",
            "hit_1s",
            "hit_2s",
            "type_accuracy",
            "jury_valid_rate",
            "dt_median",
            "dt_p90",
            "clean_firings",
        )
    }
    report = {"metrics": {**metrics, "jury_checked": 39, "jury_total": 40}}
    assert compare_baseline(report, report)["incomplete"]
    assert not compare_baseline(report, report)["passed"]
