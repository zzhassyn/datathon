"""The opt-in diagnostic never widens jury contracts or exposes arbitrary local files."""

import json

import pytest
from fastapi.testclient import TestClient

from cashguard.api.main import create_app
from cashguard.core.settings import Settings


def local_app(tmp_path, **overrides):
    return create_app(
        Settings(
            data_dir=tmp_path,
            local_model_python=tmp_path / "python",
            local_model_weights=tmp_path / "weights.pt",
            **overrides,
        )
    )


def test_disabled_by_default_and_not_in_openapi(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app, client=("127.0.0.1", 123)) as client:
        assert client.post("/api/local-model/check").status_code == 404
        assert not any(p.startswith("/api/local-model") for p in app.openapi()["paths"])


def test_remote_and_forwarded_requests_rejected_before_body(tmp_path):
    app = local_app(tmp_path)
    with TestClient(app, client=("192.0.2.1", 123)) as client:
        assert client.post("/api/local-model/check", content=b"invalid").status_code == 403
    with TestClient(app, client=("127.0.0.1", 123)) as client:
        for headers in ({"Origin": "https://other.example"}, {"CF-Connecting-IP": "127.0.0.1"}):
            assert client.post("/api/local-model/check", headers=headers).status_code == 403


def test_session_guard_and_private_assets(tmp_path):
    app = local_app(tmp_path, ui_access_code="test-code")
    with TestClient(app, client=("127.0.0.1", 123)) as client:
        assert client.get("/api/local-model/" + "a" * 32).status_code == 401
    app = local_app(tmp_path)
    root = tmp_path / "model-checks" / ("a" * 32)
    root.mkdir(parents=True)
    (root / "worker.log").write_text("private diagnostic log")
    (root / "report.json").write_text(json.dumps({"frames": [{"name": "frame-0000.jpg"}]}))
    (root / "annotated.mp4").write_bytes(b"synthetic test bytes")
    with TestClient(app, client=("127.0.0.1", 123)) as client:
        base = "/api/local-model/" + "a" * 32
        assert client.get(base + "/worker.log").status_code == 404
        assert client.get(base + "/frame-0000.jpg").status_code == 404
        report = client.get(base).json()
        assert report["video_url"] == base + "/annotated.mp4"
        assert report["frames"][0]["image_url"] == base + "/frame-0000.jpg"


def test_symlink_cannot_expose_private_asset(tmp_path):
    app = local_app(tmp_path)
    root = tmp_path / "model-checks" / ("a" * 32)
    root.mkdir(parents=True)
    (root / "worker.log").write_text("private diagnostic log")
    try:
        (root / "frame-0000.jpg").symlink_to(root / "worker.log")
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows не разрешает создание symlink (WinError 1314)")
        raise
    with TestClient(app, client=("127.0.0.1", 123)) as client:
        assert client.get("/api/local-model/" + "a" * 32 + "/frame-0000.jpg").status_code == 404


def test_invalid_video_never_starts_ml_and_removes_input(tmp_path, monkeypatch):
    app = local_app(tmp_path)
    (tmp_path / "python").touch()
    (tmp_path / "weights.pt").touch()
    monkeypatch.setattr(
        "cashguard.api.routes.model_check.psutil.virtual_memory",
        type("Memory", (), {"available": 2_000_000_000}),
    )
    with TestClient(app, client=("127.0.0.1", 123)) as client:
        response = client.post(
            "/api/local-model/check", files={"file": ("bad.mov", b"invalid video")}
        )
        assert response.status_code == 415
    assert not list((tmp_path / "model-checks").rglob("input.mov"))
