import hashlib
import hmac
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, Response

from cashguard.api.ingest_service import ingest_upload
from cashguard.api.ratelimit import client_ip
from cashguard.api.views import lookup, result_path, ui_view


def session_token(request):
    secret = request.app.state.settings.ui_access_code.get_secret_value()
    return hmac.new(
        secret.encode(), b"cashguard-ui-session-v1", hashlib.sha256
    ).hexdigest()


async def require_ui_session(request: Request):
    if (
        request.app.state.settings.ui_access_code.get_secret_value()
        and not hmac.compare_digest(
            request.cookies.get("cg_ui", ""), session_token(request)
        )
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
    if not isinstance(code, str) or not hmac.compare_digest(
        code.encode(), secret.encode()
    ):
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
            "Cache-Control": "private, max-age=60"
            if view["status"] == "done"
            else "no-store"
        },
    )


@router.get("/jobs/{job_id}/video.mp4")
async def video(job_id: str, request: Request):
    await run_in_threadpool(lookup, request, job_id, ui=True)
    root = request.app.state.settings.paths.job_dir(job_id)
    path = (root / "preview.mp4").resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "preview not ready")
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
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/catalog")
async def catalog(request: Request):
    # S0 catalogue is absent. Empty is truthful; never invent legal references.
    version = request.app.state.settings.config_hash
    etag = f'"{version}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(
        {"config_hash": version, "types": [], "signals": [], "acts": []},
        headers=headers,
    )
