"""Интеграция правил, описания и доказательств на синтетическом numeric sidecar."""

import json
import time
from pathlib import Path

import pytest
from tests.unit.scene.test_temporal import CALIBRATION, PARAMS, VIDEO, records

from cashguard.pipeline.local_config import AnalysisBlocked
from cashguard.pipeline.windows_analysis import analyze


def setup_report(tmp_path, monkeypatch, *, positive):
    video = {
        **VIDEO,
        "fps_avg": 2,
        "is_vfr": False,
        "codec": "h264",
        "pix_fmt": "yuv420p",
        "start_pts_sec": 0,
        "time_base": "1/1000",
    }
    frames = records(exit_gate=positive)
    for i, frame in enumerate(frames):
        frame.update(image_name=f"{i}.jpg", width=100, height=100)
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps({**CALIBRATION, "scene_zones_confirmed": True, "scene": PARAMS})
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "calibration_file": str(calibration),
                "temporal_rules": {"enabled": True, "codes": ["VALUABLES_LEFT"]},
            }
        )
    )

    def inference(request, **kwargs):
        assert request["tracking_enabled"] is True
        directory = Path(request["out_dir"])
        (directory / "frames").mkdir(parents=True)
        for frame in frames:
            (directory / "frames" / frame["image_name"]).write_bytes(b"synthetic evidence fixture")
        (directory / "observations.json").write_text(
            json.dumps(
                dict(video=video, frames=frames, coverage={"complete": True}, models={}, timings={})
            )
        )

    monkeypatch.setattr("cashguard.pipeline.perception.windows_inference.run", inference)
    return config


def test_temporal_handler_generates_event_time_evidence_and_catalog_description(
    tmp_path, monkeypatch
):
    config = setup_report(tmp_path, monkeypatch, positive=True)
    result = analyze(
        tmp_path / "source.mov",
        tmp_path / "job",
        "job-test",
        time.time() + 60,
        config,
        lambda *args: None,
    )
    assert result.verdict == "violation" and len(result.violations) == 1
    finding = result.violations[0]
    assert finding.code == "VALUABLES_LEFT" and finding.timecode_sec == 2.5
    assert "наличные" in finding.description_ru and finding.legal
    assert (tmp_path / "job/scene.json").is_file()
    assert finding.evidence[0].timestamp_sec == 2.5
    assert json.loads((tmp_path / "job/technical_scope.json").read_text())["evaluated_rules"] == [
        "VALUABLES_LEFT"
    ]


def test_no_candidate_preserves_unknown_not_clean_and_saves_diagnostics(tmp_path, monkeypatch):
    config = setup_report(tmp_path, monkeypatch, positive=False)
    with pytest.raises(AnalysisBlocked, match="Чистый вердикт"):
        analyze(
            tmp_path / "source.mov",
            tmp_path / "job",
            "job-test",
            time.time() + 60,
            config,
            lambda *args: None,
        )
    assert json.loads((tmp_path / "job/rule_candidates.json").read_text()) == []
    assert (tmp_path / "job/technical_scope.json").is_file()
