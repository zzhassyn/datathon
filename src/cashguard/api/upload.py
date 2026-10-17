"""Bounded multipart streaming: request -> parser -> file, never request.body()."""

import asyncio
import errno
import hashlib
import os
import shutil
import time
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from streaming_form_data import StreamingFormDataParser
from streaming_form_data.parser import (
    ParseFailedException,
    UnexpectedPartException,
    parse_content_boundary,
)
from streaming_form_data.targets import BaseTarget


async def run_io(function, *args, **kwargs):
    """Drain finite file/DB work before propagating request cancellation.

    A cancelled await must not let cleanup race an in-flight write or COMMIT.
    """
    task = asyncio.create_task(run_in_threadpool(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Keep the thread owned until it finishes even if cancellation repeats.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        # Retrieve a possible I/O exception, while preserving cancellation.
        if not task.cancelled():
            task.exception()
        raise


class DiskTarget(BaseTarget):
    def __init__(self, path: Path, limit: int):
        super().__init__()
        self.path, self.limit = path, limit
        self.size = 0
        self.sha256 = hashlib.sha256()
        self.handle = None
        self.parts = 0
        self.finished = False

    def on_start(self):
        self.parts += 1
        if self.parts != 1:
            raise HTTPException(422, "exactly one multipart file is required")
        self.handle = self.path.open("xb")

    def on_data_received(self, chunk):
        self.size += len(chunk)
        if self.size > self.limit:
            raise HTTPException(413, "file too large")
        self.sha256.update(chunk)
        self.handle.write(chunk)

    def on_finish(self):
        self.finished = True

    def close(self, *, durable=False):
        if self.handle is not None:
            try:
                if durable:
                    self.handle.flush()
                    os.fsync(self.handle.fileno())
            finally:
                self.handle.close()
                self.handle = None


def disk_free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1_000_000_000


async def receive_video(
    request: Request, directory: Path, settings, received_at: float
) -> tuple[Path, str]:
    limit = int(settings.max_upload_mb * 1_000_000)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            size = int(content_length)
            if size < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "invalid content length") from None
        if size > limit:
            raise HTTPException(413, "file too large")
    if (
        await run_in_threadpool(disk_free_gb, settings.paths.jobs_dir)
        < settings.min_free_disk_gb
    ):
        raise HTTPException(507, "insufficient storage")
    directory.mkdir(parents=True, exist_ok=False)
    temporary = directory / "input.part"
    target = DiskTarget(temporary, limit)
    try:
        if len(parse_content_boundary(request.headers)) > 70:
            raise HTTPException(422, "multipart boundary too long")
        parser = StreamingFormDataParser(headers=request.headers, strict=True)
        parser.register("file", target)
        total = 0
        header = bytearray()
        header_complete = False
        remaining = max(
            0.001, settings.upload_timeout_sec - (time.time() - received_at)
        )
        async with asyncio.timeout(remaining):
            async for chunk in request.stream():
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, "file too large")
                # Parser and file writes run off the event loop, with backpressure.
                for offset in range(0, len(chunk), 64 * 1024):
                    piece = chunk[offset : offset + 64 * 1024]
                    if not header_complete:
                        header.extend(piece[: 16_388 - len(header)])
                        end = header.find(b"\r\n\r\n")
                        if end >= 0:
                            header_complete = True
                            header.clear()
                        elif len(header) > 16_384:
                            raise HTTPException(422, "multipart headers too large")
                    await run_io(parser.data_received, piece)
        if target.parts != 1 or not target.finished or target.size == 0:
            raise HTTPException(422, "complete multipart field 'file' is required")
        await run_io(target.close, durable=True)
        destination = directory / "input.mov"
        await run_io(os.replace, temporary, destination)
        # Persist the rename before committing the database row.
        if os.name == "posix":
            fd = os.open(directory, os.O_RDONLY)
            try:
                await run_io(os.fsync, fd)
            finally:
                os.close(fd)
        return destination, target.sha256.hexdigest()
    except TimeoutError:
        raise HTTPException(422, "upload deadline exceeded") from None
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
            raise HTTPException(507, "insufficient storage") from None
        raise
    except (
        ValueError,
        RuntimeError,
        ParseFailedException,
        UnexpectedPartException,
    ) as exc:
        raise HTTPException(422, "invalid multipart body") from exc
    finally:
        target.close()
        temporary.unlink(missing_ok=True)
