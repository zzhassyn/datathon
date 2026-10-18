"""Human-only local camera calibration; images never enter an external service."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import av
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from cashguard.api.routes.ui import require_ui_session
from cashguard.core.atomic import atomic_write_bytes, atomic_write_json


def require_local(request: Request):
    if not request.app.state.settings.local_analysis_config:
        raise HTTPException(404, "Локальная калибровка не включена.")
    if (
        request.url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1"}
        or any(key in request.headers for key in ("x-forwarded-for", "cf-connecting-ip"))
    ):
        raise HTTPException(403, "Калибровка доступна только на этом компьютере.")
    if request.method == "POST" and request.headers.get("origin") != str(request.base_url).rstrip(
        "/"
    ):
        raise HTTPException(403, "Требуется запрос с локальной страницы калибровки.")


router = APIRouter(
    dependencies=[Depends(require_local), Depends(require_ui_session)], include_in_schema=False
)


def snapshot(settings):
    config = json.loads(settings.local_analysis_config.read_text(encoding="utf-8"))
    source = Path(config["calibration_source"]).resolve()
    if not source.is_file():
        raise HTTPException(404, "Видео для калибровки не найдено.")
    with source.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    directory = settings.var_dir / "calibration"
    metadata = directory / f"{sha}.json"
    image = directory / f"{sha}.jpg"
    if metadata.is_file() and image.is_file():
        return json.loads(metadata.read_text(encoding="utf-8")), image, config
    with av.open(str(source), format="mov", options={"protocol_whitelist": "file"}) as container:
        video = container.streams.video[0]
        frame = next(container.decode(video), None)
        if frame is None or frame.pts is None or frame.time_base is None:
            raise HTTPException(422, "Первый кадр видео не содержит PTS.")
        rotation = int(getattr(frame, "rotation", 0)) % 360
        if rotation not in (0, 90, 180, 270):
            raise HTTPException(422, "Неподдерживаемый поворот видео.")
        pixels = np.asarray(frame.to_ndarray(format="rgb24"), dtype=np.uint8)
        if rotation:
            pixels = np.ascontiguousarray(np.rot90(pixels, rotation // 90), dtype=np.uint8)
        height, width = pixels.shape[:2]
        encoder = av.CodecContext.create("mjpeg", "w")
        encoder.width, encoder.height, encoder.pix_fmt = width, height, "yuvj420p"
        jpeg = b"".join(
            bytes(packet)
            for packet in encoder.encode(av.VideoFrame.from_ndarray(pixels, format="rgb24"))
        )
        jpeg += b"".join(bytes(packet) for packet in encoder.encode(None))
        atomic_write_bytes(image, jpeg)
        meta = dict(
            video_id=source.name,
            video_sha256=sha,
            width=width,
            height=height,
            rotation_deg=rotation,
            frame_pts=frame.pts,
            time_base=str(frame.time_base),
            timestamp_sec=0,
            frame_url="/api/local-calibration/frame.jpg",
        )
        atomic_write_json(metadata, meta)
    return meta, image, config


def validate_confirmation(payload, meta):
    for field in ("video_sha256", "width", "height", "rotation_deg", "frame_pts"):
        if payload.get(field) != meta[field]:
            raise HTTPException(422, "Кадр или видео изменились; обновите страницу.")
    if not all(
        payload.get(key) is True
        for key in ("confirmed", "cash_area_confirmed", "staff_only_surface")
    ):
        raise HTTPException(422, "Подтвердите помещение и границы поверхности кассира.")
    roi = payload.get("desk_roi")
    if (
        not isinstance(roi, list)
        or len(roi) != 4
        or any(type(value) is not int for value in roi)
        or not 0 <= roi[0] < roi[2] <= meta["width"]
        or not 0 <= roi[1] < roi[3] <= meta["height"]
    ):
        raise HTTPException(422, "Прямоугольник должен находиться внутри кадра.")
    return {
        **meta,
        "desk_roi": roi,
        "confirmed": True,
        "cash_area_confirmed": True,
        "staff_only_surface": True,
        "staff_ownership_confirmed": payload.get("staff_ownership_confirmed") is True,
        "confirmed_at": datetime.now(UTC).isoformat(),
        "scope": "this_video_only",
    }


@router.get("/api/local-calibration/meta")
async def metadata(request: Request):
    meta, _, _ = await run_in_threadpool(snapshot, request.app.state.settings)
    return JSONResponse(meta, headers={"Cache-Control": "private, no-store"})


@router.get("/api/local-calibration/frame.jpg")
async def frame(request: Request):
    _, image, _ = await run_in_threadpool(snapshot, request.app.state.settings)
    return FileResponse(
        image, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"}
    )


@router.post("/api/local-calibration/confirm")
async def confirm(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(413, "Слишком большой запрос.")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(422, "Некорректные координаты.") from None
    if not isinstance(payload, dict):
        raise HTTPException(422, "Некорректная калибровка.")
    meta, _, config = await run_in_threadpool(snapshot, request.app.state.settings)
    document = validate_confirmation(payload, meta)
    await run_in_threadpool(atomic_write_json, Path(config["calibration_file"]), document)
    return JSONResponse(document, headers={"Cache-Control": "private, no-store"})


@router.get("/local-calibration")
async def page():
    source = Path(__file__).with_name("local_calibration.html")
    return HTMLResponse(
        source.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": (
                "default-src 'none'; img-src 'self'; connect-src 'self'; "
                "script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
        },
    )
