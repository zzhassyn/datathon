"""Local, bounded container inspection; no filename or MIME trust.

VideoInfo is an internal adapter pending S0's contracts.video.VideoMeta.
"""

import math
from dataclasses import asdict, dataclass
from pathlib import Path

import av
from av.error import FFmpegError


class UnsupportedVideo(ValueError):
    """Container cannot be opened, or deep validation failed."""


@dataclass(frozen=True)
class VideoInfo:
    duration_sec: float
    width: int
    height: int
    codec: str
    rotation: int = 0
    stub_verdict: str | None = None
    needs_transcode: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def probe(path: Path | str, *, deep: bool = False) -> VideoInfo:
    try:
        # Explicit mov demuxer prevents playlists and external URL protocols.
        with av.open(str(path), format="mov", options={"protocol_whitelist": "file"}) as container:
            streams = container.streams.video
            stream = streams[0] if streams else None
            duration = float(container.duration or 0) / av.time_base
            if stream is not None and stream.duration is not None and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            if not math.isfinite(duration):
                duration = 0.0
            width = stream.codec_context.width if stream else 0
            height = stream.codec_context.height if stream else 0
            codec = stream.codec_context.name if stream else ""
            rotation = int(float(stream.metadata.get("rotate", "0"))) if stream else 0
            marker = container.metadata.get("comment", "")
            stub = (
                marker.removeprefix("cg-stub:")
                if marker in {"cg-stub:clean", "cg-stub:violation"}
                else None
            )
            # Display matrices are exposed on decoded frames, not rotate metadata.
            # Decode one frame to distinguish rotated H.264 from remux-safe input.
            first_frame = next(container.decode(stream), None) if stream is not None else None
            if first_frame is not None:
                rotation = int(getattr(first_frame, "rotation", rotation))
            if deep:
                if (
                    stream is None
                    or not 1 <= duration <= 900
                    or not (0 < width <= 4096 and 0 < height <= 4096)
                ):
                    raise UnsupportedVideo("Недопустимые параметры видеопотока")
                frame = first_frame
                if frame is None or frame.pts is None:
                    raise UnsupportedVideo("Первый видеокадр не декодируется")
                rotation = int(getattr(frame, "rotation", rotation))
            return VideoInfo(
                duration,
                width,
                height,
                codec,
                rotation,
                stub,
                codec != "h264" or rotation % 360 != 0,
            )
    except (FFmpegError, OSError, ValueError, OverflowError) as exc:
        if isinstance(exc, UnsupportedVideo):
            raise
        raise UnsupportedVideo("Контейнер видео не читается") from exc
