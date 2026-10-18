"""Offline integration on synthetic rectangles, no model download or bank media."""

from types import SimpleNamespace

import pytest

from cashguard.pipeline.perception.windows_inference import boxes, make_tracker

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")
pytest.importorskip("lap")
pytest.importorskip("ultralytics")

Boxes = pytest.importorskip("ultralytics.engine.results").Boxes


def model_for(row):
    result = SimpleNamespace(
        boxes=Boxes(torch.tensor([row], dtype=torch.float32), orig_shape=(100, 100))
    )
    return SimpleNamespace(names={0: "person", 1: "cash"}, predict=lambda *a, **kw: [result])


def test_bot_sort_preserves_ids_and_resets_for_each_video():
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    tracker = make_tracker(2)
    assert tracker.backend.args.with_reid is False and tracker.backend.args.gmc_method == "none"
    first = boxes(model_for([10, 10, 30, 60, 0.9, 0]), image, "cpu", tracker=tracker)
    second = boxes(model_for([11, 10, 31, 60, 0.9, 0]), image, "cpu", tracker=tracker)
    assert first[0]["track_id"] == second[0]["track_id"] == 1
    fresh = make_tracker(2)
    assert fresh.backend.frame_id == 0 and not fresh.backend.tracked_stracks
    assert (
        boxes(model_for([10, 10, 30, 60, 0.9, 0]), image, "cpu", tracker=fresh)[0]["track_id"] == 1
    )
    assert "track_id" not in boxes(model_for([10, 10, 30, 60, 0.9, 0]), image, "cpu")[0]


def test_cross_class_track_match_cannot_link_money_to_person():
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    tracker = make_tracker(2)
    boxes(model_for([10, 10, 30, 60, 0.9, 0]), image, "cpu", tracker=tracker)
    changed = boxes(model_for([10, 10, 30, 60, 0.9, 1]), image, "cpu", tracker=tracker)
    assert changed[0]["track_id"] is None
