"""Browser preview without model imports. Only video is copied; audio is unused."""

import os
from pathlib import Path

import av

from .probe import VideoInfo


def make_preview(path: Path, meta: VideoInfo, out: Path) -> Path | None:
    if meta.needs_transcode:
        return None
    temporary = out.with_suffix(".part")
    try:
        with av.open(str(path), format="mov") as source:
            if not source.streams.video:
                return None
            video = source.streams.video[0]
            with av.open(
                str(temporary),
                mode="w",
                format="mp4",
                options={"movflags": "+faststart"},
            ) as target:
                output = target.add_stream_from_template(video)
                for packet in source.demux(video):
                    if packet.dts is None:
                        continue
                    packet.stream = output
                    target.mux(packet)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, out)
        return out
    except (av.FFmpegError, OSError, ValueError):
        return None
    finally:
        temporary.unlink(missing_ok=True)
