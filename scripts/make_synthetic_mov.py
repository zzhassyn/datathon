"""Create explicitly labelled synthetic test material; no dataset is accessed."""

import argparse
import math
import os
import tempfile
from fractions import Fraction
from pathlib import Path


def make_synthetic_mov(
    path: Path | str, *, verdict: str = "clean", duration: float = 2.0, fps: int = 5
) -> Path:
    import av

    if verdict not in {"clean", "violation"}:
        raise ValueError("verdict must be clean or violation")
    if not math.isfinite(duration) or not 0 < duration <= 3600 or not 1 <= fps <= 60:
        raise ValueError("duration must be (0, 3600] and fps must be [1, 60]")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".synthetic-", suffix=".part", dir=path.parent)
    os.close(fd)
    try:
        with av.open(temporary, "w", format="mov") as container:
            container.metadata["comment"] = f"cg-stub:{verdict}"
            stream = container.add_stream("libx264", rate=fps)
            stream.width, stream.height, stream.pix_fmt = 160, 96, "yuv420p"
            stream.options = {"preset": "ultrafast", "crf": "28", "threads": "1"}
            for index in range(math.ceil(duration * fps)):
                frame = av.VideoFrame(160, 96, "rgb24")
                color = bytes((190, 45, 30) if verdict == "violation" else (20, 135, 80))
                row = color * 160
                # Fill stride padding too: never expose uninitialised frame memory.
                padded = row + bytes(frame.planes[0].line_size - len(row))
                frame.planes[0].update(padded * 96)
                frame.pts, frame.time_base = index, Fraction(1, fps)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        with open(temporary, "r+b") as handle:  # Windows: fsync нужен доступ на запись
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Создать синтетический MOV для stub API")
    parser.add_argument("output", type=Path)
    parser.add_argument("--verdict", choices=["clean", "violation"], default="clean")
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--fps", type=int, default=5)
    args = parser.parse_args()
    path = make_synthetic_mov(
        args.output, verdict=args.verdict, duration=args.duration, fps=args.fps
    )
    print(f"Создан синтетический сценарий {args.verdict}: {path}")
