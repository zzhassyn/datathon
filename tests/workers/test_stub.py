import base64
import time
from io import BytesIO

import av
import pytest

from cashguard.core.budget import Budget, DeadlineTiers
from cashguard.pipeline.stub import StubDeadlineError, StubInputError, run_stub_analysis


@pytest.mark.parametrize(
    "scenario,verdict", [("clean", "no_violation"), ("violation", "violation")]
)
def test_metadata_scenario_and_valid_jpeg(tmp_path, make_video, scenario, verdict):
    # Deliberately misleading filenames must have no influence on the verdict.
    path = make_video(
        tmp_path / ("clean.mov" if scenario == "violation" else "violation.mov"),
        verdict=scenario,
    )
    result = run_stub_analysis(
        path, "a" * 32, budget=Budget(DeadlineTiers.from_received_at(time.time()))
    )
    assert result.jury["status"] == "done"
    assert result.jury["verdict"] == verdict
    if scenario == "violation":
        violation = result.jury["violations"][0]
        frame = violation["evidence_frames"][0]
        assert violation["timecode_sec"] == frame["timestamp_sec"] == 0
        assert frame["image_base64"].startswith("/9j/")
        with av.open(BytesIO(base64.b64decode(frame["image_base64"]))) as image:
            decoded = next(image.decode(video=0))
            assert (decoded.width, decoded.height) == (160, 96)
        assert "AI-анализ" in violation["description"]


def test_unmarked_video_never_fabricates_clean(tmp_path):
    path = tmp_path / "clean.mov"
    with av.open(str(path), "w", format="mov") as output:
        stream = output.add_stream("mpeg4", rate=5)
        stream.width, stream.height, stream.pix_fmt = 32, 32, "yuv420p"
        frame = av.VideoFrame(32, 32, "rgb24")
        frame.planes[0].update(bytes(frame.planes[0].buffer_size))
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    with pytest.raises(StubInputError):
        run_stub_analysis(
            path, "b" * 32, budget=Budget(DeadlineTiers.from_received_at(time.time()))
        )


def test_deadline_counts_from_request_not_worker_start(tmp_path, make_video):
    path = make_video(tmp_path / "test.mov")
    with pytest.raises(StubDeadlineError):
        run_stub_analysis(
            path,
            "c" * 32,
            budget=Budget(DeadlineTiers.from_received_at(time.time() - 240)),
        )


def test_delay_checks_deadline_cooperatively(tmp_path, make_video):
    path = make_video(tmp_path / "test.mov")
    tiers = DeadlineTiers.from_received_at(time.time() - 234.8)
    started = time.monotonic()
    with pytest.raises(StubDeadlineError):
        run_stub_analysis(path, "c" * 32, budget=Budget(tiers), delay_sec=5)
    assert time.monotonic() - started < 1


def test_deep_probe_rejects_too_short_even_with_valid_stub_marker(tmp_path, make_video):
    path = make_video(tmp_path / "tiny.mov", duration=0.2)
    with pytest.raises(StubInputError):
        run_stub_analysis(
            path, "d" * 32, budget=Budget(DeadlineTiers.from_received_at(time.time()))
        )
