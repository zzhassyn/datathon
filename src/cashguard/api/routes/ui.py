import hashlib
import hmac
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, Response

from cashguard.api.ingest_service import ingest_upload
from cashguard.api.ratelimit import client_ip
from cashguard.api.views import lookup, preview_info, result_path, ui_view, validate_frame


def session_token(request):
    secret = request.app.state.settings.ui_access_code.get_secret_value()
    return hmac.new(secret.encode(), b"cashguard-ui-session-v1", hashlib.sha256).hexdigest()


async def require_ui_session(request: Request):
    if request.app.state.settings.ui_access_code.get_secret_value() and not hmac.compare_digest(
        request.cookies.get("cg_ui", ""), session_token(request)
    ):
        raise HTTPException(401, "UI access code required")


router = APIRouter(prefix="/api/ui", dependencies=[Depends(require_ui_session)])
session_router = APIRouter(prefix="/api/ui")


@session_router.post("/session")
async def session(request: Request):
    secret = request.app.state.settings.ui_access_code.get_secret_value()
    if not secret:
        raise HTTPException(404, "Not Found")
    # Small bounded JSON; authentication is separate from the jury upload path.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1024:
            raise HTTPException(413, "body too large")
    import json

    try:
        value = json.loads(raw)
        code = value.get("code", "")
    except (ValueError, AttributeError):
        raise HTTPException(422, "invalid request") from None
    if not isinstance(code, str) or not hmac.compare_digest(code.encode(), secret.encode()):
        raise HTTPException(401, "invalid access code")
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        "cg_ui",
        session_token(request),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=3600,
    )
    return response


@router.post("/jobs")
async def create_job(request: Request):
    ip = client_ip(request)
    async with request.app.state.ui_limiter.admit(ip):
        return {"job_id": await ingest_upload(request, source="ui", client_ip=ip)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    view = await run_in_threadpool(ui_view, request, job_id)
    return JSONResponse(
        view,
        headers={
            "Cache-Control": "private, max-age=60" if view["status"] == "done" else "no-store"
        },
    )


@router.get("/jobs/{job_id}/video.mp4")
async def video(job_id: str, request: Request):
    await run_in_threadpool(lookup, request, job_id, ui=True)
    root = request.app.state.settings.paths.job_dir(job_id)
    path = (root / "preview.mp4").resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "preview not ready")
    try:
        stat = path.stat()
        await run_in_threadpool(preview_info, str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        raise HTTPException(404, "preview unavailable") from None
    return FileResponse(
        path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/jobs/{job_id}/frames/{name}")
async def frame(job_id: str, name: str, request: Request):
    if re.fullmatch(r"\d{1,2}_(event|zoom|context)\.jpg", name) is None:
        raise HTTPException(404, "frame not found")
    _, job = await run_in_threadpool(lookup, request, job_id, ui=True)
    try:
        root = result_path(request, job, "ui").parent / "evidence"
    except ValueError:
        raise HTTPException(404, "frame not found") from None
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(404, "frame not found")
    try:
        stat = path.stat()
        await run_in_threadpool(validate_frame, str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        raise HTTPException(404, "frame unavailable") from None
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/catalog")
async def catalog(request: Request):
    # Проверенный каталог S0 (configs/catalog, configs/legal): нормы только оттуда.
    body = request.app.state.ui_catalog
    etag = f'"{body["config_hash"]}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(body, headers=headers)
