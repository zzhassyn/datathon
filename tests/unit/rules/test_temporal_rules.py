import pytest
from tests.unit.scene.test_temporal import CALIBRATION, PARAMS, VIDEO, build_numeric, records

from cashguard.contracts.scene import SceneEvent, SceneModel
from cashguard.pipeline.local_config import AnalysisBlocked
from cashguard.pipeline.rules.cashops.prev_cash_on_desk import evaluate_scene
from cashguard.pipeline.windows_analysis import temporal_candidates


def turnover_scene():
    scene = build_numeric(records(), VIDEO, CALIBRATION, PARAMS).model_dump(mode="json")
    scene["roles"] += [
        dict(
            track_id=k,
            role="client",
            conf=0.9,
            first_t=0,
            last_t=6,
            entered_via_door=True,
            exited_via_door=True,
        )
        for k in (10, 11)
    ]
    scene["events"] = [
        dict(kind="exit", t=2, track_ids=[10], conf=0.9, data={"confirmed_at": 3}),
        dict(kind="enter", t=4, track_ids=[11], conf=0.9, data={}),
        dict(
            kind="handoff_cash",
            t=1,
            track_ids=[10, 1, 3],
            conf=0.9,
            data={"operation_confirmed": True, "direction": "to_cashier"},
        ),
    ]
    return SceneModel.model_validate(scene)


def test_turnover_requires_positive_cash_origin_not_just_two_people():
    scene = turnover_scene()
    found = evaluate_scene(scene)
    assert len(found) == 1 and found[0].t_sec == 4
    assert found[0].evidence[1].kind == "event"
    scene = scene.model_copy(
        update={"events": [e for e in scene.events if e.kind != "handoff_cash"]}
    )
    assert evaluate_scene(scene) == []


def test_new_cash_identity_or_same_client_does_not_prove_previous_cash():
    scene = turnover_scene()
    states = [
        s.model_copy(update={"track_id": 20}) if s.subject == "cash_on_desk" else s
        for s in scene.states
    ]
    assert evaluate_scene(scene.model_copy(update={"states": states})) == []
    events = [
        e.model_copy(update={"track_ids": [10]}) if e.kind == "enter" else e for e in scene.events
    ]
    assert evaluate_scene(scene.model_copy(update={"events": events})) == []


def test_unconfirmed_exit_or_operation_cannot_trigger_previous_cash():
    scene = turnover_scene()
    for kind, data in (("exit", {}), ("handoff_cash", {"direction": "to_cashier"})):
        events = [
            SceneEvent(**{**e.model_dump(), "data": data}) if e.kind == kind else e
            for e in scene.events
        ]
        assert evaluate_scene(scene.model_copy(update={"events": events})) == []


def test_scene_rules_need_human_zone_confirmation():
    report = dict(frames=records(), video=VIDEO)
    with pytest.raises(AnalysisBlocked, match="подтверждение зон"):
        temporal_candidates(report, CALIBRATION, {})
    calibration = {**CALIBRATION, "scene_zones_confirmed": True, "scene": PARAMS}
    scene, found, codes = temporal_candidates(report, calibration, {"codes": ["VALUABLES_LEFT"]})
    assert len(found) == 1 and codes == ["VALUABLES_LEFT"] and scene.events
    for codes in (["TYPO"], [], ["SAFE_OPEN", "SAFE_OPEN"]):
        with pytest.raises(AnalysisBlocked, match="список правил"):
            temporal_candidates(report, calibration, {"codes": codes})


@pytest.mark.parametrize("code", ["VALUABLES_LEFT", "SAFE_OPEN", "PREV_CASH_ON_DESK"])
def test_published_numeric_timeline_fixtures(code):
    import json
    from pathlib import Path

    from cashguard.pipeline.rules.leave.safe_open import evaluate_scene as safe
    from cashguard.pipeline.rules.leave.valuables_left import evaluate_scene as valuables

    evaluators = {
        "VALUABLES_LEFT": valuables,
        "SAFE_OPEN": safe,
        "PREV_CASH_ON_DESK": evaluate_scene,
    }
    root = Path(__file__).resolve().parents[2] / "fixtures/timelines"
    paths = list(root.glob(f"{code}_*.json"))
    assert len(paths) >= 2
    for path in paths:
        fixture = json.loads(path.read_text())
        assert (
            len(evaluators[code](SceneModel.model_validate(fixture["scene"])))
            == fixture["expected_candidates"]
        )
