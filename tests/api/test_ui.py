import asyncio
import base64
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from scripts.make_synthetic_mov import make_synthetic_mov

from cashguard.api.main import create_app
from cashguard.pipeline.render.jury import validate_jury
from cashguard.workers.perception_worker import PerceptionWorker


def upload(client, path, url="/api/ui/jobs", headers=None):
    with path.open("rb") as handle:
        return client.post(
            url,
            files={"file": ("ignored.mov", handle, "application/octet-stream")},
            headers=headers or {},
        )


@pytest.mark.parametrize("verdict", ["violation", "clean"])
def test_complete_ui_and_jury_scenario(client, app, settings, tmp_path, verdict, auth):
    synthetic = tmp_path / "scenario.mov"
    make_synthetic_mov(synthetic, verdict=verdict)
    accepted = upload(client, synthetic)
    assert accepted.status_code == 200
    job_id = accepted.json()["job_id"]
    assert client.get(f"/api/ui/jobs/{job_id}").json()["status"] == "queued"
    preview = client.get(f"/api/ui/jobs/{job_id}/video.mp4", headers={"Range": "bytes=0-99"})
    assert preview.status_code == 206
    assert len(preview.content) == 100
    assert preview.headers["cache-control"].startswith("private")
    worker = PerceptionWorker(settings)
    assert worker.run_once()
    ui = client.get(f"/api/ui/jobs/{job_id}").json()
    assert ui["status"] == "done"
    assert ui["verdict"] == ("violation" if verdict == "violation" else "no_violation")
    assert "stub" in ui["degradations"][0]["reason_ru"]
    jury = client.get(f"/api/v1/jobs/{job_id}", headers=auth)
    validate_jury(jury.json(), duration_sec=2)
    assert jury.json() == client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    if verdict == "violation":
        frame = ui["violations"][0]["evidence"][0]
        evidence = client.get(frame["url"])
        assert evidence.status_code == 200
        assert evidence.content[:3] == b"\xff\xd8\xff"
        assert "image_base64" not in str(ui)
        assert (
            base64.b64decode(
                jury.json()["violations"][0]["evidence_frames"][0]["image_base64"],
                validate=True,
            )
            == evidence.content
        )


def test_jury_data_hidden_from_ui(client, synthetic, auth, settings):
    job_id = upload(client, synthetic, "/api/v1/analyze", auth).json()["job_id"]
    PerceptionWorker(settings).run_once()
    for path in [
        f"/api/ui/jobs/{job_id}",
        f"/api/ui/jobs/{job_id}/video.mp4",
        f"/api/ui/jobs/{job_id}/frames/01_event.jpg",
    ]:
        assert client.get(path).status_code == 404


def test_ui_catalog_etag_and_paths(client, synthetic):
    catalog = client.get("/api/ui/catalog")
    assert catalog.status_code == 200
    assert (
        client.get(
            "/api/ui/catalog", headers={"If-None-Match": catalog.headers["etag"]}
        ).status_code
        == 304
    )
    job = upload(client, synthetic).json()["job_id"]
    for name in [
        "input.mov",
        "jury.json",
        "100_event.jpg",
        "01_other.jpg",
        "%2e%2e%2fmeta.json",
    ]:
        assert client.get(f"/api/ui/jobs/{job}/frames/{name}").status_code == 404


def test_ui_limits_do_not_limit_jury(client, app, synthetic, auth):
    assert upload(client, synthetic).status_code == 200
    assert upload(client, synthetic).status_code == 200
    response = upload(client, synthetic)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert upload(client, synthetic, "/api/v1/analyze", auth).status_code == 200


def test_jury_ui_profile_six_uploads_three_concurrent(settings, synthetic):
    from cashguard.core.settings import Settings

    adjusted = Settings(
        data_dir=settings.data_dir,
        api_keys=settings.api_keys,
        min_free_disk_gb=0,
        min_free_mem_gb=0,
        ui_rate_profile="jury",
    )
    with TestClient(create_app(adjusted)) as client:
        for _ in range(2):
            for _ in range(3):
                assert upload(client, synthetic).status_code == 200
            PerceptionWorker(adjusted).run_once()


def test_ui_access_code(settings, synthetic):
    from pydantic import SecretStr

    settings.ui_access_code = SecretStr("local-test-code")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/ui/catalog").status_code == 401
        assert client.post("/api/ui/session", json={"code": "wrong"}).status_code == 401
        session = client.post("/api/ui/session", json={"code": "local-test-code"})
        assert session.status_code == 200
        assert "HttpOnly" in session.headers["set-cookie"]
        assert "SameSite=strict" in session.headers["set-cookie"]
        assert client.get("/api/ui/catalog").status_code == 200


def test_ui_frames_symlink_escape(client, app, settings, synthetic, tmp_path):
    job_id = upload(client, synthetic).json()["job_id"]
    PerceptionWorker(settings).run_once()
    job = app.state.repo.get_job(job_id)
    evidence = Path(job.ui_result_path).parent / "evidence" / "01_event.jpg"
    evidence.unlink()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    try:
        evidence.symlink_to(outside)
    except OSError as exc:  # Windows без режима разработчика: WinError 1314
        pytest.skip(f"нет права на symlink в этой ОС: {exc}")
    assert client.get(f"/api/ui/jobs/{job_id}/frames/01_event.jpg").status_code == 404


def test_five_parallel_jury_uploads_and_linked_evidence(app, synthetic, auth, settings):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            data = synthetic.read_bytes()
            responses = await asyncio.gather(
                *[
                    client.post("/api/v1/analyze", files={"file": ("x", data)}, headers=auth)
                    for _ in range(5)
                ]
            )
            assert all(response.status_code == 200 for response in responses)
            ids = [response.json()["job_id"] for response in responses]
            assert len(set(ids)) == 5
            assert app.state.repo.queue_depth() == 1
            PerceptionWorker(settings).run_once()
            for job_id in ids:
                response = await client.get(f"/api/v1/jobs/{job_id}", headers=auth)
                assert response.json()["job_id"] == job_id
                assert response.json()["status"] == "done"
                validate_jury(response.json())

    asyncio.run(run())
