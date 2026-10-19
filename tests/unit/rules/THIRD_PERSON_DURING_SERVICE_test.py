from copy import deepcopy

import pytest

from cashguard.pipeline.rules.access.third_person_during_service import evaluate_frames

SCENE = {"cashier_track_id": 1, "cashier_seat": [0, 0, 0.2, 1]}


def frame(t, people=2):
    return {
        "t_sec": t,
        "detections": [
            {
                "class_name": "person",
                "conf": 0.9,
                "track_id": index + 2,
                "box": [0.3 + index * 0.3, 0.1, 0.5 + index * 0.3, 0.9],
            }
            for index in range(people)
        ],
    }


@pytest.mark.parametrize("people_after_gap", [1, 2])
def test_later_session_does_not_change_completed_event(people_after_gap):
    frames = [frame(0, 1), *[frame(t) for t in (0.5, 1, 1.5, 2, 2.5)]]
    frames.append(frame(10, people_after_gap))
    candidates = evaluate_frames(frames, SCENE)
    assert len(candidates) == 1
    event = candidates[0]
    assert (event.start_sec, event.end_sec) == (0.5, 2.5)
    assert event.facts["t_session_start"] == 0
    assert event.facts["t_third"] == 0.5
    assert [request.t_sec for request in event.evidence] == [0.5, 2.5]


def test_separated_sessions_keep_their_own_start():
    frames = [frame(t) for t in (0, 0.5, 1, 1.5, 2, 10, 10.5, 11, 11.5, 12)]
    candidates = evaluate_frames(frames, SCENE)
    assert [(event.start_sec, event.end_sec) for event in candidates] == [(0, 2), (10, 12)]
    assert [event.facts["t_session_start"] for event in candidates] == [0, 10]


def test_empty_frame_finishes_event_before_next_session():
    frames = [frame(t) for t in (0, 0.5, 1, 1.5, 2)]
    frames.extend([frame(2.5, 0), frame(10, 1)])
    candidates = evaluate_frames(frames, SCENE)
    assert len(candidates) == 1
    assert candidates[0].facts["t_session_start"] == 0


def test_short_runs_separated_by_gap_do_not_form_a_violation():
    candidates = evaluate_frames([frame(t) for t in (0, 0.5, 1, 10, 10.5, 11)], SCENE)
    assert candidates == []


def sized_frames(boxes, *, times=(0, 0.5, 1, 1.5, 2), confidence=0.9):
    return [
        {
            "t_sec": t,
            "detections": [
                {
                    "class_name": "person",
                    "track_id": index + 2,
                    "conf": confidence,
                    "box": list(box),
                }
                for index, box in enumerate(boxes)
            ],
        }
        for t in times
    ]


@pytest.mark.parametrize("options", [{}, {"person_size_mode": "adaptive"}])
def test_adaptive_size_recovers_two_stable_narrow_visitors(options):
    frames = sized_frames(([0.3, 0.1, 0.36, 0.4], [0.6, 0.1, 0.66, 0.4]))
    assert evaluate_frames(frames, SCENE, person_size_mode="fixed") == []
    (event,) = evaluate_frames(frames, SCENE, **options)
    assert (event.t_sec, event.end_sec) == (0, 2)


@pytest.mark.parametrize("options", [{}, {"person_size_mode": "adaptive"}])
def test_adaptive_size_rejects_short_wide_background_person(options):
    frames = sized_frames(([0.3, 0.1, 0.5, 0.7], [0.6, 0.1, 0.8, 0.22]))
    assert len(evaluate_frames(frames, SCENE, person_size_mode="fixed")) == 1
    assert evaluate_frames(frames, SCENE, **options) == []


