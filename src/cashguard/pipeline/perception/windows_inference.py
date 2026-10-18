"""Local PT inference sidecar. Numeric observations are NOT a PerceptionTrace or verdict.

The frozen trace has no hand-box/640 input contract; no schema is misrepresented.
Run only in the explicit ML interpreter, separate from the API and queue process.
"""
# pyright: reportMissingImports=false
# ML dependencies intentionally live in the separate, explicitly selected runtime.

import argparse
import hashlib
import json
import math
import os
import socket
import time
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any

MAIN_CLASSES = [
    "person",
    "cash",
    "collection_bag",
    "safe_closed",
    "safe_open",
    "laptop",
    "door",
    "counting_machine",
    "auth_detector",
    "id_document",
    "phone",
    "cup",
    "personal_bag",
    "outerwear",
]
CLASSES = {"main": MAIN_CLASSES, "hands": ["hand"], "door": ["closed", "open"]}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def inspect_weights(entry):
    path = Path(entry["path"]).resolve()
    if not path.is_file():
        raise FileNotFoundError("Trained weights missing")
    if path.suffix != ".pt" or sha256(path) != entry.get("sha256"):
        raise ValueError("Trained weights SHA-256 mismatch")
    return path


def checked_roi(roi, width, height):
    if roi is None:
        return None
    if (
        not isinstance(roi, list)
        or len(roi) != 4
        or any(type(v) is not int for v in roi)
        or not 0 <= roi[0] < roi[2] <= width
        or not 0 <= roi[1] < roi[3] <= height
    ):
        raise ValueError("Invalid camera-specific ROI")
    if roi == [0, 0, width, height]:
        raise ValueError("A full video frame is not a door crop")
    return roi


def camera_door_roi(calibration, video):
    if (
        calibration.get("confirmed") is not True
        or calibration.get("video_sha256") != video["sha256"]
        or any(calibration.get(key) != video[key] for key in ("width", "height", "rotation_deg"))
    ):
        return None
    return checked_roi(calibration.get("roi"), video["width"], video["height"])


def pts_seconds(pts, time_base, origin):
    if pts is None:
        raise ValueError("Frame has no PTS")
    value = float(pts * Fraction(time_base)) - origin
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid PTS")
    return value


def deny_network():
    def denied(*args, **kwargs):
        raise OSError("Network disabled in local inference process")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.getaddrinfo = denied
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        YOLO_OFFLINE="1",
        YOLO_AUTOINSTALL="false",
        DO_NOT_TRACK="1",
    )


@contextmanager
def gpu_lock(path, enabled):
    if not enabled:
        yield
        return
    import msvcrt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if path.stat().st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def disable_integrations():
    import ultralytics.utils.callbacks.base as base_callbacks
    from ultralytics import settings
    from ultralytics.utils import callbacks

    settings.update(
        {
            key: False
            for key in (
                "sync",
                "clearml",
                "comet",
                "dvc",
                "hub",
                "mlflow",
                "neptune",
                "raytune",
                "tensorboard",
                "wandb",
            )
            if key in settings.defaults
        }
    )
    callbacks.add_integration_callbacks = lambda instance: None
    base_callbacks.add_integration_callbacks = lambda instance: None


def load_models(request, device):
    from ultralytics import YOLO

    disable_integrations()
    models, metadata = {}, {}
    for role in ("main", "hands", "door"):
        path = inspect_weights(request["weights"][role])
        model: Any = YOLO(str(path), task="classify" if role == "door" else "detect")
        names = [model.names[i] for i in range(len(model.names))]
        if names != CLASSES[role]:
            raise ValueError("Wrong trained model class order: " + role)
        model.model.float().eval().to(device)
        if role != "door":
            model.model.end2end = False
        # No integrations, callbacks, uploads, downloads, or implicit autoinstall.
        for callback_list in model.callbacks.values():
            callback_list.clear()
        models[role] = model
        metadata[role] = dict(
            name=request["weights"][role].get("name", role),
            sha256=request["weights"][role]["sha256"],
            classes=names,
            device=device,
            task=model.task,
            imgsz=224 if role == "door" else 640,
        )
    return models, metadata


def make_tracker(sample_fps):
    """A fresh, explicit BoT-SORT instance per video; no appearance/face embeddings."""
    from types import SimpleNamespace

    import yaml
    from ultralytics.trackers.bot_sort import BOTSORT

    path = Path(__file__).resolve().parents[4] / "configs/trackers/botsort_static.yaml"
    params = yaml.safe_load(path.read_text(encoding="utf-8"))
    params["track_buffer"] = max(1, math.ceil(2 * sample_fps))
    if params.get("with_reid") is not False or params.get("gmc_method") != "none":
        raise ValueError("Only static-camera tracking without ReID is supported")
    return SimpleNamespace(
        backend=BOTSORT(SimpleNamespace(**params)), classes={}, invalid_ids=set()
    )


