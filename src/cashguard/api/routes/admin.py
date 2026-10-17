from fastapi import APIRouter, HTTPException, Request, Security
from starlette.concurrency import run_in_threadpool

from cashguard.api.deps import require_api_key

router = APIRouter(prefix="/api/v1/admin")


@router.get("/status")
async def status(request: Request, principal: str = Security(require_api_key)):
    if principal != "team":
        raise HTTPException(403, "forbidden")
    repo = request.app.state.repo
    workers = await run_in_threadpool(repo.workers)
    # Expose diagnostics only to team; no tokens, frames, filenames or payloads.
    return {
        "versions": {"pipeline": request.app.state.settings.pipeline_version},
        "queue": {
            "perception": await run_in_threadpool(repo.queue_depth, stage="perception"),
            "vlm": await run_in_threadpool(repo.queue_depth, stage="vlm"),
        },
        "workers": [
            {k: worker.get(k) for k in ("name", "role", "pid", "state", "alive_at", "progress_at")}
            for worker in workers
        ],
    }
