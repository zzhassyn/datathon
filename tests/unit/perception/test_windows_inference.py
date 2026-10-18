import hashlib

import pytest

from cashguard.pipeline.perception.windows_inference import (
    camera_door_roi,
    checked_roi,
    inspect_weights,
    pts_seconds,
)


def test_pts_origin_is_used_instead_of_frame_number():
    assert pts_seconds(135000, "1/90000", 1) == 0.5
    with pytest.raises(ValueError):
        pts_seconds(None, "1/90000", 0)


def test_roi_rejects_unknown_coordinates():
    assert checked_roi(None, 100, 200) is None
    assert checked_roi([2, 4, 30, 50], 100, 200) == [2, 4, 30, 50]
    with pytest.raises(ValueError):
        checked_roi([0, 0, 101, 200], 100, 200)
    with pytest.raises(ValueError):
        checked_roi([0, 0, 100, 200], 100, 200)


def test_door_roi_is_used_only_for_confirmed_matching_camera_geometry():
    video = dict(sha256="a" * 64, width=100, height=200, rotation_deg=90)
    calibration = dict(
        video_sha256="a" * 64,
        width=100,
        height=200,
        rotation_deg=90,
        confirmed=True,
        roi=[2, 4, 30, 50],
    )
    assert camera_door_roi(calibration, video) == [2, 4, 30, 50]
    assert camera_door_roi({}, video) is None
    assert camera_door_roi({**calibration, "confirmed": False}, video) is None
    assert camera_door_roi({**calibration, "video_sha256": "b" * 64}, video) is None
    assert camera_door_roi({**calibration, "rotation_deg": 0}, video) is None


def test_weights_must_match_declared_sha_and_exist(tmp_path):
    weights = tmp_path / "trained.pt"
    weights.write_bytes(b"test bytes, never loaded as pickle")
    expected = hashlib.sha256(weights.read_bytes()).hexdigest()
    assert inspect_weights({"path": str(weights), "sha256": expected}) == weights
    with pytest.raises(ValueError):
        inspect_weights({"path": str(weights), "sha256": "0" * 64})
    with pytest.raises(FileNotFoundError):
        inspect_weights({"path": str(tmp_path / "missing.pt"), "sha256": expected})
