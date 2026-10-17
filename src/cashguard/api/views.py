"""Публичные представления. Ответ UI проверяется контрактом UiJobView перед отправкой."""

import json
import logging
import re
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse

from cashguard.contracts.ui import UiJobView
from cashguard.core.atomic import atomic_write_json
from cashguard.pipeline.render.jury import validate_jury

log = logging.getLogger(__name__)


def lookup(request: Request, job_id: str, *, ui: bool = False):
    if re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
        raise HTTPException(404, "job not found")
    repo = request.app.state.repo
    original = repo.get_job(job_id)
    if original is None or (ui and original.source != "ui"):
        raise HTTPException(404, "job not found")
    repo.finalize_overdue()
    resolved = repo.resolve_job(job_id)
    if resolved is None:
        raise HTTPException(404, "job not found")
    return original, resolved


def result_path(request: Request, job, name: str) -> Path:
    value = job.jury_path if name == "jury" else job.ui_result_path
    if not value:
        raise ValueError("missing result")
    path = Path(value).resolve()
    root = request.app.state.settings.paths.job_dir(job.id)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("invalid result location")
    return path


def jury_file(request: Request, original, resolved) -> Path:
    source = result_path(request, resolved, "jury")
    stat = source.stat()
    validate_file(str(source), stat.st_mtime_ns, stat.st_size, resolved.id, resolved.duration_sec)
    if original.id == resolved.id:
        return source
    # Linked jobs retain a distinct public identity. Materialize once, after done.
    destination = request.app.state.settings.paths.job_dir(original.id) / "jury.json"
    if not destination.exists():
        document = json.loads(source.read_bytes())
        document["job_id"] = original.id
        validate_jury(document, duration_sec=resolved.duration_sec)
        atomic_write_json(destination, document)
    stat = destination.stat()
    validate_file(
        str(destination),
        stat.st_mtime_ns,
        stat.st_size,
        original.id,
        resolved.duration_sec,
    )
    return destination


@lru_cache(maxsize=128)
def validate_file(path: str, mtime_ns: int, size: int, job_id: str, duration: float):
    document = json.loads(Path(path).read_bytes())
    validate_jury(document, duration_sec=duration)
    if document["job_id"] != job_id or document["status"] != "done":
        raise ValueError("incorrect saved result identity/status")


def validate_ui_fragment(fragment):
    if not isinstance(fragment, dict) or not isinstance(fragment.get("violations"), list):
        raise TypeError("invalid ui fragment")
    if fragment.get("verdict") not in {"violation", "no_violation"}:
        raise ValueError("invalid ui verdict")
    if (fragment["verdict"] == "violation") != bool(fragment["violations"]):
        raise ValueError("invalid ui verdict")
    for violation in fragment["violations"]:
        if not isinstance(violation, dict) or not isinstance(violation.get("evidence"), list):
            raise TypeError("invalid ui evidence")
        for frame in violation["evidence"]:
            if not isinstance(frame, dict) or not isinstance(frame.get("url"), str):
                raise TypeError("invalid ui evidence url")


