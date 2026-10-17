"""Contract injection only: these tests do not run or evaluate ML models."""

import json
import os
from pathlib import Path

import av
import numpy as np
import pytest

from cashguard.contracts.findings import AnalysisResult
from cashguard.pipeline.legal.catalog import load_catalog
from cashguard.pipeline.render.analysis import render_analysis
from cashguard.workers.perception_worker import PerceptionWorker


def analysis(job, evidence_dir):
    evidence_dir.mkdir(parents=True)
    frame = av.VideoFrame.from_ndarray(np.zeros((64, 64, 3), dtype=np.uint8), format="rgb24")
    encoder = av.CodecContext.create("mjpeg", "w")
    encoder.width, encoder.height, encoder.pix_fmt = 64, 64, "yuvj420p"
    jpeg = b"".join(bytes(packet) for packet in encoder.encode(frame))
    catalog = load_catalog()
    entry = next(item for item in catalog.entries.values() if item.legal)
    violations = []
    for index, at in enumerate([0.5, 1.0], 1):
        name = f"{index:02}_event.jpg"
        (evidence_dir / name).write_bytes(jpeg)
        violations.append(
            {
                "id": f"synthetic-{index}",
                "code": entry.code,
                "title_ru": entry.title_ru,
                "timecode_sec": at,
                "start_sec": at - 0.1,
                "end_sec": at + 0.1,
                "confidence": 0.8,
                "vlm_status": "skipped",
                "provisional": False,
                "description_ru": "Синтетическая проверка передачи данных; не вывод модели.",
                "legal": [
                    item.model_dump(mode="json") for item in entry.legal if item.applies_if is None
                ],
                "evidence": [
                    {"kind": "event", "timestamp_sec": at, "file": name, "width": 64, "height": 64}
                ],
            }
        )
    (evidence_dir / "03_context.jpg").write_bytes(jpeg)
    signal = catalog.signals.signals[0]
    return AnalysisResult.model_validate(
        {
            "job_id": job.id,
            "verdict": "violation",
            "result_level": "rules",
            "perception_level": "fast",
            "violations": violations,
            "signals": [
                {
                    "id": "synthetic-signal",
                    "code": signal.code,
                    "title_ru": signal.title_ru,
                    "t_sec": 0.75,
                    "description_ru": "Синтетический сигнал",
                    "evidence": [
                        {
                            "kind": "context",
                            "timestamp_sec": 0.75,
                            "file": "03_context.jpg",
                            "width": 64,
                            "height": 64,
                        }
                    ],
                }
            ],
            "video": {
                "duration_sec": job.duration_sec,
                "width": job.width,
                "height": job.height,
                "rotation_deg": 0,
                "fps_avg": 25,
                "is_vfr": False,
                "codec": "h264",
                "pix_fmt": "yuv420p",
                "start_pts_sec": 0,
                "time_base": "1/90000",
                "sha256": job.sha256,
            },
            "timings": {"perception": 0.125},
            "degradations": [{"stage": "vlm", "reason": "vlm_disabled"}],
            "versions": {
                "git_sha": "synthetic",
                "models_hash": "synthetic",
                "config_hash": catalog.config_hash,
            },
        }
    )


@pytest.fixture
def claimed(client, app, settings, synthetic):
    with synthetic.open("rb") as source:
        job_id = client.post("/api/ui/jobs", files={"file": ("source.mov", source)}).json()[
            "job_id"
        ]
    worker = PerceptionWorker(settings)
    job = app.state.repo.claim_next(worker.name, pid=os.getpid())
    assert job.id == job_id
    directory = settings.paths.job_dir(job.id) / "test-input-evidence"
    return worker, job, directory, analysis(job, directory)


def test_typed_analysis_publishes_events_signals_norms_and_jury(client, app, auth, claimed):
    worker, job, directory, result = claimed
    assert worker.publish_analysis(job, result, directory)
    view = client.get(f"/api/ui/jobs/{job.id}").json()
    assert view["pipeline_mode"] == "stub"  # Explicit test injection, never presented as ML.
    assert view["status"] == "done" and view["result_level"] == "rules"
    assert view["perception_level"] == "fast" and view["timings"]["perception"] == 0.125
    assert [event["timecode_sec"] for event in view["violations"]] == [0.5, 1.0]
    assert len(view["signals"]) == 1
    for expected, actual in zip(result.violations, view["violations"], strict=True):
        assert actual["description_ru"] == expected.description_ru
        assert [ref["quote_ru"] for ref in actual["legal"]] == [
            ref.quote_ru for ref in expected.legal
        ]
    for event in [*view["violations"], *view["signals"]]:
        assert client.get(event["evidence"][0]["url"]).status_code == 200
    jury = client.get(f"/api/v1/jobs/{job.id}", headers=auth).json()
    assert len(jury["violations"]) == 2 and set(jury) == {
        "job_id",
        "status",
        "verdict",
        "violations",
    }
    saved = Path(app.state.repo.get_job(job.id).jury_path).parent / "analysis.json"
    assert json.loads(saved.read_text())["versions"]["models_hash"] == "synthetic"
    # A late second publication cannot replace the completed result.
    assert worker.publish_analysis(job, result, directory) is False


@pytest.mark.parametrize(
    "fault", ["verdict", "path", "symlink", "dimensions", "timestamp", "duplicate", "missing"]
)
def test_invalid_analysis_never_becomes_clean_or_done(app, claimed, fault, tmp_path):
    worker, job, directory, result = claimed
    data = result.model_dump(mode="json")
    if fault == "verdict":
        data["verdict"] = "no_violation"
    elif fault == "path":
        data["violations"][0]["evidence"][0]["file"] = "../01_event.jpg"
    elif fault == "symlink":
        target = tmp_path / "outside.jpg"
        path = directory / "01_event.jpg"
        target.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows не разрешает создание symlink (WinError 1314)")
            raise
    elif fault == "dimensions":
        data["violations"][0]["evidence"][0]["width"] = 123
    elif fault == "timestamp":
        data["violations"][0]["evidence"][0]["timestamp_sec"] = 1.2
    elif fault == "duplicate":
        data["violations"][1]["id"] = data["violations"][0]["id"]
    else:
        (directory / "01_event.jpg").unlink()
    with pytest.raises((ValueError, OSError)):
        worker.publish_analysis(job, AnalysisResult.model_validate(data), directory)
    assert app.state.repo.get_job(job.id).status == "running"
    assert app.state.repo.get_job(job.id).jury_path is None


def test_signals_do_not_create_jury_violations(claimed):
    _, _, directory, result = claimed
    result = result.model_copy(update={"verdict": "no_violation", "violations": []})
    rendered = render_analysis(result, directory)
    assert rendered.jury.verdict == "no_violation" and rendered.jury.violations == []
    assert len(rendered.ui["signals"]) == 1
