import asyncio
import hashlib
import os
import struct
from pathlib import Path

import httpx
import pytest

from cashguard.api.main import create_app
from cashguard.pipeline.render.jury import validate_jury


def post(client, path, auth, url="/api/v1/analyze"):
    with path.open("rb") as file:
        return client.post(
            url,
            files={"file": ("../../untrusted.txt", file, "text/plain")},
            headers=auth,
        )


def test_durable_accept_and_trailing_slash(client, app, synthetic, auth):
    response = post(client, synthetic, auth, "/api/v1/analyze/")
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    job = app.state.repo.get_job(job_id)
    assert job.status == "queued"
    assert Path(job.input_path).read_bytes() == synthetic.read_bytes()
    assert job.sha256 == hashlib.sha256(synthetic.read_bytes()).hexdigest()
    assert not list(app.state.settings.paths.jobs_dir.rglob("*.part"))
    view = client.get(f"/api/v1/jobs/{job_id}/", headers=auth)
    assert view.status_code == 200
    assert view.json() == {
        "job_id": job_id,
        "status": "processing",
        "verdict": None,
        "violations": [],
    }
    validate_jury(view.json())


@pytest.mark.parametrize("job_id", ["bad", "../x", "a" * 32])
def test_unknown_jobs(client, auth, job_id):
    assert client.get(f"/api/v1/jobs/{job_id}", headers=auth).status_code == 404


@pytest.mark.parametrize(
    "body,content_type,status",
    [
        (b"garbage", "application/octet-stream", 422),
        (
            b'--x\r\nContent-Disposition: form-data; name="other"\r\n\r\nhi\r\n--x--\r\n',
            "multipart/form-data; boundary=x",
            422,
        ),
        (
            b'--x\r\nContent-Disposition: form-data; name="file"; filename="x"\r\n\r\nhi',
            "multipart/form-data; boundary=x",
            422,
        ),
    ],
)
def test_bad_multipart(client, app, auth, body, content_type, status):
    response = client.post(
        "/api/v1/analyze", content=body, headers={**auth, "Content-Type": content_type}
    )
    assert response.status_code == status
    assert set(response.json()) == {"detail"}
    assert list(app.state.settings.paths.jobs_dir.iterdir()) == []


def test_unreadable_415(client, app, auth):
    response = client.post(
        "/api/v1/analyze",
        files={"file": ("video.mov", b"not video", "video/quicktime")},
        headers=auth,
    )
    assert response.status_code == 415
    assert list(app.state.settings.paths.jobs_dir.iterdir()) == []


def test_duplicate_parts(client, app, synthetic, auth):
    data = synthetic.read_bytes()
    response = client.post(
        "/api/v1/analyze",
        files=[("file", ("a", data)), ("file", ("b", data))],
        headers=auth,
    )
    assert response.status_code == 422
    assert list(app.state.settings.paths.jobs_dir.iterdir()) == []


def test_length_limit_before_body(app, settings):
    settings.max_upload_mb = 0.001

    async def receive():
        pytest.fail("oversized body read")

    messages = []

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "method": "POST",
        "path": "/api/v1/analyze",
        "raw_path": b"/api/v1/analyze",
        "query_string": b"",
        "headers": [(b"x-api-key", b"test-jury"), (b"content-length", b"1001")],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
        "http_version": "1.1",
    }
    asyncio.run(app(scope, receive, send))
    assert messages[0]["status"] == 413


def test_stream_limit_without_content_length(app, settings):
    settings.max_upload_mb = 0.001

    async def run():
        async def body():
            yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="x"\r\n\r\n'
            yield b"x" * 1100
            pytest.fail("oversized stream consumed past limit")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/v1/analyze",
                content=body(),
                headers={
                    "X-API-Key": "test-jury",
                    "Content-Type": "multipart/form-data; boundary=x",
                },
            )
            assert result.status_code == 413

    asyncio.run(run())
    assert list(settings.paths.jobs_dir.iterdir()) == []


def test_low_storage_507(client, app, auth, monkeypatch):
    monkeypatch.setattr("cashguard.api.upload.disk_free_gb", lambda path: -1)
    assert (
        client.post("/api/v1/analyze", files={"file": ("x", b"x")}, headers=auth).status_code == 507
    )
    assert list(app.state.settings.paths.jobs_dir.iterdir()) == []


