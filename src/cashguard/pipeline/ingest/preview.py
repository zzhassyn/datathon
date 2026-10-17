"""Browser H.264 previews. Expensive conversion is allowed only in the worker."""

import os
import sys
import time
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from av.error import FFmpegError
from av.video.stream import VideoStream

from .probe import VideoInfo


def make_preview(
    path: Path,
    meta: VideoInfo,
    out: Path,
    *,
    transcode: bool = False,
    deadline: float | None = None,
) -> Path | None:
    if meta.needs_transcode and not transcode:
        return None
    temporary = out.with_suffix(".part")
    codecs = ["h264_videotoolbox", "libx264"] if sys.platform == "darwin" else ["libx264"]
    for codec in codecs if meta.needs_transcode else [None]:
        try:
            with av.open(str(path), format="mov", options={"protocol_whitelist": "file"}) as source:
                if not source.streams.video:
                    return None
                video = source.streams.video[0]
                with av.open(
                    str(temporary), mode="w", format="mp4", options={"movflags": "+faststart"}
                ) as target:
                    if codec is None:
                        output = target.add_stream_from_template(video)
                        for packet in source.demux(video):
                            _check_deadline(deadline)
                            if packet.dts is None:
                                continue
                            packet.stream = output
                            target.mux(packet)
                    else:
                        output = target.add_stream(codec, rate=video.average_rate or Fraction(25))
                        if not isinstance(output, VideoStream):
                            raise ValueError("video encoder required")
                        width, height = meta.width, meta.height
                        if meta.rotation % 180:
                            width, height = height, width
                        scale = min(1.0, 1280 / max(width, height))
                        output.width = max(2, int(width * scale) // 2 * 2)
                        output.height = max(2, int(height * scale) // 2 * 2)
                        output.pix_fmt = "yuv420p"
                        output.time_base = Fraction(1, 90000)
                        output.codec_context.time_base = Fraction(1, 90000)
                        output.bit_rate = 2_000_000
                        if codec == "libx264":
                            output.options = {"preset": "veryfast", "crf": "24", "threads": "2"}
                        origin = None
                        previous = -1
                        for frame in source.decode(video):
                            _check_deadline(deadline)
                            if frame.pts is None or frame.time_base is None:
                                raise ValueError("preview requires frame PTS")
                            at = frame.pts * frame.time_base
                            if origin is None:
                                origin = at
                            pts = round((at - origin) * 90000)
                            if pts <= previous:
                                continue
                            previous = pts
                            pixels = np.asarray(frame.to_ndarray(format="rgb24"), dtype=np.uint8)
                            rotation = meta.rotation % 360
                            if rotation not in {0, 90, 180, 270}:
                                raise ValueError("unsupported rotation")
                            if rotation:
                                pixels = np.ascontiguousarray(np.rot90(pixels, rotation // 90))
                            image = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                            image = image.reformat(output.width, output.height, format="yuv420p")
                            image.pts, image.time_base = pts, Fraction(1, 90000)
                            for packet in output.encode(image):
                                target.mux(packet)
                        if origin is None:
                            raise ValueError("empty preview")
                        for packet in output.encode():
                            target.mux(packet)
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, out)
            return out
        except (FFmpegError, OSError, ValueError, RuntimeError, TimeoutError):
            # Hardware encoder may be unavailable; retry with bounded software encoding.
            pass
        finally:
            temporary.unlink(missing_ok=True)
    return None


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.time() >= deadline:
        raise TimeoutError("preview budget exhausted")
