"""Windows trained-model handler; publishes only existing AnalysisResult contracts."""

import hashlib
import json
import time

from cashguard.contracts.findings import (
    AnalysisResult,
    AnalysisVersions,
    Decision,
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
from cashguard.pipeline.legal.describe import describe
from cashguard.pipeline.local_config import (
    AnalysisBlocked,
    config_fingerprint,
    confirmed_calibration,
    read_config,
)
from cashguard.pipeline.rules.discipline.personal_items import evaluate_frames
from cashguard.pipeline.scene.auto_zones import auto_calibration
from cashguard.pipeline.scene.temporal import build_numeric


def temporal_candidates(report, calibration, options):
    from cashguard.pipeline.rules.cashops.prev_cash_on_desk import evaluate_scene as previous_cash
    from cashguard.pipeline.rules.leave.safe_open import evaluate_scene as safe_open
    from cashguard.pipeline.rules.leave.valuables_left import evaluate_scene as valuables_left

    codes = options.get("codes", ["VALUABLES_LEFT", "SAFE_OPEN"])
    evaluators = {
        "VALUABLES_LEFT": valuables_left,
        "SAFE_OPEN": safe_open,
        "PREV_CASH_ON_DESK": previous_cash,
    }
    if not codes or len(codes) != len(set(codes)) or any(c not in evaluators for c in codes):
        raise AnalysisBlocked("Некорректный список правил сцены.")
    if calibration.get("scene_zones_confirmed") is not True:
        raise AnalysisBlocked("Требуется подтверждение зон кассира, клиента, выхода и сейфа.")
    scene = build_numeric(
        report["frames"], report["video"], calibration, calibration.get("scene", {})
    )
    candidates = []
    for code in codes:
        if code == "PREV_CASH_ON_DESK":
            candidates.extend(previous_cash(scene))
        else:
            candidates.extend(
                evaluators[code](scene, min_duration_sec=float(options.get("min_duration_sec", 2)))
            )
    return scene, sorted(candidates, key=lambda c: c.t_sec), codes


def catalog_hash():
    return load_catalog().config_hash


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
    temporal = config.get("temporal_rules", {}).get("enabled") is True
    if temporal:
        request["tracking_enabled"] = True
    started = time.monotonic()
    run(request, cached=cached, progress_fn=progress)
    report = json.loads((output / "inference/observations.json").read_text(encoding="utf-8"))
    if report["coverage"].get("complete") is not True:
        raise AnalysisBlocked("Видео обработано не полностью; чистый результат запрещён.")
    video = VideoMeta.model_validate(report["video"])
    if config_fingerprint(config_path) != initial_config_hash:
        raise AnalysisBlocked("Калибровка изменилась во время обработки. Загрузите видео повторно.")
    progress("rules", 0, "Проверка подтверждённых зон и правил")
    auto_zones = temporal and config["temporal_rules"].get("auto_zones") is True
    if auto_zones:
        try:
            calibration = auto_calibration(report)
        except ValueError as error:
            raise AnalysisBlocked(f"Автоматические зоны не определены: {error}") from None
        atomic_write_json(output / "auto_calibration.json", calibration)
    else:
        calibration = confirmed_calibration(config, report["video"], require_ownership=not temporal)
    scene = None
    if temporal:
        scene, candidates, evaluated = temporal_candidates(
            report, calibration, config["temporal_rules"]
        )
        atomic_write_json(output / "scene.json", scene.model_dump(mode="json"))
    else:
        evaluated = ["PERSONAL_ITEMS"]
        candidates = evaluate_frames(
            report["frames"],
            calibration,
            min_duration_sec=config.get("rule", {}).get("min_duration_sec", 2),
        )
    atomic_write_json(
        output / "rule_candidates.json", [c.model_dump(mode="json") for c in candidates]
    )
    atomic_write_json(
        output / "technical_scope.json",
        dict(
            evaluated_rules=evaluated,
            all_other_rules="NOT_EVALUATED",
            accuracy_validated=False,
            door="EVALUATED" if report.get("door_evaluated") else "NOT_EVALUATED_ROI_MISSING",
            detection_counts="per_frame_not_unique_objects",
            zones="auto_objects_not_human_confirmed" if auto_zones else "human_confirmed",
            clean_verdict_basis=(
                "no_candidates_from_evaluated_rules_only" if auto_zones else "blocked"
            ),
        ),
    )
    model_hash = hashlib.sha256(json.dumps(report["models"], sort_keys=True).encode()).hexdigest()
    if not candidates and auto_zones:
        progress("rules", 1, "Кандидатов по оцениваемым правилам нет; чистый вердикт по их охвату")
        return AnalysisResult(
            job_id=job_id,
            verdict="no_violation",
            result_level=ResultLevel.RULES,
            perception_level=PerceptionLevel.FAST,
            violations=[],
            signals=[],
            video=video,
            timings={**report["timings"], "total": time.monotonic() - started},
            degradations=[Degradation(stage=JobStage.VLM, reason=DegradationReason.VLM_DISABLED)],
            versions=AnalysisVersions(
                git_sha="local-mac-auto-zones", models_hash=model_hash, config_hash=catalog_hash()
            ),
        )
    if not candidates:
        raise AnalysisBlocked(
            "Выбранные правила не подтверждены. "
            "Качество моделей ещё не проверено для чистого вердикта; "
            "отсутствие обнаружений не доказывает отсутствие нарушения. "
            "Чистый вердикт не сформирован."
        )
    progress(
        "rules",
        1,
        f"Технических кандидатов: {len(candidates)}; требуется ручная проверка качества",
    )
    catalog = load_catalog()
    evidence_dir = output / "evidence"
    findings = []
    progress("evidence", 0, "Подготовка локальных кадров-доказательств по PTS")
    for index, candidate in enumerate(candidates[:5], 1):
        entry = catalog.entries[candidate.code]
        images = []
        requested_times = list(
            dict.fromkeys([candidate.t_sec, candidate.start_sec, candidate.end_sec])
        )
        for number, at in enumerate(requested_times):
            frame = min(report["frames"], key=lambda f: abs(f["t_sec"] - at))
            kind = "event" if number == 0 else "context"
            name = f"{(index - 1) * 3 + number + 1:02}_{kind}.jpg"
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
        legal = entry.legal
        if scene is not None:
            description, legal = describe(
                Decision(
                    candidate=candidate, score=candidate.confidence, vlm_status=VlmStatus.SKIPPED
                ),
                scene,
                catalog.entries,
            )
        else:
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
                start_sec=min(candidate.start_sec, video.duration_sec),
                end_sec=min(candidate.end_sec, video.duration_sec),
                confidence=candidate.confidence,
                vlm_status=VlmStatus.SKIPPED,
                provisional=False,
                description_ru=description,
                legal=legal,
                evidence=images,
            )
        )
    progress("evidence", 1, "Кадры-доказательства сохранены локально")
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
            git_sha="local-mac-auto-zones" if auto_zones else "local-windows",
            models_hash=model_hash,
            config_hash=catalog.config_hash,
        ),
    )
    return result
