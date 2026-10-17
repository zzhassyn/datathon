"""Deterministic synthetic-only adapter while S0's runner ports are pending.

No inference, legal claim or filename-based classification happens here. Only
videos bearing ``comment=cg-stub:clean|violation`` are accepted in this mode.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from cashguard.core.budget import Budget
from cashguard.pipeline.ingest.probe import UnsupportedVideo, probe
from cashguard.pipeline.render.jury import render_jury


class StubInputError(ValueError):
    """The input was not labelled as an explicit synthetic test scenario."""


class StubDeadlineError(TimeoutError):
    """No synthetic result is fabricated after the processing budget expires."""


@dataclass(frozen=True)
class StubAnalysis:
    jury: dict
    evidence: dict[str, bytes]
    scenario: str
    width: int
    height: int


def run_stub_analysis(
    path: Path | str,
    job_id: str,
    *,
    budget: Budget,
    duration_sec: float | None = None,
    delay_sec: float = 0.0,
    progress: Callable[[float], object] | None = None,
) -> StubAnalysis:
    import av

    def checkpoint(fraction: float) -> None:
        if budget.remaining("perception_hard_stop") <= 0:
            raise StubDeadlineError("Время обработки синтетического сценария истекло")
        if progress is not None:
            progress(fraction)

    checkpoint(0.0)
    try:
        metadata = probe(path, deep=True)
    except UnsupportedVideo as error:
        raise StubInputError(str(error)) from error
    duration_sec = metadata.duration_sec
    checkpoint(0.0)
    with av.open(str(path), format="mov", options={"protocol_whitelist": "file"}) as container:
        marker = container.metadata.get("comment", "")
        if marker not in {"cg-stub:clean", "cg-stub:violation"}:
            raise StubInputError(
                "Режим stub принимает только явно отмеченные синтетические сценарии"
            )
        scenario = marker.split(":", 1)[1]
        if not container.streams.video:
            raise StubInputError("В синтетическом файле нет видеодорожки")
        stream = container.streams.video[0]
        frame = next(container.decode(stream), None)
        if frame is None or frame.pts is None or frame.time_base is None:
            raise StubInputError("Синтетический кадр не имеет корректного PTS")
        timestamp = float((frame.pts - (stream.start_time or 0)) * frame.time_base)
        jpeg = b""
        if scenario == "violation":
            # PyAV/FFmpeg encodes a genuine JPEG of the decoded synthetic frame.
            buffer = BytesIO()
            with av.open(buffer, "w", format="mjpeg") as output:
                encoder = output.add_stream("mjpeg", rate=1)
                encoder.width, encoder.height = frame.width, frame.height
                encoder.pix_fmt = "yuvj420p"
                frame.pts = 0
                for packet in encoder.encode(frame):
                    output.mux(packet)
                for packet in encoder.encode():
                    output.mux(packet)
            jpeg = buffer.getvalue()
    # A configurable deterministic delay makes lifecycle/crash tests observable.
    start = time.monotonic()
    while time.monotonic() - start < delay_sec:
        elapsed = time.monotonic() - start
        checkpoint(min(0.9, elapsed / max(delay_sec, 0.001)))
        time.sleep(min(0.05, max(0.0, delay_sec - elapsed)))
    checkpoint(0.95)
    findings = []
    evidence = {}
    if scenario == "violation":
        findings = [
            {
                "type": "Демонстрационное событие (stub)",
                "pts_event": timestamp,
                "description": (
                    "Синтетический тестовый сценарий cg-stub:violation. "
                    "Результат задан метатегом для проверки API; AI-анализ и оценка "
                    "реального нарушения не выполнялись."
                ),
                "evidence_frames": [{"timestamp_sec": timestamp, "jpeg_bytes": jpeg}],
            }
        ]
        evidence["01_event.jpg"] = jpeg
    result = render_jury(job_id, findings, duration_sec=duration_sec)
    checkpoint(1.0)
    return StubAnalysis(result, evidence, scenario, frame.width, frame.height)
