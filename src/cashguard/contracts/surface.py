"""Декларация HTTP-поверхности для OpenAPI: пути, методы и модели ответов. Логики здесь нет.

Из неё ``cashguard.contracts.export`` собирает ``web/src/shared/api/openapi.json``, чтобы web (S2)
получил типы раньше, чем появится настоящее приложение API (S1). Настоящее приложение обязано
отдавать те же пути и модели; это проверяет контрактный тест S1.
"""

from typing import Any

from fastapi import FastAPI, Header
from fastapi.responses import Response

from cashguard.contracts.health import AdminStatus, LiveReport, ReadyReport
from cashguard.contracts.jury import AnalyzeAccepted, JuryJobResponse
from cashguard.contracts.ui import UiCatalog, UiJobView

API_TITLE = "CashGuard API"
API_VERSION = "1.0.0"

_MULTIPART_FILE: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}

_JPEG: dict[str, Any] = {"content": {"image/jpeg": {}}, "description": "JPEG-кадр"}
_MP4: dict[str, Any] = {
    "content": {"video/mp4": {}},
    "description": "Предпросмотр H.264 (faststart, Range)",
}


def build_contract_app() -> FastAPI:
    """FastAPI-приложение только с сигнатурами маршрутов. Обработчики не вызываются."""
    app = FastAPI(title=API_TITLE, version=API_VERSION, separate_input_output_schemas=False)

    def _stub() -> Any:
        raise NotImplementedError

    @app.post(
        "/api/v1/analyze",
        response_model=AnalyzeAccepted,
        tags=["jury"],
        summary="Загрузить видео (.mov) на анализ",
        openapi_extra=_MULTIPART_FILE,
    )
    def analyze(x_api_key: str = Header(alias="X-API-Key")) -> AnalyzeAccepted:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    @app.get(
        "/api/v1/jobs/{job_id}",
        response_model=JuryJobResponse,
        tags=["jury"],
        summary="Статус и результат анализа",
    )
    def get_job(job_id: str, x_api_key: str = Header(alias="X-API-Key")) -> JuryJobResponse:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    @app.get(
        "/api/v1/admin/status",
        response_model=AdminStatus,
        tags=["admin"],
        summary="Статус сервиса (только ключ команды)",
    )
    def admin_status(x_api_key: str = Header(alias="X-API-Key")) -> AdminStatus:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    @app.post(
        "/api/ui/jobs",
        response_model=AnalyzeAccepted,
        tags=["ui"],
        summary="Загрузить видео из интерфейса",
        openapi_extra=_MULTIPART_FILE,
    )
    def ui_create_job() -> AnalyzeAccepted:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    @app.get("/api/ui/jobs/{job_id}", response_model=UiJobView, tags=["ui"])
    def ui_get_job(job_id: str) -> UiJobView:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    @app.get(
        "/api/ui/jobs/{job_id}/video.mp4",
        tags=["ui"],
        response_class=Response,
        responses={200: _MP4},
    )
    def ui_video(job_id: str) -> None:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    @app.get(
        "/api/ui/jobs/{job_id}/frames/{name}",
        tags=["ui"],
        response_class=Response,
        responses={200: _JPEG},
    )
    def ui_frame(job_id: str, name: str) -> None:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    @app.get("/api/ui/catalog", response_model=UiCatalog, tags=["ui"])
    def ui_catalog() -> UiCatalog:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    @app.get("/health", response_model=LiveReport, tags=["health"])
    def health() -> LiveReport:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    @app.get("/ready", response_model=ReadyReport, tags=["health"])
    def ready() -> ReadyReport:  # pyright: ignore[reportUnusedFunction]
        return _stub()

    return app
