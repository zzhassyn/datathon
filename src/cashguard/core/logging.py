"""Structured process-local logs. Callers must never pass request bodies."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

_PRIVATE = {
    "api_key",
    "api_keys",
    "x-api-key",
    "authorization",
    "image_base64",
    "frames",
    "video",
    "token",
}


def redact_secrets(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: "[redacted]" if k.lower() in _PRIVATE else clean(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value

    return clean(event)


def configure_logging(path: Path | None = None, level: int = logging.INFO) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handlers = [RotatingFileHandler(path, maxBytes=10_000_000, backupCount=5)]
    logging.basicConfig(
        level=level, format="%(message)s", handlers=handlers, force=True
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            redact_secrets,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
