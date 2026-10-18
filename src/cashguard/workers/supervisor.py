"""Fresh-interpreter supervision, bounded restart backoff and hang detection."""

import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import psutil
import structlog

from cashguard.core.env import apply_offline_env
from cashguard.core.settings import Settings
from cashguard.jobs.repo import JobRepository


@dataclass
class Child:
    name: str
    command: list[str]
    process: subprocess.Popen | None = None
    next_start: float = 0.0
    failures: int = 0
    started_at: float = 0.0


class Supervisor:
    def __init__(
        self,
        settings: Settings,
        *,
        profile: str = "dev",
        start_api: bool = True,
        restart_base: float = 1.0,
        shutdown_timeout: float = 30.0,
    ) -> None:
        self.settings = settings
        self.repo = JobRepository(settings.db_path, deadlines_path=Path("configs/deadlines.yaml"))
        self.stop_event = threading.Event()
        self.restart_base, self.shutdown_timeout = restart_base, shutdown_timeout
        self.children: list[Child] = []
        self._next_health = time.monotonic() + 5
        self._health_failures = 0
        if settings.pipeline_mode != "stub" and not (
            settings.pipeline_mode == "full"
            and settings.vision_backend == "torch"
            and settings.local_analysis_config
            and settings.local_model_python
        ):
            raise RuntimeError(
                "Supervisor: real/replay runner ожидается от S0; выберите CG_PIPELINE_MODE=stub"
            )
        if start_api:
            command = [sys.executable, "-m", "cashguard.cli", "api"]
            if profile == "dev":
                command.append("--reload")
            self.children.append(Child("api", command))
        name = f"all-{os.getpid()}"
        self.children.append(
            Child(
                name,
                [
                    str(settings.local_model_python)
                    if settings.pipeline_mode == "full"
                    else sys.executable,
                    "-m",
                    "cashguard.workers",
                    "--role",
                    "all",
                    "--name",
                    name,
                ],
            )
        )

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        apply_offline_env(env)
        values = self.settings.model_dump(exclude={"api_keys", "ui_access_code"})
        for name, value in values.items():
            env[f"CG_{name.upper()}"] = (
                str(value).lower() if isinstance(value, bool) else str(value)
            )
        env.update(
            {
                "CG_DATA_DIR": str(self.settings.var_dir),
                "CG_VLM_ENABLED": "false",
                "CG_API_KEYS": self.settings.api_keys.get_secret_value(),
                "CG_UI_ACCESS_CODE": self.settings.ui_access_code.get_secret_value(),
                "PYTHONUNBUFFERED": "1",
            }
        )
        return env

    def _start(self, child: Child) -> None:
        child.process = subprocess.Popen(child.command, env=self._environment())
        child.started_at = time.time()
        structlog.get_logger().info("child_started", name=child.name, pid=child.process.pid)

    @staticmethod
    def _matches(child: Child) -> bool:
        if child.process is None or child.process.poll() is not None:
            return False
        try:
            command = psutil.Process(child.process.pid).cmdline()
            expected_module = child.command[2]
            return (
                "-m" in command
                and expected_module in command
                and (child.name == "api" or child.name in command)
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _kill_stalled(self, child: Child, workers: dict[str, dict], now: float) -> None:
        if child.name == "api" or child.process is None:
            return
        worker = workers.get(child.name)
        if worker is None or worker.get("pid") != child.process.pid:
            stalled = now - child.started_at > 300
        else:
            state = worker.get("state")
            alive = float(worker.get("alive_at") or child.started_at)
            progress = float(worker.get("progress_at") or child.started_at)
            stalled = now - alive > 20 or (
                state in {"busy", "warming"}
                and now - progress > (300 if state == "warming" else 60)
            )
        if stalled and self._matches(child):
            structlog.get_logger().warning(
                "worker_unresponsive", name=child.name, pid=child.process.pid
            )
            child.process.kill()

    def tick(self) -> None:
        now, mono = time.time(), time.monotonic()
        workers = {worker["name"]: worker for worker in self.repo.workers()}
        for child in self.children:
            if child.process is not None and child.process.poll() is not None:
                if child.process.returncode == 0 or now - child.started_at > 60:
                    child.failures = 0
                child.failures += 1
                child.next_start = mono + min(
                    30, self.restart_base * 2 ** min(child.failures - 1, 10)
                )
                child.process = None
            if child.process is None and mono >= child.next_start and not self.stop_event.is_set():
                self._start(child)
            self._kill_stalled(child, workers, now)
        self.repo.requeue_dead()
        self.repo.finalize_overdue()
        if mono >= self._next_health:
            self._next_health = mono + 5
            api = next((child for child in self.children if child.name == "api"), None)
            if api is not None and api.process is not None:
                host = (
                    "127.0.0.1" if self.settings.host in {"0.0.0.0", "::"} else self.settings.host  # noqa: S104 — сравнение, не привязка
                )
                try:
                    with urlopen(
                        f"http://{host}:{self.settings.port}/health", timeout=1
                    ) as response:
                        healthy = response.status == 200
                except (URLError, OSError):
                    healthy = False
                self._health_failures = 0 if healthy else self._health_failures + 1
                if self._health_failures >= 3 and self._matches(api):
                    self._terminate_tree(api.process)
                    self._health_failures = 0

    @staticmethod
    def _terminate_tree(process: subprocess.Popen, *, force: bool = False) -> list[psutil.Process]:
        # Include uvicorn's reload child, which must not outlive its supervisor.
        try:
            parent = psutil.Process(process.pid)
            processes = parent.children(recursive=True) + [parent]
        except psutil.NoSuchProcess:
            return []
        for member in processes:
            try:
                member.kill() if force else member.terminate()
            except psutil.NoSuchProcess:
                pass
        return processes

    def stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        self.stop()
        live = [child.process for child in self.children if child.process is not None]
        # Retain process handles even if the reload parent exits first, so its
        # children cannot be orphaned during the bounded shutdown.
        descendants = []
        for process in live:
            descendants.extend(self._terminate_tree(process))
        _, survivors = psutil.wait_procs(descendants, timeout=self.shutdown_timeout)
        for process in survivors:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(survivors, timeout=5)
        for process in live:
            process.poll()

    def run(self) -> None:
        multiprocessing.set_start_method("spawn", force=True)
        apply_offline_env()
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
            signal.signal(signal.SIGINT, lambda *_: self.stop())
        try:
            while not self.stop_event.is_set():
                self.tick()
                self.stop_event.wait(0.25)
        finally:
            self.close()
