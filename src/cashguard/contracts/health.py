"""Проверки живости и готовности, статус для команды."""

from typing import Literal

from pydantic import Field

from cashguard.contracts._base import Contract
from cashguard.contracts.perception import ModelRef


class LiveReport(Contract):
    """``GET /health``: процесс API жив. Без I/O."""

    status: Literal["ok"] = "ok"


class ReadyChecks(Contract):
    worker_perception: bool
    worker_vlm: bool
    models_loaded: bool
    warmup_done: bool
    free_mem_ok: bool
    disk_ok: bool
    queue_ok: bool


class ReadyReport(Contract):
    """``GET /ready``: воркеры, модели, память, диск и очередь в порядке."""

    ready: bool
    checks: ReadyChecks
    vlm_quant: str | None = None
    version: str


class QueueDepth(Contract):
    perception: int = Field(ge=0)
    vlm: int = Field(ge=0)


class WorkerStatus(Contract):
    name: str
    role: str
    pid: int | None = None
    state: Literal["warming", "idle", "busy", "draining", "dead"]
    job_id: str | None = None
    rss_mb: float | None = None
    mlx_active_mb: float | None = None
    quant: str | None = None


class RecentJob(Contract):
    id: str
    source: str
    status: str
    perception_level: str | None = None
    result_level: str | None = None
    verdict: str | None = None
    n: int = Field(ge=0)
    timings: dict[str, float] = Field(default_factory=dict[str, float])


class MemoryStatus(Contract):
    available_gb: float
    mlx_active_gb: float | None = None
    mlx_peak_gb: float | None = None


class AdminStatus(Contract):
    """``GET /api/v1/admin/status`` (только ключ команды)."""

    versions: dict[str, str]
    models: dict[str, ModelRef]
    queue: QueueDepth
    workers: list[WorkerStatus]
    recent_jobs: list[RecentJob]
    memory: MemoryStatus
    disk_free_gb: float
    tunnel_ready: bool | None = None
