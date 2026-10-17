from pathlib import Path

import pytest
from tests.unit.ingest.test_preview import synthetic

from cashguard.contracts.ui import UiJobView
from cashguard.workers.perception_worker import PerceptionWorker


@pytest.mark.parametrize("codec,rotation", [("libx265", 0), ("libx264", 90)])
def test_worker_publishes_playable_preview_before_done(
    client, app, settings, tmp_path, codec, rotation
):
    path = synthetic(tmp_path / "source.mov", codec, rotation)
    with path.open("rb") as handle:
        response = client.post("/api/ui/jobs", files={"file": ("source.mov", handle)})
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert client.get(f"/api/ui/jobs/{job_id}").json()["video"] is None
    assert PerceptionWorker(settings).run_once()
    view = UiJobView.model_validate(client.get(f"/api/ui/jobs/{job_id}").json())
    assert view.status == "done" and view.video is not None
    assert (view.video.width, view.video.height) == ((96, 160) if rotation else (160, 96))
    response = client.get(view.video.url, headers={"Range": "bytes=0-63"})
    assert response.status_code == 206 and len(response.content) == 64
    assert response.headers["content-type"] == "video/mp4"
    assert "content-encoding" not in response.headers


@pytest.mark.parametrize("artifact", ["preview", "frame"])
def test_corrupt_media_is_not_served_as_valid_image_or_video(
    client, app, settings, tmp_path, artifact
):
    path = synthetic(tmp_path / "source.mov")
    with path.open("rb") as handle:
        job_id = client.post("/api/ui/jobs", files={"file": ("source.mov", handle)}).json()[
            "job_id"
        ]
    assert PerceptionWorker(settings).run_once()
    view = client.get(f"/api/ui/jobs/{job_id}").json()
    if artifact == "preview":
        file = settings.paths.job_dir(job_id) / "preview.mp4"
        url = view["video"]["url"]
    else:
        file = Path(app.state.repo.get_job(job_id).ui_result_path).parent / "evidence/01_event.jpg"
        url = view["violations"][0]["evidence"][0]["url"]
    assert client.get(url).status_code == 200
    file.write_bytes(b"corrupted synthetic artifact")
    response = client.get(url)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    if artifact == "preview":
        assert client.get(f"/api/ui/jobs/{job_id}").json()["video"] is None
