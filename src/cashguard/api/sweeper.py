"""Deadline fallback and retention; inference always stays in a worker."""

import asyncio
import logging
import sqlite3
import time

from starlette.concurrency import run_in_threadpool

from cashguard.jobs.retention import purge_expired

log = logging.getLogger(__name__)


async def sweep(app):
    last_retention = 0.0
    while True:
        await asyncio.sleep(2)
        try:
            await run_in_threadpool(app.state.repo.finalize_overdue, now=time.time())
            if time.monotonic() - last_retention >= 3600:
                settings = app.state.settings
                await run_in_threadpool(
                    purge_expired,
                    app.state.repo,
                    settings.paths,
                    settings.retention_hours,
                )
                last_retention = time.monotonic()
        except (OSError, sqlite3.Error, ValueError):
            # Keep the sweep alive after transient database failure. No payloads.
            log.error("deadline sweep failed")
