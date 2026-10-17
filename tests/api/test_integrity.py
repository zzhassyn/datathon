"""Regression coverage for durable uploads and corrupted result artifacts."""

import asyncio
import errno
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from cashguard.api.ingest_service import ingest_upload
from cashguard.pipeline.ingest.probe import VideoInfo
from cashguard.pipeline.render.jury import validate_jury
from cashguard.workers.perception_worker import PerceptionWorker


def upload(client, synthetic, auth, *, ui=False):
    with synthetic.open("rb") as stream:
        response = client.post(
            "/api/ui/jobs" if ui else "/api/v1/analyze",
            files={"file": ("synthetic.mov", stream)},
            headers={} if ui else auth,
        )
    assert response.status_code == 200
    return response.json()["job_id"]


def assert_failed_jury(response, job_id):
    assert response.status_code == 200
    assert response.json() == {
        "job_id": job_id,
        "status": "failed",
        "verdict": None,
        "violations": [],
    }
    validate_jury(response.json())


def test_cancel_during_committed_insert_preserves_durable_video(app, monkeypatch):
    """Cancellation after DB COMMIT must not unlink the already accepted input."""
    repo = app.state.repo
    original_create = repo.create_job
    committed = threading.Event()
    release = threading.Event()
    captured = []

    def slow_create(**kwargs):
        job = original_create(**kwargs)
        captured.append(job)
        committed.set()
        if not release.wait(5):
            raise AssertionError("test did not release insert thread")
        return job

    async def fake_receive(request, directory, settings, received_at):
        directory.mkdir()
        path = directory / "input.mov"
        path.write_bytes(b"synthetic upload; probe mocked")
        return path, "a" * 64

    monkeypatch.setattr(repo, "create_job", slow_create)
    monkeypatch.setattr("cashguard.api.ingest_service.receive_video", fake_receive)
    monkeypatch.setattr(
        "cashguard.api.ingest_service.probe",
        lambda path: VideoInfo(1.0, 64, 64, "h264"),
    )

    async def exercise():
        task = asyncio.create_task(ingest_upload(SimpleNamespace(app=app)))
        try:
            assert await asyncio.to_thread(committed.wait, 3)
            task.cancel()
            asyncio.get_running_loop().call_later(0.05, release.set)
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
        assert len(captured) == 1
        job = repo.get_job(captured[0].id)
        assert job is not None
        assert Path(job.input_path).is_file()

    asyncio.run(exercise())


@pytest.mark.parametrize("corruption", ["invalid_json", "extra_key", "wrong_identity"])
def test_corrupted_canonical_jury_never_returned_as_done(
    client, app, synthetic, auth, settings, corruption
):
    job_id = upload(client, synthetic, auth)
    assert PerceptionWorker(settings).run_once()
    first = client.get(f"/api/v1/jobs/{job_id}", headers=auth)
    assert first.json()["status"] == "done"
    path = Path(app.state.repo.get_job(job_id).jury_path)
    if corruption == "invalid_json":
        path.write_text("{truncated")
    else:
        # Valid synthetic result is local; never printed to tool output.
        document = json.loads(path.read_text())
        if corruption == "extra_key":
            document["private_debug"] = True
        else:
            document["job_id"] = "f" * 32
        path.write_text(json.dumps(document))
    assert_failed_jury(client.get(f"/api/v1/jobs/{job_id}", headers=auth), job_id)


def test_corrupted_materialized_linked_jury_is_not_served(
    client, app, synthetic, auth, settings
):
    canonical = upload(client, synthetic, auth)
    linked = upload(client, synthetic, auth)
    assert app.state.repo.get_job(linked).linked_to == canonical
    assert PerceptionWorker(settings).run_once()
    first = client.get(f"/api/v1/jobs/{linked}", headers=auth)
    assert first.json()["job_id"] == linked
    assert first.json()["status"] == "done"
    linked_result = settings.paths.job_dir(linked) / "jury.json"
    assert linked_result.is_file()
    linked_result.write_text("{truncated")
    assert_failed_jury(client.get(f"/api/v1/jobs/{linked}", headers=auth), linked)


@pytest.mark.parametrize("stage", ["mkdir", "metadata", "sqlite"])
def test_storage_exhaustion_before_commit_returns_507_and_cleans_up(
    client, app, synthetic, auth, monkeypatch, stage
):
    if stage == "mkdir":
        mkdir = Path.mkdir
        jobs_dir = app.state.settings.paths.jobs_dir

        def full_mkdir(path, *args, **kwargs):
            if path.parent == jobs_dir:
                raise OSError(errno.ENOSPC, "injected full storage")
            return mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", full_mkdir)
    elif stage == "metadata":

        def full_metadata(*args, **kwargs):
            raise OSError(errno.ENOSPC, "injected full storage")

        monkeypatch.setattr(
            "cashguard.api.ingest_service.atomic_write_json", full_metadata
        )
    else:

        def full_database(*args, **kwargs):
            error = sqlite3.OperationalError("database or disk is full")
            error.sqlite_errorcode = sqlite3.SQLITE_FULL
            raise error

        monkeypatch.setattr(app.state.repo, "create_job", full_database)
    with synthetic.open("rb") as stream:
        response = client.post(
            "/api/v1/analyze",
            files={"file": ("synthetic.mov", stream)},
            headers=auth,
        )
    assert response.status_code == 507
    assert list(app.state.settings.paths.jobs_dir.iterdir()) == []
    assert app.state.repo.queue_depth() == 0


@pytest.mark.parametrize(
    "corruption",
    ["{truncated", '{"violations":null}', '{"violations":[{"evidence":[{}]}]}'],
)
def test_corrupted_ui_result_returns_explicit_failed_view(
    client, app, synthetic, auth, settings, corruption
):
    job_id = upload(client, synthetic, auth, ui=True)
    assert PerceptionWorker(settings).run_once()
    path = Path(app.state.repo.get_job(job_id).ui_result_path)
    path.write_text(corruption)
    response = client.get(f"/api/ui/jobs/{job_id}")
    assert response.status_code == 200
    result = response.json()
    assert result["job_id"] == job_id
    assert result["status"] == "failed"
    assert result["verdict"] is None
    assert result["violations"] == []
    assert result["error"] is not None
