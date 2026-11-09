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