@pytest.mark.parametrize("scale", [1, 0.5])
def test_adaptive_detection_is_consistent_under_scene_scaling(scale):
    scene = {**SCENE, "cashier_seat": [value * scale for value in SCENE["cashier_seat"]]}
    frames = sized_frames(([0.3, 0.1, 0.5, 0.7], [0.6, 0.1, 0.8, 0.7]))
    for observed in frames:
        for person in observed["detections"]:
            person["box"] = [value * scale for value in person["box"]]
    (event,) = evaluate_frames(
        frames, scene, desk=[0.25 * scale, 0, scale, scale], person_size_mode="adaptive"
    )
    assert (event.t_sec, event.end_sec) == (0, 2)


@pytest.mark.parametrize("confidence", [0.9, 0.55])
def test_adaptive_falls_back_to_area_without_enough_confident_observations(confidence):
    frames = sized_frames(
        ([0.3, 0.1, 0.36, 0.4], [0.6, 0.1, 0.66, 0.4]),
        confidence=confidence,
    )
    if confidence == 0.9:
        # Only one stable reference track; the second switches identity every frame.
        for index, observed in enumerate(frames):
            observed["detections"][1]["track_id"] = index + 10
    assert evaluate_frames(frames, SCENE, person_size_mode="adaptive") == []


@pytest.mark.parametrize("options", [{}, {"person_size_mode": "adaptive"}])
def test_adaptive_fallback_keeps_existing_large_person_detections(options):
    frames = sized_frames(([0.3, 0.1, 0.5, 0.7], [0.6, 0.1, 0.8, 0.7]), confidence=0.55)
    assert len(evaluate_frames(frames, SCENE, **options)) == 1


def test_single_height_outlier_does_not_hide_narrow_visitors():
    frames = sized_frames(([0.3, 0.1, 0.36, 0.4], [0.6, 0.1, 0.66, 0.4]))
    frames[2]["detections"][0]["box"][3] = 0.99
    assert len(evaluate_frames(frames, SCENE, person_size_mode="adaptive")) == 1


def test_repeated_background_track_has_only_one_scale_vote():
    frames = sized_frames(([0.3, 0.1, 0.5, 0.22],), times=tuple(index / 2 for index in range(100)))
    for observed in frames[-5:]:
        observed["detections"].append(
            {"class_name": "person", "track_id": 3, "conf": 0.9, "box": [0.6, 0.1, 0.8, 0.7]}
        )
    assert evaluate_frames(frames, SCENE, person_size_mode="adaptive") == []


@pytest.mark.parametrize("mode", ["fixed", "adaptive"])
def test_duplicate_track_id_does_not_count_as_two_people(mode):
    frames = sized_frames(([0.3, 0.1, 0.5, 0.7], [0.6, 0.1, 0.8, 0.7]))
    for observed in frames:
        observed["detections"][1]["track_id"] = 2
    assert evaluate_frames(frames, SCENE, person_size_mode=mode) == []


@pytest.mark.parametrize("mirrored", [False, True])
def test_door_edge_filter_is_symmetric(mirrored):
    frames = sized_frames(([0.3, 0.1, 0.5, 0.7], [0, 0.2, 0.2, 0.8]))
    scene = {"cashier_track_id": 1, "cashier_seat": [0.8, 0, 1, 1], "entry_roi": [0, 0, 0.25, 1]}
    if mirrored:

        def mirror(box):
            return [1 - box[2], box[1], 1 - box[0], box[3]]

        scene = {
            **scene,
            "cashier_seat": mirror(scene["cashier_seat"]),
            "entry_roi": mirror(scene["entry_roi"]),
        }
        for observed in frames:
            for person in observed["detections"]:
                person["box"] = mirror(person["box"])
    assert evaluate_frames(frames, scene) == []


@pytest.mark.parametrize("reverse", [False, True])
def test_overlap_dedup_uses_confidence_before_input_order(reverse):
    frames = sized_frames(([0.3, 0.1, 0.5, 0.7], [0.4, 0.1, 0.6, 0.7]))
    # A lower-confidence box bridges two real people. Greedy input-order NMS
    # used to discard both real boxes if the bridge happened to come first.
    for observed in frames:
        observed["detections"].insert(
            0,
            {
                "class_name": "person",
                "track_id": 9,
                "conf": 0.6,
                "box": [0.35, 0.1, 0.55, 0.7],
            },
        )
        if reverse:
            observed["detections"].reverse()
    assert len(evaluate_frames(frames, SCENE)) == 1


