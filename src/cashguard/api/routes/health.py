import shutil
import time

import psutil
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


def readiness(request):
    settings = request.app.state.settings
    workers = request.app.state.repo.workers()
    now = time.time()

    def alive(role):
        return any(
            w["role"] in {role, "all"}
            and w["state"] in {"idle", "busy"}
            and now - w["alive_at"] < 10
            and psutil.pid_exists(w["pid"])
            for w in workers
        )

    checks = {
        "worker_perception": alive("perception"),
        "worker_vlm": not settings.vlm_enabled or alive("vlm"),
        # В stub моделей нет — так и сообщаем; готовность stub от моделей не зависит.
        "models_loaded": False,
        "warmup_done": settings.pipeline_mode == "stub",
        "free_mem_ok": psutil.virtual_memory().available / 1_000_000_000
        >= settings.min_free_mem_gb,
        "disk_ok": shutil.disk_usage(settings.var_dir).free / 1_000_000_000 >= 5,
        "queue_ok": request.app.state.repo.active_count(source="ui") < settings.ui_rate_queue,
    }
    required = {k: v for k, v in checks.items() if k != "models_loaded"}
    if settings.pipeline_mode != "stub":
        required = checks
    return {
        "ready": all(required.values()),
        "checks": checks,
        "pipeline_mode": settings.pipeline_mode,
        "vlm_quant": None,
        "version": settings.pipeline_version,
    }


@router.get("/ready")
async def ready(request: Request):
    report = await run_in_threadpool(readiness, request)
    return JSONResponse(
        report,
        status_code=200 if report["ready"] else 503,
        headers={"Cache-Control": "no-store"},
    )
