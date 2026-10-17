"""Run remux off the ASGI loop, after the job has been committed."""

from starlette.concurrency import run_in_threadpool

from cashguard.pipeline.ingest.preview import make_preview


async def create_preview(path, meta, out):
    return await run_in_threadpool(make_preview, path, meta, out)