def test_adaptive_filter_does_not_mutate_numeric_observations():
    frames = sized_frames(([0.3, 0.1, 0.5, 0.7], [0.6, 0.1, 0.8, 0.7]))
    original = deepcopy(frames)
    evaluate_frames(frames, SCENE, person_size_mode="adaptive")
    assert frames == original


def test_later_stable_track_does_not_validate_earlier_uncertain_segment():
    boxes = ([0.3, 0.1, 0.36, 0.4], [0.6, 0.1, 0.66, 0.4])
    frames = sized_frames(boxes, confidence=0.55)
    frames.extend(sized_frames(boxes, times=(10, 10.5, 11, 11.5, 12)))
    events = evaluate_frames(frames, SCENE, person_size_mode="adaptive")
    assert [(event.start_sec, event.end_sec) for event in events] == [(10, 12)]


def test_short_calibration_uses_fixed_area_fallback():
    frames = sized_frames(([0.3, 0.1, 0.36, 0.4], [0.6, 0.1, 0.66, 0.4]), times=(0, 0.1, 0.2))
    assert evaluate_frames(frames, SCENE, min_duration_sec=0.2, person_size_mode="adaptive") == []


def test_adaptive_height_floor_rejects_tiny_wide_detections():
    frames = sized_frames(([0.25, 0.1, 0.55, 0.14], [0.6, 0.1, 0.9, 0.14]))
    assert evaluate_frames(frames, SCENE, person_size_mode="adaptive") == []


def test_undersized_duplicate_cannot_suppress_a_valid_fixed_size_person():
    frames = sized_frames(([0.3, 0.1, 0.4, 0.31], [0.6, 0.1, 0.8, 0.7]))
    for observed in frames:
        observed["detections"][0]["conf"] = 0.8
        observed["detections"].append(
            {
                "class_name": "person",
                "track_id": 4,
                "conf": 0.9,
                "box": [0.3, 0.1, 0.4, 0.29],
            }
        )
    assert len(evaluate_frames(frames, SCENE, person_size_mode="fixed")) == 1


@pytest.mark.parametrize("background_height", [None, 0.04, 0.08, 0.12])
@pytest.mark.parametrize("reuse_ids", [False, True])
@pytest.mark.parametrize("with_empty_frames", [False, True])
def test_independent_later_visitors_do_not_change_earlier_scale(
    with_empty_frames, background_height, reuse_ids
):
    frames = sized_frames(([0.3, 0.1, 0.36, 0.3], [0.6, 0.1, 0.66, 0.3]))
    if with_empty_frames:
        frames.extend(dict(t_sec=i / 2, detections=[]) for i in range(5, 20))
    later = sized_frames(
        ([0.3, 0.05, 0.5, 0.95], [0.6, 0.05, 0.8, 0.95]), times=(10, 10.5, 11, 11.5, 12)
    )
    for observed in later:
        for person in observed["detections"]:
            if not reuse_ids:
                person["track_id"] += 10
    frames.extend(later)
    if background_height is not None:
        for observed in frames:
            observed["detections"].append(
                dict(
                    class_name="person",
                    track_id=99,
                    conf=0.9,
                    box=[0.85, 0.1, 0.95, 0.1 + background_height],
                )
            )
    assert [(e.start_sec, e.end_sec) for e in evaluate_frames(frames, SCENE)] == [(0, 2), (10, 12)]


@pytest.mark.parametrize("reverse", [False, True])
def test_rejected_adaptive_duplicate_does_not_remove_valid_track_support(reverse):
    frames = sized_frames(([0.3, 0.1, 0.36, 0.4], [0.6, 0.1, 0.8, 0.7]), confidence=0.8)
    for frame in frames:
        frame["detections"].append(
            dict(class_name="person", track_id=4, conf=0.9, box=[0.3, 0.1, 0.36, 0.3])
        )
        if reverse:
            frame["detections"].reverse()
    assert len(evaluate_frames(frames, SCENE)) == 1


