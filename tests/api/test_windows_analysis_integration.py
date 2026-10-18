import json
import os

from cashguard.api.main import create_app
from cashguard.pipeline.local_config import AnalysisBlocked, confirmed_calibration
from cashguard.workers.perception_worker import PerceptionWorker


def test_full_torch_api_can_be_created_without_importing_ml(settings, tmp_path):
    settings.pipeline_mode = "full"
    settings.vision_backend = "torch"
    settings.local_analysis_config = tmp_path / "config.json"
    app = create_app(settings)
    assert app.state.settings.pipeline_mode == "full"


def test_saved_stub_provenance_survives_server_mode_switch(client, settings, synthetic):
    with synthetic.open("rb") as source:
        job_id = client.post("/api/ui/jobs", files={"file": ("source.mov", source)}).json()[
            "job_id"
        ]
    settings.pipeline_mode = "full"
    assert client.get(f"/api/ui/jobs/{job_id}").json()["pipeline_mode"] == "stub"


def test_missing_calibration_never_becomes_clean_result(
    client, app, settings, synthetic, tmp_path, monkeypatch
):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"calibration_file": str(tmp_path / "missing.json")}))
    with synthetic.open("rb") as source:
        job_id = client.post("/api/ui/jobs", files={"file": ("source.mov", source)}).json()[
            "job_id"
        ]
    settings.pipeline_mode, settings.vision_backend = "full", "torch"
    settings.local_analysis_config = config
    worker = PerceptionWorker(settings)
    job = app.state.repo.claim_next(worker.name, pid=os.getpid())

    def blocked(*args, **kwargs):
        raise AnalysisBlocked("Не подтверждена зона стола кассира; итог заблокирован.")

    monkeypatch.setattr("cashguard.pipeline.windows_analysis.analyze", blocked)
    worker.process(job)
    view = client.get(f"/api/ui/jobs/{job_id}").json()
    assert view["status"] == "failed"
    assert view["verdict"] is None and view["violations"] == []
    assert "зона стола" in view["error"]["message_ru"]


def test_wrong_camera_and_unknown_ownership_block_results(tmp_path):
    path = tmp_path / "confirmed.json"
    path.write_text(
        json.dumps(
            dict(
                confirmed=True,
                cash_area_confirmed=True,
                staff_only_surface=True,
                staff_ownership_confirmed=False,
                video_sha256="a" * 64,
                width=464,
                height=848,
                rotation_deg=0,
            )
        )
    )
    config = {"calibration_file": str(path)}
    video = dict(sha256="a" * 64, width=464, height=848, rotation_deg=0)
    import pytest

    with pytest.raises(AnalysisBlocked, match="Принадлежность"):
        confirmed_calibration(config, video)
    video["sha256"] = "b" * 64
    with pytest.raises(AnalysisBlocked, match="другому видео"):
        confirmed_calibration(config, video)


def test_positive_numeric_rule_publishes_existing_jury_ui_and_evidence(
    client, app, settings, synthetic, tmp_path, monkeypatch
):
    """Explicit synthetic numeric fixture: tests publication, never claims ML accuracy."""
    from pathlib import Path

    import av
    import numpy as np
    from scripts.make_synthetic_mov import make_synthetic_mov

    from cashguard.pipeline.windows_analysis import analyze

    make_synthetic_mov(synthetic, verdict="violation", duration=3)

    with synthetic.open("rb") as source:
        job_id = client.post("/api/ui/jobs", files={"file": ("source.mov", source)}).json()[
            "job_id"
        ]
    worker = PerceptionWorker(settings)
    job = app.state.repo.claim_next(worker.name, pid=os.getpid())
    calibration = dict(
        confirmed=True,
        cash_area_confirmed=True,
        staff_only_surface=True,
        staff_ownership_confirmed=True,
        video_sha256=job.sha256,
        width=job.width,
        height=job.height,
        rotation_deg=0,
        desk_roi=[1, 1, job.width - 1, job.height - 1],
    )
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"calibration_file": str(calibration_path)}))

    def numeric_inference(request, **kwargs):
        directory = Path(request["out_dir"])
        (directory / "frames").mkdir(parents=True)
        encoder = av.CodecContext.create("mjpeg", "w")
        encoder.width, encoder.height, encoder.pix_fmt = job.width, job.height, "yuvj420p"
        image = av.VideoFrame.from_ndarray(
            np.zeros((job.height, job.width, 3), dtype=np.uint8), format="rgb24"
        )
        jpeg = b"".join(bytes(packet) for packet in encoder.encode(image))
        (directory / "frames/frame.jpg").write_bytes(jpeg)
        records = [
            dict(
                idx=i,
                t_sec=i * 0.5,
                pts=i * 45000,
                image_name="frame.jpg",
                width=job.width,
                height=job.height,
                detections=[
                    dict(class_name="person", conf=0.9, box=[0.05, 0.05, 0.95, 0.95]),
                    dict(class_name="personal_bag", conf=0.9, box=[0.2, 0.2, 0.4, 0.4]),
                ],
                hands=[dict(conf=0.9, box=[0.4, 0.3, 0.5, 0.4])],
            )
            for i in range(6)
        ]
        report = dict(
            video=dict(
                duration_sec=job.duration_sec,
                width=job.width,
                height=job.height,
                rotation_deg=0,
                fps_avg=25,
                is_vfr=False,
                codec="h264",
                pix_fmt="yuv420p",
                start_pts_sec=0,
                time_base="1/90000",
                sha256=job.sha256,
            ),
            frames=records,
            coverage={"complete": True},
            models={"main": {"name": "synthetic numeric fixture"}},
            timings={"perception": 0.1},
        )
        (directory / "observations.json").write_text(json.dumps(report))

    monkeypatch.setattr("cashguard.pipeline.perception.windows_inference.run", numeric_inference)
    output = settings.paths.job_dir(job_id) / "numeric-fixture"
    output.mkdir()
    result = analyze(
        Path(job.input_path), output, job_id, job.tiers.finalize_at, config_path, lambda *args: None
    )
    assert worker.publish_analysis(job, result, output / "evidence")
    view = client.get(f"/api/ui/jobs/{job_id}").json()
    assert view["status"] == "done" and view["verdict"] == "violation"
    (event,) = view["violations"]
    assert event["code"] == "PERSONAL_ITEMS" and event["end_sec"] == 2.5
    assert event["legal"][0]["clause"] == "п. 12 ч. 1"
    for frame in event["evidence"]:
        assert client.get(frame["url"]).status_code == 200
