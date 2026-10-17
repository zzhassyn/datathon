"""Opt-in, loopback-only object detection. Separate from frozen jury/UI job contracts."""

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path

import psutil
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse

from cashguard.api.ratelimit import client_ip
from cashguard.api.routes.ui import require_ui_session
from cashguard.api.upload import receive_video
from cashguard.pipeline.ingest.probe import UnsupportedVideo, probe


def require_local(request: Request):
    settings = request.app.state.settings
    if not settings.local_model_python or not settings.local_model_weights:
        raise HTTPException(404, "Проверка модели не включена.")
    if (
        request.client is None
        or request.client.host not in {"127.0.0.1", "::1"}
        or any(h in request.headers for h in ("cf-connecting-ip", "x-forwarded-for"))
        or request.headers.get("origin", str(request.base_url).rstrip("/"))
        != str(request.base_url).rstrip("/")
    ):
        raise HTTPException(403, "Проверка доступна только с этого компьютера.")


router = APIRouter(
    prefix="/api/local-model",
    dependencies=[Depends(require_local), Depends(require_ui_session)],
    include_in_schema=False,
)


def directory(request: Request, check_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", check_id):
        raise HTTPException(404, "Проверка не найдена.")
    root = request.app.state.settings.var_dir / "model-checks"
    path = root / check_id
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise HTTPException(404, "Проверка не найдена.")
    return path


def read_report(request: Request, check_id: str) -> dict:
    path = directory(request, check_id) / "report.json"
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Проверка не найдена.")
    report = json.loads(path.read_text())
    report["check_id"] = check_id
    report["video_url"] = f"/api/local-model/{check_id}/annotated.mp4"
    for frame in report["frames"]:
        frame["image_url"] = f"/api/local-model/{check_id}/{frame['name']}"
    return report


@router.post("/check")
async def check(request: Request):
    settings = request.app.state.settings
    # Admission only: no in-memory job queue. The request owns its finite child process.
    lock = request.app.state.local_model_lock
    if lock.locked():
        raise HTTPException(429, "Другая проверка модели ещё выполняется.")
    async with lock, request.app.state.ui_limiter.admit(client_ip(request)):
        if psutil.virtual_memory().available < 1_000_000_000:
            raise HTTPException(503, "Для проверки модели недостаточно свободной памяти.")
        python, weights = settings.local_model_python, settings.local_model_weights
        if not python or not weights or not python.is_file() or not weights.is_file():
            raise HTTPException(503, "Локальные файлы модели недоступны.")
        check_id = uuid.uuid4().hex
        root = directory(request, check_id)
        root.parent.mkdir(parents=True, exist_ok=True)
        process = None
        source = None
        try:
            source, _ = await receive_video(request, root, settings, time.time())
            meta = await run_in_threadpool(probe, source, deep=True)
            if meta.duration_sec > 180 or meta.width * meta.height > 4096 * 2160:
                raise HTTPException(422, "Проверка модели принимает видео до трёх минут.")
            env = os.environ.copy()
            for key in ("CG_API_KEYS", "ULTRALYTICS_API_KEY", "HF_TOKEN"):
                env.pop(key, None)
            env.update(
                PYTHONPATH=str(Path(__file__).resolve().parents[3]),
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                HF_HUB_DISABLE_TELEMETRY="1",
                YOLO_OFFLINE="1",
                YOLO_AUTOINSTALL="false",
                DO_NOT_TRACK="1",
                OMP_NUM_THREADS="1",
                YOLO_CONFIG_DIR=str(root / "config"),
            )
            with (root / "worker.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(
                    str(python),
                    "-m",
                    "cashguard.pipeline.perception.object_check",
                    str(source),
                    str(root),
                    str(weights),
                    stdout=log,
                    stderr=log,
                    env=env,
                    start_new_session=True,
                )
                await asyncio.wait_for(process.wait(), timeout=180)
            if process.returncode != 0:
                raise HTTPException(500, "Не удалось выполнить проверку модели.")
            return JSONResponse(
                read_report(request, check_id), headers={"Cache-Control": "no-store"}
            )
        except UnsupportedVideo:
            raise HTTPException(415, "Не удалось прочитать видео MOV/MP4.") from None
        except TimeoutError:
            raise HTTPException(504, "Проверка модели превысила три минуты.") from None
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            if source is not None:
                source.unlink(missing_ok=True)


@router.get("/{check_id}")
async def result(check_id: str, request: Request):
    return JSONResponse(read_report(request, check_id), headers={"Cache-Control": "no-store"})


@router.get("/{check_id}/{name}")
async def asset(check_id: str, name: str, request: Request):
    if name != "annotated.mp4" and not re.fullmatch(r"frame-\d{4}\.jpg", name):
        raise HTTPException(404, "Файл не найден.")
    path = directory(request, check_id) / name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Файл не найден.")
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})
