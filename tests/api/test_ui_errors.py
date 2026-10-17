import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cashguard.contracts.ui import UiHttpError


@pytest.mark.parametrize(
    "status,detail,code",
    [
        (400, "disconnect", "UPLOAD_ABORTED"),
        (401, "secret", "UNAUTHORIZED"),
        (404, "private path", "NOT_FOUND"),
        (413, "too large", "FILE_TOO_LARGE"),
        (415, "codec internals", "UNSUPPORTED_MEDIA"),
        (422, "bad multipart", "INVALID_REQUEST"),
        (422, "upload deadline exceeded", "UPLOAD_TIMEOUT"),
        (429, "limit", "RATE_LIMITED"),
        (507, "private path", "INSUFFICIENT_STORAGE"),
        (500, "stack trace", "INTERNAL"),
    ],
)
def test_every_ui_http_error_is_safe_contract(app, status, detail, code):
    @app.get("/api/ui/test-error")
    def fail():
        raise HTTPException(status, detail, headers={"Retry-After": "7"} if status == 429 else None)

    with TestClient(app) as client:
        response = client.get("/api/ui/test-error")
    assert response.status_code == status
    value = UiHttpError.model_validate(response.json())
    assert value.code == code
    assert value.message_ru != detail
    assert response.headers["cache-control"] == "no-store"
    if status == 429:
        assert value.retry_after_sec == int(response.headers["Retry-After"]) == 7


def test_unhandled_error_does_not_leak(app):
    @app.get("/api/ui/crash")
    def crash():
        raise RuntimeError("private file path and secret")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/ui/crash")
    assert response.status_code == 500
    assert UiHttpError.model_validate(response.json()).code == "INTERNAL"
    assert "private" not in response.text


def test_real_upload_errors_follow_contract(client, settings, monkeypatch):
    invalid = client.post("/api/ui/jobs", files={"file": ("x.mov", b"garbage")})
    assert invalid.status_code == 415
    assert invalid.json()["code"] == "UNSUPPORTED_MEDIA"
    settings.max_upload_mb = 0.0001
    large = client.post("/api/ui/jobs", files={"file": ("x.mov", b"x" * 101)})
    assert large.status_code == 413
    assert large.json()["code"] == "FILE_TOO_LARGE"
    settings.max_upload_mb = 100
    monkeypatch.setattr("cashguard.api.upload.disk_free_gb", lambda _: -1)
    disk = client.post("/api/ui/jobs", files={"file": ("x.mov", b"x")})
    assert disk.status_code == 507
    assert disk.json()["code"] == "INSUFFICIENT_STORAGE"
    assert list(settings.paths.jobs_dir.iterdir()) == []