@pytest.mark.parametrize("offset", [0, 0.3, 0.6])
@pytest.mark.parametrize("step", [0.5, 0.75])
def test_duration_and_gap_boundaries_are_invariant_to_pts_origin(offset, step):
    frames = [frame(offset + i * step) for i in range(5)]
    (event,) = evaluate_frames(frames, SCENE)
    assert event.start_sec == offset
    assert event.end_sec == offset + 4 * step


def test_numerical_tolerance_does_not_join_real_gaps_or_short_events():
    assert evaluate_frames([frame(t) for t in (0, 0.5, 1, 1.5, 1.999)], SCENE) == []
    assert evaluate_frames([frame(t) for t in (0, 0.5, 1, 1.751, 2.251)], SCENE) == []


def test_many_overlapping_background_ids_cannot_change_scale_or_count():
    frames = sized_frames(([0.3, 0.1, 0.5, 0.7], [0.6, 0.1, 0.8, 0.22]))
    for observed in frames:
        background = observed["detections"][1]
        observed["detections"].extend({**background, "track_id": track} for track in range(10, 30))
    assert evaluate_frames(frames, SCENE) == []


def test_floor_only_episode_does_not_bypass_adaptive_height_rejection():
    frames = sized_frames(([0.05, 0.1, 0.49, 0.149], [0.51, 0.1, 0.95, 0.149]))
    assert len(evaluate_frames(frames, SCENE, person_size_mode="fixed")) == 1
    assert evaluate_frames(frames, SCENE) == []


def registry_context():
    from tests.contract.test_registry import _context

    from cashguard.contracts.findings import RuleConfig
    from cashguard.contracts.perception import FrameObservation
    from cashguard.contracts.scene import Role, SceneLayout, TrackRole, Zone, ZoneKind

    ctx = _context()
    frames = []
    for i in range(5):
        people = frame(i / 2)["detections"]
        people.append(dict(class_name="person", track_id=1, conf=0.9, box=SCENE["cashier_seat"]))
        frames.append(
            FrameObservation.model_validate(
                dict(
                    idx=i,
                    t_sec=i / 2,
                    detections=[
                        dict(
                            cls=p["class_name"],
                            track_id=p["track_id"],
                            conf=p["conf"],
                            box=dict(zip(("x1", "y1", "x2", "y2"), p["box"], strict=True)),
                        )
                        for p in people
                    ],
                )
            )
        )
    scene = ctx.scene.model_copy(
        update=dict(
            layout=SceneLayout(
                zones=[
                    Zone(kind=ZoneKind.DESK, polygon=[(0, 0), (1, 0), (1, 1), (0, 1)], conf=1),
                    Zone(
                        kind=ZoneKind.CASHIER_SIDE,
                        polygon=[(0, 0), (0.2, 0), (0.2, 1), (0, 1)],
                        conf=1,
                    ),
                ],
                orientation="calibrated",
                calibrated_from_sec=0,
                conf=1,
            ),
            roles=[
                TrackRole(
                    track_id=1,
                    role=Role.CASHIER,
                    conf=0.7,
                    first_t=0,
                    last_t=2,
                    entered_via_door=False,
                    exited_via_door=False,
                )
            ],
        )
    )
    return ctx.model_copy(
        update=dict(
            scene=scene,
            trace=ctx.trace.model_copy(update={"frames": frames}),
            cfg=RuleConfig(code="THIRD_PERSON_DURING_SERVICE"),
        )
    )


