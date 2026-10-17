"""Common durable ingestion for jury and browser requests."""

import errno
import shutil
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from cashguard.core.atomic import atomic_write_json
from cashguard.pipeline.ingest.probe import UnsupportedVideo, probe

from .upload import receive_video, run_io


async def ingest_upload(
    request: Request, *, source: str = "jury", client_ip: str | None = None
) -> str:
    received_at = time.time()
    settings = request.app.state.settings
    repo = request.app.state.repo
    job_id = uuid4().hex
    directory = settings.paths.job_dir(job_id)
    committed = False
    try:
        path, digest = await receive_video(request, directory, settings, received_at)
        try:
            meta = await run_io(probe, path)
        except UnsupportedVideo:
            raise HTTPException(415, "unsupported media type") from None
        metadata = meta.to_dict()
        if settings.pipeline_mode != "stub":
            metadata.pop("stub_verdict", None)
        await run_io(atomic_write_json, directory / "meta.json", metadata)

        def commit_job():
            nonlocal committed
            job = repo.create_job(
                job_id=job_id,
                input_path=str(path),
                sha256=digest,
                received_at=received_at,
                duration_sec=meta.duration_sec,
                source=source,
                # A transcoded UI preview belongs to its own durable worker job.
                # Until preview has a separate queue, do not link these jobs to a
                # jury-only or already-finalized parent that cannot create it.
                deduplicate=not (source == "ui" and meta.needs_transcode),
                width=meta.width,
                height=meta.height,
                codec=meta.codec,
                rotation=meta.rotation,
                needs_transcode=meta.needs_transcode,
                size_bytes=path.stat().st_size,
                metadata=metadata,
                client_ip=client_ip,
                pipeline_version=settings.pipeline_version,
                config_hash=settings.config_hash,
            )
            committed = True
            return job

        job = await run_io(commit_job)
        if source == "ui":
            from .preview import create_preview

            # Preview failure must never undo an accepted durable job.
            await create_preview(path, meta, directory / "preview.mp4")
        return job.id
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
            raise HTTPException(507, "insufficient storage") from None
        raise
    except sqlite3.OperationalError as exc:
        if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            raise HTTPException(507, "insufficient storage") from None
        raise
    finally:
        if not committed:
            # Cancellation/disconnect/invalid input must leave no untracked upload.
            await run_in_threadpool(shutil.rmtree, directory, True)
