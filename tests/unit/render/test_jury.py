"""Only synthetic/checked-in public example JPEG data is used here."""

import base64
import copy
import json
from pathlib import Path

import pytest

from cashguard.pipeline.render.jury import (
    RenderError,
    render_jury,
    validate_jpeg,
    validate_jury,
)
from cashguard.pipeline.render.ui import build_ui_result_fragment

ROOT = Path(__file__).resolve().parents[3]
JOB_ID = "a" * 32


@pytest.fixture
def jpeg():
    fixture = json.loads((ROOT / "tests/fixtures/jury_example.json").read_text())
    return base64.b64decode(fixture["violations"][0]["evidence_frames"][0]["image_base64"])


def finding(jpeg, time=1.234, **overrides):
    result = {
        "type": "Синтетическое событие",
        "pts_event": time,
        "description": "Демонстрационный сценарий, не обнаружение реального нарушения.",
        "evidence_frames": [{"timestamp_sec": time, "jpeg_bytes": jpeg}],
    }
    result.update(overrides)
    return result


def test_frozen_schema_valid_and_no_extra_fields(jpeg):
    result = render_jury(JOB_ID, [finding(jpeg)], duration_sec=2)
    validate_jury(result, duration_sec=2)
    assert set(result) == {"job_id", "status", "verdict", "violations"}
    violation = result["violations"][0]
    assert result["verdict"] == "violation"
    assert violation["timecode_sec"] == 1.23
    assert violation["evidence_frames"][0]["timestamp_sec"] == 1.23
    assert violation["evidence_frames"][0]["image_base64"].startswith("/9j/")


def test_actual_jpeg_decodes_with_pyav(jpeg):
    import av

    frames = av.CodecContext.create("mjpeg", "r").decode(av.Packet(jpeg))
    assert len(frames) == 1
    assert frames[0].width > 0 and frames[0].height > 0


@pytest.mark.parametrize(
    "status,expected",
    [
        ("processing", "processing"),
        ("queued", "processing"),
        ("running", "processing"),
        ("failed", "failed"),
    ],
)
def test_nonfinal_public_status(status, expected):
    assert render_jury(JOB_ID, status=status) == {
        "job_id": JOB_ID,
        "status": expected,
        "verdict": None,
        "violations": [],
    }


def test_clean():
    assert render_jury(JOB_ID, [], duration_sec=2)["verdict"] == "no_violation"


def test_safe_fallback_clamps_sorts_caps_and_discards_private_fields(jpeg):
    items = [finding(jpeg, time) for time in (20, 5, 4, 3, -5, 1, 2)]
    items[0]["evidence_frames"] *= 4
    items[0]["debug"] = "not-for-jury"
    result = render_jury(JOB_ID, items, duration_sec=6)
    validate_jury(result, duration_sec=6)
    assert [item["timecode_sec"] for item in result["violations"]] == [0, 1, 2, 3, 4]
    assert "debug" not in json.dumps(result)


def test_safe_fallback_caps_evidence(jpeg):
    item = finding(jpeg)
    item["evidence_frames"] *= 4
    result = render_jury(JOB_ID, [item], duration_sec=2)
    assert len(result["violations"][0]["evidence_frames"]) == 3


def test_nan_event_uses_valid_evidence_pts(jpeg):
    item = finding(jpeg)
    item["pts_event"] = float("nan")
    result = render_jury(JOB_ID, [item], duration_sec=2)
    assert result["violations"][0]["timecode_sec"] == 1.23
    validate_jury(result, duration_sec=2)


def test_first_evidence_pts_is_never_relabelled(jpeg):
    item = finding(jpeg, 0.5)
    item["evidence_frames"][0]["timestamp_sec"] = 0.7
    result = render_jury(JOB_ID, [item], duration_sec=1)
    assert result["violations"][0]["timecode_sec"] == 0.7
    with pytest.raises(RenderError):
        render_jury(JOB_ID, [item], duration_sec=1, safe_fallback=False)


@pytest.mark.parametrize(
    "override",
    [
        {"evidence_frames": []},
        {"description": " "},
        {"type": ""},
        {"evidence_frames": [{"timestamp_sec": 1, "image_base64": "/9j/not-jpeg"}]},
    ],
)
def test_no_false_clean_verdict_when_evidence_or_text_irrecoverable(jpeg, override):
    result = render_jury(JOB_ID, [finding(jpeg, **override)], duration_sec=2)
    assert result == {
        "job_id": JOB_ID,
        "status": "failed",
        "verdict": None,
        "violations": [],
    }
    validate_jury(result)


@pytest.mark.parametrize(
    "raw", [b"", b"\xff\xd8\xff\xd9", b"\xff\xd8\xff\xe0\xff\xff\xd9", b"not jpeg"]
)
def test_invalid_jpeg_is_rejected(raw):
    with pytest.raises(RenderError):
        validate_jpeg(raw)


def test_truncated_and_extra_data_jpeg_rejected(jpeg):
    for invalid in (jpeg[:-1], jpeg + b"trailer"):
        with pytest.raises(RenderError):
            validate_jpeg(invalid)


def test_reencoding_callback_can_repair_evidence(jpeg):
    item = finding(b"image to reencode")
    result = render_jury(JOB_ID, [item], duration_sec=2, reencode_jpeg=lambda raw: jpeg)
    assert result["status"] == "done"


def test_rounding_does_not_exceed_duration(jpeg):
    result = render_jury(JOB_ID, [finding(jpeg, 1.999)], duration_sec=1.999)
    assert result["violations"][0]["timecode_sec"] == 1.99


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(debug=True),
        lambda d: d.update(verdict="no_violation"),
        lambda d: d["violations"][0].update(timecode_sec=float("nan")),
        lambda d: d["violations"][0]["evidence_frames"][0].update(
            image_base64="data:image/jpeg;base64,/9j/"
        ),
        lambda d: d["violations"][0]["evidence_frames"][0].update(timestamp_sec=0.5),
    ],
)
def test_validation_rejects_schema_and_semantic_mutations(jpeg, mutation):
    result = render_jury(JOB_ID, [finding(jpeg)], duration_sec=2)
    mutation(result)
    with pytest.raises(RenderError):
        validate_jury(result, duration_sec=2)


def test_does_not_mutate_inputs(jpeg):
    item = finding(jpeg)
    before = copy.deepcopy(item)
    render_jury(JOB_ID, [item], duration_sec=2)
    assert item == before


def test_ui_fragment_contains_urls_never_base64(jpeg):
    jury = render_jury(JOB_ID, [finding(jpeg)], duration_sec=2)
    fragment = build_ui_result_fragment(JOB_ID, jury, evidence_names=[["0_event.jpg"]])
    assert fragment["violations"][0]["evidence"][0]["url"].endswith("/frames/0_event.jpg")
    assert "base64" not in json.dumps(fragment)
    assert "status" not in fragment  # This is not a fabricated UiJobView.


@pytest.mark.parametrize(
    "name",
    ["../0_event.jpg", "100_event.jpg", "0_other.jpg", "/0_event.jpg", "0_event.jpg\n"],
)
def test_ui_rejects_unsafe_names(jpeg, name):
    jury = render_jury(JOB_ID, [finding(jpeg)], duration_sec=2)
    with pytest.raises(RenderError):
        build_ui_result_fragment(JOB_ID, jury, evidence_names=[[name]])