async def jury_response(request: Request, job_id: str):
    original, job = await run_in_threadpool(lookup, request, job_id)
    if job.status == "done":
        try:
            path = await run_in_threadpool(jury_file, request, original, job)
            return FileResponse(
                path,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        except (OSError, ValueError, TypeError):
            # Never turn lost/corrupt results into a clean verdict.
            status = "failed"
    else:
        status = job.public_status
    return JSONResponse(
        {"job_id": job_id, "status": status, "verdict": None, "violations": []},
        headers={"Cache-Control": "no-store"},
    )


STUB_NOTE_RU = "Демонстрационный режим: AI-модели не подключены, результат синтетический."

DEGRADATIONS_RU = {
    "vlm_deadline": ("vlm", "Проверка Qwen пропущена: не хватило времени до срока ответа."),
    "vlm_memory": ("vlm", "Проверка Qwen пропущена: не хватило свободной памяти."),
    "vlm_crash": ("vlm", "Проверка Qwen прервалась из-за сбоя; результат построен по правилам."),
    "vlm_backlog": ("vlm", "Проверка Qwen пропущена из-за очереди заданий."),
    "vlm_disabled": ("vlm", "Проверка Qwen отключена в этом режиме."),
    "perception_fast": ("perception", "Ускоренный режим распознавания."),
    "perception_min": ("perception", "Минимальный режим распознавания."),
    "deadline_finalize": ("final", "Результат зафиксирован по сроку: часть проверок не завершена."),
}


def degradation_ru(reason: str) -> dict:
    stage, text = DEGRADATIONS_RU.get(reason, ("pipeline", f"Неполная обработка ({reason})."))
    return {"stage": stage, "reason_ru": text}


FAILURES_RU = {
    "UNSUPPORTED_VIDEO": "Не удалось прочитать видео: файл повреждён или формат не поддерживается.",
    "PIPELINE_CRASH": "Обработка прервалась из-за сбоя. Попробуйте загрузить видео ещё раз.",
}


def failure_ru(job, *, corrupt: bool) -> dict:
    if corrupt:
        return {"code": "INTERNAL", "message_ru": "Сохранённый результат не читается."}
    code = job.error_code or "INTERNAL"
    text = job.error_msg or FAILURES_RU.get(code, "Обработка завершилась ошибкой.")
    return {"code": code, "message_ru": text}


def ui_view(request: Request, job_id: str) -> dict:
    original, job = lookup(request, job_id, ui=True)
    fragment = {"verdict": None, "violations": [], "signals": []}
    corrupt = False
    if job.ui_result_path and (job.status == "done" or job.provisional_at):
        try:
            saved = json.loads(result_path(request, job, "ui").read_bytes())
            validate_ui_fragment(saved)
            fragment.update(saved)
        except (OSError, ValueError, TypeError):
            corrupt = True
    for violation in fragment.get("violations", []):
        for frame in violation.get("evidence", []):
            frame["url"] = frame["url"].replace(f"/jobs/{job.id}/", f"/jobs/{job_id}/")
    metadata = original.metadata
    preview = request.app.state.settings.paths.job_dir(job_id) / "preview.mp4"
    stages = json.loads(job.stages_json)
    if not stages:
        stages = [
            {
                "code": "queued",
                "status": "running" if job.status == "queued" else "done",
                "progress": 0.0 if job.status == "queued" else 1.0,
                "elapsed_sec": None,
                "note_ru": None,
            }
        ]
    mode = request.app.state.settings.pipeline_mode
    status = "failed" if corrupt else job.status
    view = {
        "job_id": job_id,
        "status": status,
        "pipeline_mode": mode,
        "created_at": datetime.fromtimestamp(original.received_at, UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "elapsed_sec": round(max(0, (job.finished_at or time.time()) - original.received_at), 2),
        "queue_position": request.app.state.repo.queue_position(job_id),
        "eta_sec": None,
        "perception_level": job.perception_level,
        "result_level": job.result_level,
        "stages": stages,
        "degradations": (
            [{"stage": "perception", "reason_ru": STUB_NOTE_RU}] if mode == "stub" else []
        )
        + [degradation_ru(str(reason)) for reason in job.degradations],
        "provisional": bool(job.provisional_at and job.status not in {"done", "failed"}),
        "video": {
            "url": f"/api/ui/jobs/{job_id}/video.mp4",
            "duration_sec": original.duration_sec,
            "width": metadata.get("width", 0),
            "height": metadata.get("height", 0),
        }
        if preview.is_file()
        else None,
        "verdict": fragment.get("verdict"),
        "violations": fragment.get("violations", []),
        "signals": fragment.get("signals", []),
        "timings": json.loads(job.timings_json) or None,
        "error": failure_ru(job, corrupt=corrupt) if status == "failed" else None,
    }
    if status != "done":
        view["verdict"] = None
        view["violations"] = []
    try:
        return UiJobView.model_validate(view).model_dump(mode="json")
    except ValidationError:
        # Ответ не соответствует контракту UI: не показываем его как успех.
        log.exception("ui view %s не прошёл контракт UiJobView", job_id)
        view.update(
            status="failed",
            verdict=None,
            violations=[],
            signals=[],
            error={"code": "INTERNAL", "message_ru": "Результат не прошёл проверку формата."},
        )
        return UiJobView.model_validate(view).model_dump(mode="json")
