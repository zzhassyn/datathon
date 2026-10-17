"""In-process UI admission control (one API process), including in-flight uploads."""

import asyncio
import ipaddress
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("cf-connecting-ip")
    if peer == "127.0.0.1" and forwarded:
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer


class UiLimiter:
    def __init__(self, settings, repo):
        self.settings, self.repo = settings, repo
        self.lock = asyncio.Lock()
        self.history = defaultdict(deque)
        self.pending = defaultdict(int)

    @asynccontextmanager
    async def admit(self, ip):
        async with self.lock:
            now = time.monotonic()
            window = self.settings.ui_rate_window_sec
            for old_ip in list(self.history):
                history = self.history[old_ip]
                while history and history[0] <= now - window:
                    history.popleft()
                if not history and not self.pending.get(old_ip):
                    del self.history[old_ip]
            history = self.history[ip]
            active = await run_in_threadpool(self.repo.active_count, source="ui", client_ip=ip)
            total = await run_in_threadpool(self.repo.active_count, source="ui")
            if (
                len(history) >= self.settings.ui_rate_uploads
                or active + self.pending[ip] >= self.settings.ui_rate_active
                or total + sum(self.pending.values()) >= self.settings.ui_rate_queue
            ):
                retry = max(1, int(window - (now - history[0]))) if history else 2
                raise HTTPException(
                    429, "upload limit reached", headers={"Retry-After": str(retry)}
                )
            history.append(now)
            self.pending[ip] += 1
        try:
            yield
        finally:
            async with self.lock:
                self.pending[ip] -= 1
                if not self.pending[ip]:
                    del self.pending[ip]
