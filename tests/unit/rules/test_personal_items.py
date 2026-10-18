"""Technical rule fixtures, not an accuracy evaluation on bank videos."""

import pytest

from cashguard.pipeline.rules.discipline.personal_items import evaluate_frames

CALIBRATION = {
    "confirmed": True,
    "cash_area_confirmed": True,
    "staff_only_surface": True,
    "staff_ownership_confirmed": True,
    "width": 100,
    "height": 100,
    "desk_roi": [10, 10, 80, 60],
}


def frames(times=(0, 0.5, 1, 1.5, 2, 2.5), label="personal_bag", hand=True) -> list[dict]:
    return [
        dict(
            idx=i,
            t_sec=t,
            detections=[
                dict(class_name=label, conf=0.9, box=[0.2, 0.2, 0.4, 0.4]),
                dict(class_name="person", conf=0.9, box=[0.1, 0.05, 0.8, 0.9]),
            ],
            hands=[dict(conf=0.9, box=[0.4, 0.3, 0.5, 0.4])] if hand else [],
        )
        for i, t in enumerate(times)
    ]


def test_event_is_persistent_item_on_confirmed_staff_surface_with_work_context():
    events = evaluate_frames(frames(), CALIBRATION, min_duration_sec=2)
    assert len(events) == 1
    event = events[0]
    assert event.code == "PERSONAL_ITEMS"
    assert (event.start_sec, event.t_sec, event.end_sec) == (0, 0, 2.5)
    assert event.facts["dur_sec"] == 2.5
    assert event.rule_version and event.evidence[0].t_sec == 0


@pytest.mark.parametrize(
    "key", ["confirmed", "cash_area_confirmed", "staff_only_surface", "staff_ownership_confirmed"]
)
def test_unconfirmed_context_cannot_issue_result(key):
    with pytest.raises(ValueError, match="калибров"):
        evaluate_frames(frames(), {**CALIBRATION, key: False})


@pytest.mark.parametrize("label", ["collection_bag", "outerwear", "phone"])
def test_other_objects_and_untrained_outerwear_are_not_personal_bags(label):
    assert evaluate_frames(frames(label=label), CALIBRATION) == []


def test_single_detection_and_no_hand_context_do_not_become_events():
    assert evaluate_frames(frames(times=(0,)), CALIBRATION) == []
    assert evaluate_frames(frames(hand=False), CALIBRATION) == []


def test_item_outside_staff_surface_is_not_a_violation():
    records = frames()
    for record in records:
        record["detections"][0]["box"] = [0.81, 0.7, 0.95, 0.9]
    assert evaluate_frames(records, CALIBRATION) == []


def test_disappearance_closes_event_and_does_not_bridge_long_gap():
    records = frames(times=(0, 0.5, 1, 1.5, 2))
    records += [dict(idx=5, t_sec=2.5, detections=[], hands=[])]
    records += frames(times=(3, 3.5))
    (event,) = evaluate_frames(records, CALIBRATION)
    assert event.end_sec == 2


def test_moving_item_does_not_count_as_storage():
    records = frames()
    for index, record in enumerate(records):
        x = 0.12 + 0.09 * index
        record["detections"][0]["box"] = [x, 0.2, x + 0.12, 0.4]
    assert evaluate_frames(records, CALIBRATION) == []


def test_registered_scene_adapter_never_enables_unknown_camera_by_default():
    from cashguard.contracts.findings import RuleConfig, RuleContext
    from cashguard.pipeline.rules.registry import discover

    rule = discover().rules["PERSONAL_ITEMS"]
    ctx = RuleContext.model_construct(cfg=RuleConfig(code="PERSONAL_ITEMS"))
    with pytest.raises(ValueError, match="калибров"):
        rule.evaluate(ctx)


@pytest.mark.parametrize("name", ["pos_synthetic", "neg_no_work_synthetic"])
def test_numeric_timeline_fixtures(name):
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "fixtures/timelines"
    case = json.loads((root / f"PERSONAL_ITEMS_{name}.json").read_text(encoding="utf-8"))
    observed = frames(
        times=case["times_sec"], label=case["object_class"], hand=case["work_context"]
    )
    events = evaluate_frames(observed, CALIBRATION, min_duration_sec=case["min_duration_sec"])
    assert len(events) == case["expected_events"]
    if events:
        assert events[0].start_sec == case["expected_start_sec"]
        assert events[0].end_sec == case["expected_end_sec"]
