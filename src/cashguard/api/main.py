"""HTTP process. No models, background inference, or in-memory job queue."""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from cashguard.api.deps import key_digests
from cashguard.api.ratelimit import UiLimiter
from cashguard.api.routes import admin, health, jury, ui
from cashguard.api.sweeper import sweep
from cashguard.core.settings import Settings, get_settings
from cashguard.jobs.repo import JobRepository


class CachePolicy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send_message(message):
            if message["type"] == "http.response.start" and scope.get(
                "path", ""
            ).startswith("/api/v1/"):
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ]
                message["headers"] = headers + [(b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, receive, send_message)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Fail explicitly instead of silently using synthetic inference for real mode.
    if settings.pipeline_mode != "stub":
        raise RuntimeError("S0 runner integration required for replay/full mode")
    settings.paths.ensure()
    repo = JobRepository(
        settings.db_path, deadlines_path=Path("configs/deadlines.yaml")
    )

    @asynccontextmanager
    async def lifespan(application):
        task = asyncio.create_task(sweep(application))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            repo.db.close()

    application = FastAPI(
        title="CashGuard",
        version="0.1.0-stub",
        lifespan=lifespan,
        separate_input_output_schemas=False,
        redirect_slashes=False,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
    )
    application.state.settings = settings
    application.state.repo = repo
    application.state.key_digests = key_digests(settings.api_keys.get_secret_value())
    application.state.ui_limiter = UiLimiter(settings, repo)
    application.add_middleware(
        GZipMiddleware,
        minimum_size=1024,
        exclude_content_types=["video/mp4", "image/jpeg"],
    )
    application.add_middleware(CachePolicy)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        # Exception strings can contain upload fragments, secrets or private paths.
        import logging

        logging.getLogger(__name__).error("request failed: %s", type(exc).__name__)
        return JSONResponse(
            {"detail": "internal error"},
            status_code=500,
            headers={"Cache-Control": "no-store"},
        )

    for router in (
        jury.router,
        admin.router,
        ui.session_router,
        ui.router,
        health.router,
    ):
        application.include_router(router)
    web = Path(settings.web_dist).resolve()
    if (web / "assets").is_dir():
        application.mount(
            "/assets", StaticFiles(directory=web / "assets"), name="assets"
        )

    @application.get("/", include_in_schema=False)
    async def index():
        if not (web / "index.html").is_file():
            raise HTTPException(404, "web build unavailable")
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    return application