def boxes(model, image, device, *, tracker=None):
    result = model.predict(
        image,
        imgsz=640,
        rect=False,
        device=device,
        conf=0.25,
        iou=0.7,
        max_det=300,
        verbose=False,
        save=False,
        nms=None,
        quantize=None,
        end2end=False,
    )[0]
    identities = {}
    if tracker is not None:
        tracks = tracker.backend.update(result.boxes.cpu().numpy(), image)
        for track in tracks:
            identity, class_id = int(track[4]), int(track[6])
            if tracker.classes.setdefault(identity, class_id) != class_id:
                tracker.invalid_ids.add(identity)
            if identity not in tracker.invalid_ids:
                identities[int(track[-1])] = identity
    output = []
    for index, box in enumerate(result.boxes):
        coordinates = [float(v) for v in box.xyxyn[0].tolist()]
        confidence = float(box.conf[0])
        if not all(math.isfinite(v) for v in [confidence, *coordinates]):
            raise ValueError("Non-finite detection")
        class_id = int(box.cls[0])
        output.append(
            dict(
                class_id=class_id,
                class_name=model.names[class_id],
                conf=confidence,
                box=[max(0.0, min(1.0, v)) for v in coordinates],
            )
        )
        if tracker is not None:
            output[-1]["track_id"] = identities.get(index)
    return output


