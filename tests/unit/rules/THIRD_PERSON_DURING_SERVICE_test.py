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
