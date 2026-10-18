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