def run(request, *, cached=None, progress_fn=None):
    deny_network()
    import av
    import numpy as np
    import torch
    from PIL import Image

    started = time.perf_counter()
    torch.set_num_threads(1)
    requested = request.get("device", "auto")
    device = "cuda:0" if requested in ("auto", "cuda") and torch.cuda.is_available() else "cpu"
    if requested == "cuda" and device == "cpu":
        raise RuntimeError("Requested CUDA is unavailable")
    output = Path(request["out_dir"])
    output.mkdir(parents=True, exist_ok=True)

    def progress(fraction, note):
        atomic_json(
            output / "progress.json",
            dict(stage="perception", fraction=fraction, note_ru=note, updated_at=time.time()),
        )
        if progress_fn is not None:
            progress_fn("perception", fraction, note)

    with gpu_lock(request.get("gpu_lock_path", output.parent / "cuda.lock"), device != "cpu"):
        progress(0, "Проверка обученных моделей и контрольных сумм")
        models, metadata = load_models(request, device) if cached is None else cached
        if any(
            metadata[role]["sha256"] != request["weights"][role]["sha256"]
            for role in ("main", "hands", "door")
        ):
            raise ValueError("Loaded model differs from requested trained weights")
        atomic_json(output / "models.json", metadata)
        if request.get("preflight_only"):
            atomic_json(
                output / "ready.json",
                dict(models=metadata, device=device, elapsed_sec=time.perf_counter() - started),
            )
            return
        source = Path(request["input_path"])
        input_hash = sha256(source)
        sample_fps = float(request.get("sample_fps", 2))
        if not 0 < sample_fps <= 10:
            raise ValueError("Invalid sampling frequency")
        tracker = make_tracker(sample_fps) if request.get("tracking_enabled") is True else None
        frames, decoded, previous, next_sample = [], 0, -1.0, 0.0
        is_vfr = False
        (output / "frames").mkdir(exist_ok=True)
        with av.open(
            str(source), format="mov", options={"protocol_whitelist": "file"}
        ) as container:
            stream = container.streams.video[0]
            stream.thread_type = "NONE"
            duration = (
                float(stream.duration * stream.time_base)
                if stream.duration is not None
                else float(container.duration or 0) / av.time_base
            )
            fps = float(stream.average_rate or stream.guessed_rate or 0)
            if not math.isfinite(duration) or duration <= 0 or fps <= 0:
                raise ValueError("Invalid video duration/rate")
            origin, meta = None, None
            for frame in container.decode(stream):
                if time.time() >= request["deadline_at"]:
                    raise TimeoutError("Incomplete inference: deadline")
                if frame.pts is None or frame.time_base is None:
                    raise ValueError("Decoded frame has no PTS")
                origin = float(frame.pts * frame.time_base) if origin is None else origin
                timestamp = pts_seconds(frame.pts, frame.time_base, origin)
                if timestamp <= previous:
                    raise ValueError("Non-monotonic decoded PTS")
                if previous >= 0 and abs(timestamp - previous - 1 / fps) > max(0.002, 0.1 / fps):
                    is_vfr = True
                previous, decoded = timestamp, decoded + 1
                if timestamp + 1e-6 < next_sample:
                    continue
                next_sample = (math.floor(timestamp * sample_fps) + 1) / sample_fps
                rotation = int(getattr(frame, "rotation", 0)) % 360
                if rotation not in (0, 90, 180, 270):
                    raise ValueError("Unsupported rotation")
                image = frame.to_image().convert("RGB")
                if rotation:
                    image = image.rotate(rotation, expand=True)
                if meta is None:
                    meta = dict(
                        duration_sec=duration,
                        width=image.width,
                        height=image.height,
                        rotation_deg=rotation,
                        fps_avg=fps,
                        is_vfr=False,
                        codec=stream.codec_context.name,
                        pix_fmt=stream.codec_context.pix_fmt,
                        start_pts_sec=origin,
                        time_base=str(frame.time_base),
                        sha256=input_hash,
                    )
                if (image.width, image.height, rotation) != (
                    meta["width"],
                    meta["height"],
                    meta["rotation_deg"],
                ):
                    raise ValueError("Frame geometry changed during inference")
                door_roi = camera_door_roi(request.get("door_calibration", {}), meta)
                detections = boxes(models["main"], image, device, tracker=tracker)
                hands = boxes(models["hands"], image, device)
                door = None
                if door_roi is not None:
                    # PIL bilinear resize always antialiases; no shortest-edge/center crop.
                    crop = image.crop(tuple(door_roi)).resize((224, 224), Image.Resampling.BILINEAR)
                    pixels = np.asarray(crop, dtype=np.uint8).copy()
                    tensor = torch.from_numpy(pixels).permute(2, 0, 1).unsqueeze(0)
                    tensor = tensor.to(device=device, dtype=torch.float32) / 255
                    with torch.inference_mode():
                        probabilities = models["door"].model(tensor)
                    if isinstance(probabilities, tuple):
                        probabilities = probabilities[0]
                    values = probabilities[0].detach().float().cpu().tolist()
                    if (
                        len(values) != 2
                        or abs(sum(values) - 1) > 0.001
                        or not all(math.isfinite(v) and 0 <= v <= 1 for v in values)
                    ):
                        raise ValueError("Invalid classifier probabilities")
                    label = max(range(2), key=lambda index: values[index])
                    door = dict(
                        label=CLASSES["door"][label],
                        conf=values[label],
                        probabilities=values,
                        roi=door_roi,
                    )
                name = f"frame-{len(frames):06d}.jpg"
                image.save(output / "frames" / name, quality=90)
                frames.append(
                    dict(
                        idx=len(frames),
                        t_sec=timestamp,
                        pts=frame.pts,
                        image_name=name,
                        width=image.width,
                        height=image.height,
                        detections=detections,
                        hands=hands,
                        door=door,
                    )
                )
                progress(
                    min(0.99, timestamp / duration),
                    f"Main14 + hands: {len(frames)} кадров, {timestamp:.1f}/{duration:.1f} с; "
                    + (
                        "дверь: известный ROI"
                        if door_roi
                        else "дверь пропущена: ROI не подтверждён"
                    ),
                )
        if (
            not frames
            or duration - previous > max(0.15, 2 / fps)
            or duration - frames[-1]["t_sec"] > 1 / sample_fps + 2 / fps
        ):
            raise ValueError("Incomplete video coverage")
        if meta is None:
            raise ValueError("No video metadata")
        meta["is_vfr"] = is_vfr
        report = dict(
            video=meta,
            models=metadata,
            frames=frames,
            coverage=dict(
                complete=True,
                decoded_frames=decoded,
                sampled_frames=len(frames),
                first_sec=frames[0]["t_sec"],
                last_sec=frames[-1]["t_sec"],
            ),
            sample_fps=sample_fps,
            detections_are_frame_counts=True,
            unique_objects=None,
            door_evaluated=any(frame["door"] is not None for frame in frames),
            timings=dict(perception=time.perf_counter() - started),
        )
        atomic_json(output / "observations.json", report)
        progress(
            1,
            f"Обработано {len(frames)} кадров; устройство {device}. "
            + "; ".join(item["name"] for item in metadata.values())
            + (
                "; дверь: классифицирована по подтверждённому ROI"
                if report["door_evaluated"]
                else "; дверь: ROI не подтверждён, классификация пропущена"
            ),
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    try:
        run(request)
    except Exception as error:
        atomic_json(
            Path(request["out_dir"]) / "error.json",
            dict(
                kind=type(error).__name__,
                message="Локальная модельная обработка не завершена. См. защищённый worker.log.",
            ),
        )
        raise


if __name__ == "__main__":
    main()
