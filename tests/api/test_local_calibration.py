import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as connection:
        yield connection


def enable(settings, tmp_path, source):
    config = tmp_path / "local-analysis.json"
    config.write_text(
        json.dumps(
            {
                "calibration_source": str(source),
                "calibration_file": str(tmp_path / "confirmed.json"),
            }
        )
    )
    settings.local_analysis_config = config
    return tmp_path / "confirmed.json"


def test_calibration_is_opt_in_and_private(client):
    assert client.get("/api/local-calibration/meta").status_code == 404


def test_local_frame_metadata_and_confirmation(client, settings, synthetic, tmp_path):
    saved = enable(settings, tmp_path, synthetic)
    meta = client.get("/api/local-calibration/meta").json()
    assert meta["width"] > 0 and meta["height"] > 0
    assert len(meta["video_sha256"]) == 64 and meta["rotation_deg"] == 0
    assert client.get(meta["frame_url"]).headers["content-type"].startswith("image/jpeg")
    assert not saved.exists()
    roi = [1, 1, meta["width"] - 1, meta["height"] - 1]
    payload = {
        **meta,
        "desk_roi": roi,
        "confirmed": True,
        "cash_area_confirmed": True,
        "staff_only_surface": True,
    }
    response = client.post(
        "/api/local-calibration/confirm", json=payload, headers={"Origin": "http://127.0.0.1"}
    )
    assert response.status_code == 200
    document = json.loads(saved.read_text())
    assert document["desk_roi"] == roi
    assert document["video_sha256"] == meta["video_sha256"]
    assert document["confirmed_at"]


def test_calibration_rejects_unconfirmed_wrong_identity_and_bounds(
    client, settings, synthetic, tmp_path
):
    saved = enable(settings, tmp_path, synthetic)
    meta = client.get("/api/local-calibration/meta").json()
    payload = {
        **meta,
        "desk_roi": [-1, 0, 99999, 99999],
        "confirmed": True,
        "cash_area_confirmed": True,
        "staff_only_surface": True,
    }
    headers = {"Origin": "http://127.0.0.1"}
    assert (
        client.post("/api/local-calibration/confirm", json=payload, headers=headers).status_code
        == 422
    )
    payload["desk_roi"] = [1, 1, 20, 20]
    payload["video_sha256"] = "0" * 64
    assert (
        client.post("/api/local-calibration/confirm", json=payload, headers=headers).status_code
        == 422
    )
    assert not saved.exists()


def test_calibration_post_requires_same_origin(client, settings, synthetic, tmp_path):
    enable(settings, tmp_path, synthetic)
    response = client.post(
        "/api/local-calibration/confirm", json={}, headers={"Origin": "https://elsewhere.invalid"}
    )
    assert response.status_code == 403


def test_calibration_rejects_untrusted_host_even_from_loopback(
    client, settings, synthetic, tmp_path
):
    enable(settings, tmp_path, synthetic)
    response = client.get("/api/local-calibration/meta", headers={"Host": "untrusted.invalid"})
    assert response.status_code == 403


def test_saved_calibration_is_visible_without_reconfirmation_or_writes(
    client, settings, synthetic, tmp_path
):
    saved = enable(settings, tmp_path, synthetic)
    meta = client.get("/api/local-calibration/meta").json()
    assert meta["saved_calibration"] is None
    assert meta["calibration_status"] == "missing"
    payload = {
        **meta,
        "desk_roi": [1, 1, 20, 20],
        "confirmed": True,
        "cash_area_confirmed": True,
        "staff_only_surface": True,
        "staff_ownership_confirmed": False,
    }
    response = client.post(
        "/api/local-calibration/confirm", json=payload, headers={"Origin": "http://127.0.0.1"}
    )
    assert response.status_code == 200
    original = saved.read_bytes()
    reopened = client.get("/api/local-calibration/meta").json()
    assert reopened["calibration_status"] == "valid"
    assert reopened["saved_calibration"]["desk_roi"] == [1, 1, 20, 20]
    assert reopened["saved_calibration"]["staff_ownership_confirmed"] is False
    assert reopened["saved_calibration"]["confirmed_at"] == response.json()["confirmed_at"]
    assert saved.read_bytes() == original


def test_saved_calibration_from_other_camera_is_not_shown_as_current(
    client, settings, synthetic, tmp_path
):
    saved = enable(settings, tmp_path, synthetic)
    meta = client.get("/api/local-calibration/meta").json()
    saved.write_text(json.dumps({**meta, "video_sha256": "f" * 64, "desk_roi": [1, 1, 20, 20]}))
    reopened = client.get("/api/local-calibration/meta").json()
    assert reopened["calibration_status"] == "mismatch"
    assert reopened["saved_calibration"] is None


def test_scene_zones_require_explicit_confirmation_and_matching_coordinates(
    client, settings, synthetic, tmp_path
):
    saved = enable(settings, tmp_path, synthetic)
    meta = client.get("/api/local-calibration/meta").json()
    payload = {
        **meta,
        "desk_roi": [1, 1, 20, 20],
        "confirmed": True,
        "cash_area_confirmed": True,
        "staff_only_surface": True,
        "scene": {"cashier_roi": [0, 0, 0.5, 1], "entry_roi": [0.8, 0, 1, 1]},
    }
    headers = {"Origin": "http://127.0.0.1"}
    assert (
        client.post("/api/local-calibration/confirm", json=payload, headers=headers).status_code
        == 422
    )
    assert not saved.exists()
    payload["scene_zones_confirmed"] = True
    assert (
        client.post("/api/local-calibration/confirm", json=payload, headers=headers).status_code
        == 200
    )
    state = client.get("/api/local-calibration/meta").json()["saved_calibration"]
    assert state["scene"] == payload["scene"] and state["scene_zones_confirmed"] is True
    for invalid in ([0, 0, 2, 1], [0, 0, 0, 1], [True, 0, 1, 1]):
        payload["scene"]["entry_roi"] = invalid
        assert (
            client.post("/api/local-calibration/confirm", json=payload, headers=headers).status_code
            == 422
        )
