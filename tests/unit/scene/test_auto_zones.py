"""Проверки роли кассира на синтетических числовых наблюдениях."""

import pytest

from cashguard.pipeline.scene.auto_zones import auto_calibration

VIDEO = dict(sha256="a" * 64, width=100, height=100, rotation_deg=0, duration_sec=30)
DESK_PERSON = [0.3, 0.1, 0.6, 0.8]
BACKGROUND_PERSON = [0.8, 0.1, 0.95, 0.8]


def person(track_id, box):
    return dict(class_name="person", conf=0.9, track_id=track_id, box=box)


def report(people_by_frame):
    return dict(
        video=VIDEO,
        frames=[
            dict(
                t_sec=index / 2,
                detections=[
                    dict(class_name="laptop", conf=0.9, box=[0.4, 0.4, 0.5, 0.5]),
                    *people,
                ],
            )
            for index, people in enumerate(people_by_frame)
        ],
    )


def test_long_background_track_cannot_outvote_cashier_at_desk():
    frames = [[person(2, BACKGROUND_PERSON)] for _ in range(40)]
    for people in frames[:3]:
        people.append(person(1, DESK_PERSON))
    calibration = auto_calibration(report(frames))
    assert calibration["scene"]["cashier_track_id"] == 1
    assert "2" not in calibration["track_scores"]


def test_person_never_overlapping_desk_cannot_be_cashier():
    with pytest.raises(ValueError, match="кассир"):
        auto_calibration(report([[person(2, BACKGROUND_PERSON)] for _ in range(5)]))


@pytest.mark.parametrize("order", [(1, 2), (2, 1)])
def test_equal_cashier_scores_do_not_depend_on_detection_order(order):
    with pytest.raises(ValueError, match="однозначно"):
        auto_calibration(report([[person(track, DESK_PERSON) for track in order]] * 5))


def test_three_detections_in_one_frame_do_not_establish_cashier():
    frames = [[person(1, DESK_PERSON)] * 3, [], []]
    with pytest.raises(ValueError, match="кассир"):
        auto_calibration(report(frames))


def test_cashier_seat_uses_desk_observations_not_later_walk_away():
    frames = [[person(1, DESK_PERSON)] for _ in range(3)]
    frames.extend([[person(1, BACKGROUND_PERSON)] for _ in range(20)])
    calibration = auto_calibration(report(frames))
    assert calibration["scene"]["cashier_seat"] == DESK_PERSON


def test_equal_overlap_duplicates_keep_calibration_independent_of_order():
    first = person(1, DESK_PERSON)
    second = person(1, [0.34, 0.3, 0.95, 0.95])
    forward = auto_calibration(report([[first, second] for _ in range(5)]))
    reverse = auto_calibration(report([[second, first] for _ in range(5)]))
    assert forward == reverse
    assert forward["scene"]["cashier_track_id"] == 1
