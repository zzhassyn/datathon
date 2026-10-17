"""CPU-only sampled detection in a fresh ML interpreter; never issues a violation verdict."""

import argparse
import hashlib
import json
import multiprocessing
import time
from collections import Counter
from fractions import Fraction
from pathlib import Path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(source: Path, output: Path, weights: Path) -> dict:
    import av
    import numpy as np
    import torch  # pyright: ignore[reportMissingImports]  # Separate ML interpreter.
    from PIL import ImageDraw  # pyright: ignore[reportMissingImports]
    from ultralytics import YOLO, settings  # pyright: ignore[reportMissingImports]

    settings.update({"sync": False})
    torch.set_num_threads(1)
    started = time.perf_counter()
    model = YOLO(str(weights))
    frames = []
    counts: Counter[str] = Counter()
    next_sample = 0.0
    first_pts = None
    with (
        av.open(str(source), format="mov", options={"protocol_whitelist": "file"}) as video,
        av.open(
            str(output / "annotated.part"), "w", format="mp4", options={"movflags": "+faststart"}
        ) as annotated,
    ):
        incoming = video.streams.video[0]
        incoming.thread_type = "NONE"
        duration = float(video.duration or 0) / av.time_base
        stream = None
        for frame in video.decode(incoming):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("decoded frame has no PTS")
            pts_sec = float(frame.pts * frame.time_base)
            first_pts = pts_sec if first_pts is None else first_pts
            timestamp = pts_sec - first_pts
            if timestamp + 1e-6 < next_sample:
                continue
            if timestamp > 180 or time.perf_counter() - started > 165:
                raise TimeoutError("object-check budget exceeded")
            next_sample = (int(timestamp * 2) + 1) / 2
            image = frame.to_image()
            rotation = int(getattr(frame, "rotation", 0))
            if rotation % 360:
                image = image.rotate(rotation, expand=True)
            image.thumbnail((1280, 1280))
            prediction = model.predict(
                np.asarray(image)[:, :, ::-1].copy(),
                device="cpu",
                imgsz=640,
                conf=0.25,
                verbose=False,
                save=False,
            )[0]
            detections = []
            draw = ImageDraw.Draw(image)
            for box in prediction.boxes:
                label = model.names[int(box.cls[0])]
                confidence = float(box.conf[0])
                xyxy = [round(float(v), 2) for v in box.xyxy[0].tolist()]
                detections.append({"class_name": label, "confidence": confidence, "xyxy": xyxy})
                counts[label] += 1
                draw.rectangle(xyxy, outline="#ff408a", width=3)
                draw.text(
                    (xyxy[0] + 3, xyxy[1] + 3),
                    f"{label} {confidence:.2f}",
                    fill="white",
                    stroke_width=1,
                    stroke_fill="black",
                )
            draw.text(
                (8, 8),
                f"{timestamp:.2f}s | objects only | 2 fps",
                fill="white",
                stroke_width=1,
                stroke_fill="black",
            )
            name = f"frame-{len(frames):04d}.jpg"
            image.save(output / name, quality=88)
            frames.append(
                {"name": name, "timestamp_sec": round(timestamp, 6), "detections": detections}
            )
            if stream is None:
                stream = annotated.add_stream("libx264", rate=2)
                stream.width, stream.height = image.width // 2 * 2, image.height // 2 * 2
                stream.pix_fmt = "yuv420p"
                stream.time_base = Fraction(1, 90000)
                stream.options = {"preset": "ultrafast", "crf": "23", "threads": "1", "bf": "0"}
            rendered = av.VideoFrame.from_image(image.resize((stream.width, stream.height)))
            rendered.pts = round(timestamp * 90000)
            rendered.time_base = Fraction(1, 90000)
            for packet in stream.encode(rendered):
                annotated.mux(packet)
        if stream is None:
            raise ValueError("no decoded frames")
        for packet in stream.encode():
            annotated.mux(packet)
    (output / "annotated.part").replace(output / "annotated.mp4")
    report = {
        "model_name": "aux_common_objects_s / YOLO26s",
        "weights_sha256": sha256(weights),
        "input_sha256": sha256(source),
        "device": "cpu",
        "sample_fps": 2,
        "confidence_threshold": 0.25,
        "duration_sec": duration,
        "sampled_frames": len(frames),
        "counts": dict(counts),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "assessment": "objects_only",
        "frames": frames,
    }
    temporary = output / "report.part"
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output / "report.json")
    return report


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("weights", type=Path)
    args = parser.parse_args()
    run(args.source, args.output, args.weights)
