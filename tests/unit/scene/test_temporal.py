"""Синтетические числовые наблюдения проверяют логику, не точность детекторов."""

import pytest

from cashguard.pipeline.rules.leave.safe_open import evaluate_scene as safe_open
from cashguard.pipeline.rules.leave.valuables_left import evaluate_scene as valuables_left
from cashguard.pipeline.scene.temporal import build_numeric

VIDEO = dict(sha256="a" * 64, width=100, height=100, rotation_deg=0, duration_sec=7)
CALIBRATION = dict(
    confirmed=True,
    cash_area_confirmed=True,
    staff_only_surface=True,
    video_sha256="a" * 64,
    width=100,
    height=100,
    rotation_deg=0,
    desk_roi=[0, 0, 70, 80],
)
PARAMS = dict(cashier_roi=[0, 0, 0.4, 1], entry_roi=[0.75, 0, 1, 1], safe_roi=[0.75, 0.2, 1, 0.8])


def detection(label, track, box):
    return dict(class_name=label, track_id=track, box=box, conf=0.9)


def records(*, exit_gate=True, returns=False, other_person=False):
    output = []
    for i in range(14):
        people = []
        if i <= 4:
            box = [0.1, 0.1, 0.3, 0.7]
            if i == 3 and exit_gate:
                box = [0.6, 0.1, 0.8, 0.7]
            if i == 4 and exit_gate:
                box = [0.8, 0.1, 0.96, 0.7]
            people.append(detection("person", 1, box))
        if returns and i == 10:
            people.append(detection("person", 1, [0.1, 0.1, 0.3, 0.7]))
        if other_person:
            people.append(detection("person", 2, [0.4, 0.1, 0.6, 0.8]))
        output.append(
            dict(
                t_sec=i / 2,
                detections=people + [detection("cash", 3, [0.4, 0.4, 0.5, 0.5])],
                hands=[],
            )
        )
    return output


def test_confirmed_gate_exit_and_continuous_cash_create_candidate():
    scene = build_numeric(records(), VIDEO, CALIBRATION, PARAMS)
    candidates = valuables_left(scene)
    assert len(candidates) == 1
    assert candidates[0].t_sec == 2.5
    assert candidates[0].end_sec == 4.5
    assert scene.events[0].data["confirmed_at"] == 4


def test_occlusion_away_from_gate_and_clip_end_are_not_exits():
    for frames in (records(exit_gate=False), records()[:5]):
        scene = build_numeric(frames, VIDEO, CALIBRATION, PARAMS)
        assert not scene.events
        assert valuables_left(scene) == []


def test_another_person_prevents_unattended_valuables_claim():
    scene = build_numeric(
        records(other_person=True), VIDEO, CALIBRATION, {**PARAMS, "cashier_track_id": 1}
    )
    assert scene.events
    assert valuables_left(scene) == []


def test_return_then_occlusion_does_not_reuse_old_exit():
    scene = build_numeric(records(returns=True), VIDEO, CALIBRATION, PARAMS)
    last = [s for s in scene.states if s.subject == "cashier_present" and s.zone is None][-1]
    assert last.start_t == 5.5 and last.value == "unknown"


def test_cash_identity_change_and_gap_break_continuity():
    frames = records()
    for frame in frames[7:]:
        frame["detections"][-1]["track_id"] = 9
    assert valuables_left(build_numeric(frames, VIDEO, CALIBRATION, PARAMS)) == []
    frames = records()
    frames[7]["detections"] = []
    assert valuables_left(build_numeric(frames, VIDEO, CALIBRATION, PARAMS)) == []


def test_unknown_roles_and_unobserved_intervals_are_not_filled():
    assert all(r.role == "unknown" for r in build_numeric(records(), VIDEO, CALIBRATION).roles)
    frames = [f for f in records() if f["t_sec"] not in (3, 3.5, 4)]
    assert not build_numeric(frames, VIDEO, CALIBRATION, PARAMS).events


def test_safe_open_while_cashier_works_near_safe_is_not_candidate():
    frames = records(exit_gate=False)[:5]
    for frame in frames:
        frame["detections"].append(detection("safe_open", 8, [0.8, 0.2, 0.95, 0.7]))
    cfg = {**PARAMS, "cashier_track_id": 1}
    assert len(safe_open(build_numeric(frames, VIDEO, CALIBRATION, cfg))) == 1
    for frame in frames:
        frame["detections"][0]["box"] = [0.7, 0.1, 0.99, 0.9]
    assert safe_open(build_numeric(frames, VIDEO, CALIBRATION, cfg)) == []
    for frame in frames:
        frame["detections"] = frame["detections"][1:]
    assert safe_open(build_numeric(frames, VIDEO, CALIBRATION, PARAMS)) == []


@pytest.mark.parametrize(
    "field,value", [("video_sha256", "b" * 64), ("rotation_deg", 90), ("width", 200)]
)
def test_wrong_camera_is_rejected(field, value):
    with pytest.raises(ValueError):
        build_numeric(records(), VIDEO, {**CALIBRATION, field: value}, PARAMS)


def test_pts_order_is_mandatory():
    frames = records()
    frames[2]["t_sec"] = frames[1]["t_sec"]
    with pytest.raises(ValueError, match="хронология"):
        build_numeric(frames, VIDEO, CALIBRATION, PARAMS)


def test_bag_without_cash_does_not_invent_internal_bank_policy():
    frames = records()
    for frame in frames:
        frame["detections"][-1]["class_name"] = "collection_bag"
    assert valuables_left(build_numeric(frames, VIDEO, CALIBRATION, PARAMS)) == []


def test_safe_zone_and_no_actor_at_safe_are_required():
    frames = records(exit_gate=False)[:5]
    for frame in frames:
        frame["detections"].append(detection("safe_open", 8, [0.8, 0.2, 0.95, 0.7]))
    cfg = {**PARAMS, "cashier_track_id": 1}
    no_safe = {k: v for k, v in cfg.items() if k != "safe_roi"}
    assert safe_open(build_numeric(frames, VIDEO, CALIBRATION, no_safe)) == []
    for frame in frames:
        frame["detections"].append(detection("person", 9, [0.7, 0.1, 0.99, 0.9]))
    assert safe_open(build_numeric(frames, VIDEO, CALIBRATION, cfg)) == []
