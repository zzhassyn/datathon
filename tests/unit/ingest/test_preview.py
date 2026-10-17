"""Synthetic codec/rotation/VFR checks; no dataset material."""

import time
from fractions import Fraction

import av
import numpy as np
import pytest
from av.video.stream import VideoStream

from cashguard.pipeline.ingest.preview import make_preview
from cashguard.pipeline.ingest.probe import probe


def synthetic(path, codec="libx264", rotation=0):
    with av.open(str(path), "w", format="mov") as container:
        container.metadata["comment"] = "cg-stub:violation"
        stream = container.add_stream(codec, rate=5)
        assert isinstance(stream, VideoStream)
        stream.width, stream.height, stream.pix_fmt = 160, 96, "yuv420p"
        stream.options = {"threads": "1"}
        if codec == "libx265":
            stream.options["x265-params"] = "log-level=error:pools=1:frame-threads=1"
        stream.set_display_rotation(rotation)
        for pts in [0, 2, 5, 9, 12, 16]:
            pixels = np.zeros((96, 160, 3), dtype=np.uint8)
            pixels[:, :80] = (220, 20, 20)
            pixels[:, 80:] = (20, 20, 220)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = pts, Fraction(1, 10)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


@pytest.mark.parametrize(
    "codec,rotation", [("libx265", 0), ("libx264", 90), ("libx264", -90), ("libx264", 180)]
)
def test_browser_preview_codec_rotation_and_pts(tmp_path, codec, rotation):
    source = synthetic(tmp_path / "source.mov", codec, rotation)
    meta = probe(source, deep=True)
    assert meta.needs_transcode
    assert meta.rotation % 360 == rotation % 360
    preview = tmp_path / "preview.mp4"
    assert make_preview(source, meta, preview) is None
    assert make_preview(source, meta, preview, transcode=True) == preview
    output = probe(preview, deep=True)
    assert output.codec == "h264" and output.rotation == 0
    assert (output.width, output.height) == ((96, 160) if rotation % 180 else (160, 96))
    with av.open(str(source)) as input_video, av.open(str(preview)) as output_video:
        before = list(input_video.decode(video=0))
        after = list(output_video.decode(video=0))
        times = [timestamp(f) for f in before]
        assert [timestamp(f) for f in after] == pytest.approx(
            [t - times[0] for t in times], abs=0.03
        )
        expected = np.rot90(before[0].to_ndarray(format="rgb24"), (rotation % 360) // 90)
        actual = after[0].to_ndarray(format="rgb24")
        assert np.mean(np.abs(expected.astype(float) - actual.astype(float))) < 12
    assert not preview.with_suffix(".part").exists()


def test_failed_or_expired_preview_never_publishes_partial_file(tmp_path):
    source = synthetic(tmp_path / "source.mov", rotation=90)
    out = tmp_path / "preview.mp4"
    assert (
        make_preview(source, probe(source), out, transcode=True, deadline=time.time() - 1) is None
    )
    assert not out.exists()
    assert not out.with_suffix(".part").exists()


def timestamp(frame):
    assert frame.pts is not None and frame.time_base is not None
    return float(frame.pts * frame.time_base)
