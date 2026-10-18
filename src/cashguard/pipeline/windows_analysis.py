"""Windows trained-model handler; publishes only existing AnalysisResult contracts."""

import hashlib
import json
import time

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import (
    AnalysisResult,
    AnalysisVersions,
    ViolationFinding,
    VlmStatus,
)
from cashguard.contracts.jobs import (
    Degradation,
    DegradationReason,
    JobStage,
    PerceptionLevel,
    ResultLevel,
)
from cashguard.contracts.video import VideoMeta
from cashguard.core.atomic import atomic_write_bytes, atomic_write_json
from cashguard.pipeline.legal.catalog import load_catalog
from cashguard.pipeline.local_config import (
    AnalysisBlocked,
    config_fingerprint,
    confirmed_calibration,
    read_config,
)
from cashguard.pipeline.rules.discipline.personal_items import evaluate_frames


def analyze(input_path, output, job_id, deadline_at, config_path, progress, *, cached=None):
    from cashguard.pipeline.perception.windows_inference import run

    config = read_config(config_path)
    initial_config_hash = config_fingerprint(config_path)
    request = {
        **config,
        "input_path": str(input_path),
        "out_dir": str(output / "inference"),
        "deadline_at": deadline_at,
    }
    # No ROI may be inferred from the desk region. Door has a separate camera calibration.
    request.pop("door_roi", None)
    started = time.monotonic()
    run(request, cached=cached, progress_fn=progress)
    report = json.loads((output / "inference/observations.json").read_text(encoding="utf-8"))
    if report["coverage"].get("complete") is not True:
        raise AnalysisBlocked("Видео обработано не полностью; чистый результат запрещён.")
    video = VideoMeta.model_validate(report["video"])
    if config_fingerprint(config_path) != initial_config_hash:
        raise AnalysisBlocked("Калибровка изменилась во время обработки. Загрузите видео повторно.")
    progress("rules", 0, "Проверка калибровки и PERSONAL_ITEMS; остальные нарушения не проверяются")
    calibration = confirmed_calibration(config, report["video"])
    candidates = evaluate_frames(
        report["frames"],
        calibration,
        min_duration_sec=config.get("rule", {}).get("min_duration_sec", 2),
    )
    atomic_write_json(
        output / "rule_candidates.json", [c.model_dump(mode="json") for c in candidates]
    )
    if not candidates:
        raise AnalysisBlocked(
            "PERSONAL_ITEMS не подтверждено. Классы личных вещей недостаточно проверены; "
            "отсутствие обнаружений не доказывает отсутствие нарушения. "
            "Чистый вердикт не сформирован."
        )
    progress(
        "rules",
        1,
        f"Технических кандидатов PERSONAL_ITEMS: {len(candidates)}; "
        "требуется ручная проверка качества",
    )
    catalog = load_catalog()
    entry = catalog.entries[ViolationCode.PERSONAL_ITEMS]
    evidence_dir = output / "evidence"
    findings = []
    progress("evidence", 0, "Подготовка локальных кадров-доказательств по PTS")
    for index, candidate in enumerate(candidates[:5], 1):
        images = []
        for number, at in enumerate(dict.fromkeys([candidate.start_sec, candidate.end_sec])):
            frame = min(report["frames"], key=lambda f: abs(f["t_sec"] - at))
            kind = "event" if number == 0 else "context"
            name = f"{index:02}_{kind}.jpg"
            atomic_write_bytes(
                evidence_dir / name,
                (output / "inference/frames" / frame["image_name"]).read_bytes(),
            )
            images.append(
                dict(
                    kind=kind,
                    timestamp_sec=frame["t_sec"],
                    file=name,
                    width=frame["width"],
                    height=frame["height"],
                )
            )
        facts = candidate.facts
        description = (
            f"В {candidate.start_sec:.1f} с на подтверждённой поверхности стола кассира "
            f"обнаружена сумка, устойчиво наблюдаемая {facts['dur_sec']:.1f} с при наличии "
            "рабочего контекста. Принадлежность сотруднику подтверждена оператором. "
            "Основание: п. 12 ч. 1 Правил осуществления кассовых операций, "
            "утверждённых постановлением Правления НБРК № 231 от 29.11.2019. "
            "Качество автоматического определения требует отдельной проверки."
        )
        findings.append(
            ViolationFinding(
                id=candidate.id,
                code=candidate.code,
                title_ru=entry.title_ru,
                timecode_sec=images[0]["timestamp_sec"],
                start_sec=candidate.start_sec,
                end_sec=candidate.end_sec,
                confidence=candidate.confidence,
                vlm_status=VlmStatus.SKIPPED,
                provisional=False,
                description_ru=description,
                legal=entry.legal,
                evidence=images,
            )
        )
    progress("evidence", 1, "Кадры-доказательства сохранены локально")
    model_hash = hashlib.sha256(json.dumps(report["models"], sort_keys=True).encode()).hexdigest()
    result = AnalysisResult(
        job_id=job_id,
        verdict="violation",
        result_level=ResultLevel.RULES,
        perception_level=PerceptionLevel.FAST,
        violations=findings,
        signals=[],
        video=video,
        timings={**report["timings"], "total": time.monotonic() - started},
        degradations=[Degradation(stage=JobStage.VLM, reason=DegradationReason.VLM_DISABLED)],
        versions=AnalysisVersions(
            git_sha="local-windows", models_hash=model_hash, config_hash=catalog.config_hash
        ),
    )
    atomic_write_json(
        output / "technical_scope.json",
        dict(
            evaluated_rules=["PERSONAL_ITEMS"],
            all_other_rules="NOT_EVALUATED",
            accuracy_validated=False,
            door="EVALUATED" if report.get("door_evaluated") else "NOT_EVALUATED_ROI_MISSING",
            detection_counts="per_frame_not_unique_objects",
        ),
    )
    return result
