"""Typed findings → consistent jury/UI artifacts; no inference or invented evidence."""

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
from av.error import FFmpegError

from cashguard.contracts.findings import AnalysisResult, EvidenceImage
from cashguard.contracts.jury import JuryJobResponse
from cashguard.contracts.ui import UiEvidence, UiSignal, UiViolation
from cashguard.pipeline.legal.catalog import ui_legal
from cashguard.pipeline.render.jury import RenderError, render_jury, validate_jpeg
from cashguard.pipeline.render.ui import _FRAME_NAME, _JOB_ID


@dataclass(frozen=True)
class RenderedAnalysis:
    jury: JuryJobResponse
    ui: dict[str, Any]
    evidence: dict[str, bytes]


def render_analysis(result: AnalysisResult, evidence_dir: Path) -> RenderedAnalysis:
    """Validate before publication. Evidence filenames follow the existing UI route contract.

    UI retains all findings. Jury receives the first five events in chronological order.
    Signal evidence is validated too, but signals never alter the jury verdict.
    """
    if not _JOB_ID.fullmatch(result.job_id):
        raise RenderError("Неверный job_id анализа")
    if (result.verdict == "violation") != bool(result.violations):
        raise RenderError("Вердикт анализа не соответствует событиям")
    root = evidence_dir.resolve()
    images: dict[str, bytes] = {}
    duration = result.video.duration_sec
    ids = [event.id for event in [*result.violations, *result.signals]]
    if len(ids) != len(set(ids)):
        raise RenderError("Идентификаторы событий должны быть уникальны")

    def evidence(frame: EvidenceImage) -> UiEvidence:
        if (
            not _FRAME_NAME.fullmatch(frame.file)
            or not frame.file.endswith(f"_{frame.kind}.jpg")
            or frame.timestamp_sec > duration
        ):
            raise RenderError("Неверный путь или таймкод доказательства")
        path = (root / frame.file).resolve()
        if not path.is_relative_to(root):
            raise RenderError("Доказательство вне каталога задания")
        if frame.file not in images:
            images[frame.file] = path.read_bytes()
            validate_jpeg(images[frame.file])
        try:
            with av.open(io.BytesIO(images[frame.file]), mode="r", format="mjpeg") as source:
                decoded = next(source.decode(video=0))
                if (decoded.width, decoded.height) != (frame.width, frame.height):
                    raise RenderError("Размер доказательства не соответствует JPEG")
        except (FFmpegError, StopIteration) as error:
            raise RenderError("Доказательство не декодируется") from error
        return UiEvidence(
            timestamp_sec=frame.timestamp_sec,
            url=f"/api/ui/jobs/{result.job_id}/frames/{frame.file}",
            kind=frame.kind,
            width=frame.width,
            height=frame.height,
        )

    events = sorted(result.violations, key=lambda event: (event.timecode_sec, event.id))
    violations = []
    jury_findings = []
    for event in events:
        if not 0 <= event.start_sec <= event.timecode_sec <= event.end_sec <= duration:
            raise RenderError("Интервал события вне видео")
        if round(event.evidence[0].timestamp_sec, 2) != round(event.timecode_sec, 2):
            raise RenderError("Первый кадр должен подтверждать время события")
        violations.append(
            UiViolation(
                **event.model_dump(exclude={"legal", "evidence"}),
                legal=[ui_legal(ref) for ref in event.legal],
                legal_note_ru=(
                    None
                    if event.legal
                    else "Нормативное основание для этого события не установлено."
                ),
                evidence=[evidence(frame) for frame in event.evidence],
            ).model_dump(mode="json")
        )
        jury_findings.append(
            {
                "type": event.title_ru,
                "pts_event": event.timecode_sec,
                "description": event.description_ru,
                "evidence_frames": [
                    {"timestamp_sec": frame.timestamp_sec, "jpeg_bytes": images[frame.file]}
                    for frame in event.evidence
                ],
            }
        )
    signals = []
    for signal in sorted(result.signals, key=lambda event: (event.t_sec, event.id)):
        if signal.t_sec > duration:
            raise RenderError("Сигнал вне длительности видео")
        signals.append(
            UiSignal(
                **signal.model_dump(exclude={"evidence"}),
                evidence=[evidence(frame) for frame in signal.evidence],
            ).model_dump(mode="json")
        )
    jury = JuryJobResponse.model_validate(
        render_jury(result.job_id, jury_findings[:5], duration_sec=duration, safe_fallback=False)
    )
    return RenderedAnalysis(
        jury=jury,
        ui={
            "verdict": result.verdict,
            "violations": violations,
            "signals": signals,
            "perception_level": result.perception_level,
            "timings": result.timings,
        },
        evidence=images,
    )