def test_registered_third_person_uses_frozen_trace_pts():
    from cashguard.contracts.findings import RuleConfig
    from cashguard.pipeline.rules.registry import discover, evaluate_all

    registry = discover()
    configs = {
        code: RuleConfig(code=code, enabled=code == "THIRD_PERSON_DURING_SERVICE")
        for code in registry.codes
    }
    configs["THIRD_PERSON_DURING_SERVICE"] = RuleConfig(code="THIRD_PERSON_DURING_SERVICE")
    (event,), _ = evaluate_all(registry_context(), configs, registry)
    assert (event.code, event.start_sec, event.end_sec) == ("THIRD_PERSON_DURING_SERVICE", 0, 2)


@pytest.mark.parametrize(
    "polygon",
    [
        [(0, 0), (1, 0), (0, 1)],
        [(0, 0), (1, 1), (1, 0), (0, 1)],
        [(-0.1, 0), (1, 0), (1, 1), (-0.1, 1)],
    ],
)
def test_registered_third_person_does_not_expand_invalid_zones(polygon):
    from cashguard.pipeline.rules.registry import discover

    ctx = registry_context()
    zones = [
        ctx.scene.layout.zones[0].model_copy(update={"polygon": polygon}),
        ctx.scene.layout.zones[1],
    ]
    ctx = ctx.model_copy(
        update={
            "scene": ctx.scene.model_copy(
                update={"layout": ctx.scene.layout.model_copy(update={"zones": zones})}
            )
        }
    )
    with pytest.raises(ValueError, match="зон"):
        discover().rules["THIRD_PERSON_DURING_SERVICE"].evaluate(ctx)


def test_registered_third_person_does_not_guess_cashier():
    from cashguard.pipeline.rules.registry import discover

    ctx = registry_context()
    for roles in ([], [*ctx.scene.roles, ctx.scene.roles[0].model_copy(update={"track_id": 2})]):
        with pytest.raises(ValueError, match="кассир"):
            discover().rules["THIRD_PERSON_DURING_SERVICE"].evaluate(
                ctx.model_copy(update={"scene": ctx.scene.model_copy(update={"roles": roles})})
            )


def test_manual_and_frozen_routes_do_not_treat_whole_cashier_zone_as_person():
    from cashguard.contracts.geometry import BBox
    from cashguard.pipeline.rules.registry import discover

    ctx = registry_context()
    boxes = {1: [0.1, 0.1, 0.4, 0.7], 2: [0.22, 0, 0.55, 0.82], 3: [0.65, 0.2, 0.85, 0.8]}
    frames = [
        observed.model_copy(
            update={
                "detections": [
                    d.model_copy(
                        update={
                            "box": BBox(
                                x1=boxes[d.track_id][0],
                                y1=boxes[d.track_id][1],
                                x2=boxes[d.track_id][2],
                                y2=boxes[d.track_id][3],
                            )
                        }
                    )
                    for d in observed.detections
                    if d.track_id is not None
                ]
            }
        )
        for observed in ctx.trace.frames
    ]
    zones = [
        ctx.scene.layout.zones[0],
        ctx.scene.layout.zones[1].model_copy(
            update={"polygon": [(0, 0), (0.52, 0), (0.52, 0.82), (0, 0.82)]}
        ),
    ]
    ctx = ctx.model_copy(
        update={
            "trace": ctx.trace.model_copy(update={"frames": frames}),
            "scene": ctx.scene.model_copy(
                update={"layout": ctx.scene.layout.model_copy(update={"zones": zones})}
            ),
        }
    )
    numeric = [
        dict(
            t_sec=i / 2,
            detections=[
                dict(class_name="person", conf=0.9, track_id=track, box=box)
                for track, box in boxes.items()
            ],
        )
        for i in range(5)
    ]
    expected = evaluate_frames(
        numeric, {"cashier_track_id": 1, "cashier_seat": boxes[1]}, desk=[0, 0, 1, 1]
    )
    manual = evaluate_frames(
        numeric, {"cashier_track_id": 1, "cashier_roi": [0, 0, 0.52, 0.82]}, desk=[0, 0, 1, 1]
    )
    assert len(expected) == 1
    assert manual == expected
    assert discover().rules["THIRD_PERSON_DURING_SERVICE"].evaluate(ctx) == expected