def test_advisory_unknown_atom(client, synthetic, auth, tmp_path):
    # Appending an unknown top-level atom before ftyp is valid if chunk offsets
    # are shifted; synthetic generator normally writes moov after mdat.
    data = synthetic.read_bytes()
    shift = 24
    patched = bytearray(data)
    cursor = 0
    while True:
        index = data.find(b"stco", cursor)
        if index < 0:
            break
        count = struct.unpack_from(">I", data, index + 8)[0]
        for i in range(count):
            offset = index + 12 + 4 * i
            struct.pack_into(
                ">I", patched, offset, struct.unpack_from(">I", data, offset)[0] + shift
            )
        cursor = index + 4
    path = tmp_path / "unknown.mov"
    path.write_bytes(struct.pack(">I4s16s", shift, b"uuid", b"0" * 16) + patched)
    assert post(client, path, auth).status_code == 200


def test_dedup_new_ids_restart_and_lazy_finalize(client, app, synthetic, auth, settings):
    first = post(client, synthetic, auth).json()["job_id"]
    second = post(client, synthetic, auth).json()["job_id"]
    assert first != second
    assert app.state.repo.get_job(second).linked_to == first
    claim = app.state.repo.claim_next("test-worker", pid=os.getpid())
    assert claim is not None
    from cashguard.core.atomic import atomic_write_json

    result = {
        "job_id": first,
        "status": "done",
        "verdict": "no_violation",
        "violations": [],
    }
    path = settings.paths.job_dir(first) / "jury.json"
    atomic_write_json(path, result)
    app.state.repo.set_provisional(
        first,
        lease_owner="test-worker",
        lease_token=claim.lease_token,
        jury_path=path,
        verdict="no_violation",
        n_violations=0,
    )
    app.state.repo.db.connect().execute("UPDATE jobs SET finalize_at=0 WHERE id=?", (first,))
    response = client.get(f"/api/v1/jobs/{second}", headers=auth)
    assert response.json() == {**result, "job_id": second}
    from fastapi.testclient import TestClient

    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/v1/jobs/{second}", headers=auth).json() == response.json()


def test_json_errors_admin_and_health(client, auth):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 503
    assert client.get("/api/v1/admin/status", headers=auth).status_code == 403
    assert client.get("/api/v1/admin/status", headers={"X-API-Key": "test-team"}).status_code == 200
    for method, path, status in [
        ("get", "/missing", 404),
        ("get", "/api/v1/analyze", 405),
    ]:
        response = getattr(client, method)(path, headers=auth)
        assert response.status_code == status
        assert set(response.json()) == {"detail"}


def test_file_fsync_happens_before_sql_commit(client, app, synthetic, auth, monkeypatch):
    calls = []
    fsync = os.fsync
    create = app.state.repo.create_job

    def record_fsync(fd):
        calls.append("fsync")
        return fsync(fd)

    def record_create(**kwargs):
        assert "fsync" in calls
        assert Path(kwargs["input_path"]).is_file()
        calls.append("commit")
        return create(**kwargs)

    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(app.state.repo, "create_job", record_create)
    assert post(client, synthetic, auth).status_code == 200
    assert calls.index("fsync") < calls.index("commit")


def test_multipart_headers_are_bounded(client, app, auth):
    body = b'--x\r\nContent-Disposition: form-data; name="file"; filename="' + b"x" * 20_000
    response = client.post(
        "/api/v1/analyze",
        content=body,
        headers={**auth, "Content-Type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 422
    assert list(app.state.settings.paths.jobs_dir.iterdir()) == []


def test_upload_deadline_cleans_partial(app, settings, auth):
    settings.upload_timeout_sec = 0.02

    async def run():
        async def body():
            yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="x"\r\n\r\n'
            await asyncio.sleep(0.1)
            yield b"remaining"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/analyze",
                content=body(),
                headers={**auth, "Content-Type": "multipart/form-data; boundary=x"},
            )
            assert response.status_code == 422

    asyncio.run(run())
    assert list(settings.paths.jobs_dir.iterdir()) == []


def test_api_import_does_not_load_models():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import cashguard.api.main, sys; forbidden={'torch','ultralytics','coremltools','mlx','mlx_vlm','cv2'}; assert not forbidden.intersection(sys.modules)",  # noqa: E501
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
