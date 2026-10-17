"""Эталонные ответы ``GET /api/ui/jobs/{job_id}`` для параллельной работы frontend и backend.

    uv run python scripts/gen_ui_examples.py          # перегенерировать tests/fixtures/ui/*.json
    uv run python scripts/gen_ui_examples.py --check  # упасть, если файлы устарели

Ответы собираются из контрактов (``UiJobView``) и настоящего каталога ``configs/catalog``:
названия типов и нормы берутся оттуда, а не придумываются. Frontend верстает по этим файлам,
backend отдаёт ровно такую форму (контрактный тест ``tests/contract/test_ui_examples.py``).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from cashguard.contracts.catalog import CatalogEntry, LegalRef
from cashguard.contracts.jobs import (
    JobStatus,
    PerceptionLevel,
    ResultLevel,
    StageCode,
    StageState,
    StageStatus,
)
from cashguard.contracts.ui import (
    UiDegradation,
    UiError,
    UiEvidence,
    UiJobView,
    UiLegalRef,
    UiSignal,
    UiVideo,
    UiViolation,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "tests" / "fixtures" / "ui"
CATALOG_DIR = ROOT / "configs" / "catalog"

CREATED = "2026-10-17T09:00:00.000000Z"
STAGE_ORDER = (
    StageCode.QUEUED,
    StageCode.PROBE,
    StageCode.PERCEPTION,
    StageCode.RULES,
    StageCode.EVIDENCE,
    StageCode.VLM,
    StageCode.DONE,
)


def _entry(code: str) -> CatalogEntry:
    data = yaml.safe_load((CATALOG_DIR / f"{code}.yaml").read_text(encoding="utf-8"))
    return CatalogEntry.model_validate(data)


def _ui_legal(ref: LegalRef) -> UiLegalRef:
    return UiLegalRef(
        act_code=str(ref.act_code),
        act_title_ru=ref.act_title_ru,
        clause=ref.clause,
        quote_ru=ref.quote_ru,
        url=ref.url,
        strength=ref.strength,
        note_ru=ref.note_ru,
    )


def _stages(
    done_until: StageCode | None, running: StageCode | None = None, **kw: Any
) -> list[StageState]:
    out: list[StageState] = []
    passed = done_until is not None
    for code in STAGE_ORDER:
        if code == running:
            out.append(
                StageState(code=code, status=StageStatus.RUNNING, progress=kw.get("progress", 0.0))
            )
            passed = False
            continue
        if passed:
            status = kw.get("skipped", {}).get(code, StageStatus.DONE)
            out.append(
                StageState(
                    code=code, status=status, progress=1.0, elapsed_sec=kw.get("t", {}).get(code)
                )
            )
        else:
            out.append(StageState(code=code, status=StageStatus.PENDING, progress=0.0))
        if code == done_until:
            passed = False
    return out


def _video(job: str) -> UiVideo:
    return UiVideo(url=f"/api/ui/jobs/{job}/video.mp4", duration_sec=25.9, width=464, height=848)


def _evidence(job: str, i: int, t: float, kinds: tuple[str, ...]) -> list[UiEvidence]:
    shifts = {"event": 0.0, "zoom": 0.0, "context": -1.5}
    return [
        UiEvidence(
            timestamp_sec=round(max(t + shifts[k], 0.0), 2),
            url=f"/api/ui/jobs/{job}/frames/{i:02d}_{k}.jpg",
            kind=k,  # pyright: ignore[reportArgumentType]
            width=464 if k != "zoom" else 320,
            height=848 if k != "zoom" else 320,
        )
        for k in kinds
    ]


def _violation(job: str, i: int, code: str, t: float, description: str, **kw: Any) -> UiViolation:
    entry = _entry(code)
    facts: set[str] = set(kw.get("facts", ()))
    # Как legal.describe: норма с applies_if цитируется, только если факт сценария истинен.
    legal = [_ui_legal(r) for r in entry.legal if r.applies_if is None or r.applies_if in facts]
    return UiViolation(
        id=f"{code.lower()}-{i}",
        code=code,
        title_ru=entry.title_ru,
        timecode_sec=t,
        start_sec=kw.get("start", t),
        end_sec=kw.get("end", t),
        confidence=kw.get("confidence", 0.86),
        vlm_status=kw.get("vlm_status", "confirmed"),
        provisional=kw.get("provisional", False),
        description_ru=description,
        legal=legal,
        legal_note_ru=kw.get("legal_note_ru"),
        evidence=_evidence(job, i, t, kw.get("kinds", ("event", "zoom", "context"))),
    )


def build_examples() -> dict[str, UiJobView]:
    ex: dict[str, UiJobView] = {}

    job = "0" * 31 + "1"
    ex["01_queued"] = UiJobView(
        job_id=job,
        status=JobStatus.QUEUED,
        pipeline_mode="full",
        created_at=CREATED,
        elapsed_sec=3.2,
        queue_position=2,
        eta_sec=55.0,
        stages=_stages(None, StageCode.QUEUED),
    )

    job = "0" * 31 + "2"
    ex["02_running"] = UiJobView(
        job_id=job,
        status=JobStatus.RUNNING,
        pipeline_mode="full",
        created_at=CREATED,
        elapsed_sec=18.4,
        eta_sec=21.0,
        perception_level=PerceptionLevel.FULL,
        stages=_stages(
            StageCode.PROBE,
            StageCode.PERCEPTION,
            progress=0.45,
            t={StageCode.QUEUED: 0.3, StageCode.PROBE: 0.6},
        ),
        video=_video(job),
    )

    job = "0" * 31 + "3"
    ex["03_done_clean"] = UiJobView(
        job_id=job,
        status=JobStatus.DONE,
        pipeline_mode="full",
        created_at=CREATED,
        elapsed_sec=31.7,
        perception_level=PerceptionLevel.FULL,
        result_level=ResultLevel.FULL,
        stages=_stages(StageCode.DONE, t={StageCode.PERCEPTION: 14.1, StageCode.VLM: 9.8}),
        video=_video(job),
        verdict="no_violation",
        signals=[
            UiSignal(
                id="sig_doc_not_handed-1",
                code="SIG_DOC_NOT_HANDED",
                title_ru="Не наблюдается передача документа клиенту",
                t_sec=19.5,
                description_ru="С 17,0 по 19,5 с не наблюдается передача клиенту "
                "документа об операции. "
                "Сигнал для проверяющего, на вердикт не влияет.",
                evidence=_evidence(job, 1, 19.5, ("event",)),
            )
        ],
        timings={"perception": 14.1, "rules": 0.4, "vlm": 9.8, "total": 31.7},
    )

    job = "0" * 31 + "4"
    ex["04_done_violations"] = UiJobView(
        job_id=job,
        status=JobStatus.DONE,
        pipeline_mode="full",
        created_at=CREATED,
        elapsed_sec=44.9,
        perception_level=PerceptionLevel.FULL,
        result_level=ResultLevel.FULL,
        stages=_stages(StageCode.DONE, t={StageCode.PERCEPTION: 17.0, StageCode.VLM: 15.2}),
        video=_video(job),
        verdict="violation",
        violations=[
            _violation(
                job,
                1,
                "NO_CLIENT_ID",
                10.0,
                "В 10,0 с кассир принимает наличные; документ, удостоверяющий личность клиента, "
                "в кадре не предъявлялся.",
                facts=("cash_in",),
                start=8.5,
                end=12.5,
            ),
            _violation(
                job,
                2,
                "SAFE_KEY_LEFT",
                14.2,
                "В 14,2 с кассир покидает помещение; в замке сейфа остаётся ключ.",
                start=14.2,
                end=25.9,
                confidence=0.78,
            ),
            _violation(
                job,
                3,
                "PC_UNLOCKED",
                18.0,
                "В 18,0 с кассир выходит из кассы; экран ноутбука остаётся "
                "включённым и не заблокирован.",
                start=18.0,
                end=25.9,
                vlm_status="unverified",
                kinds=("event", "zoom"),
            ),
        ],
        timings={"perception": 17.0, "rules": 0.6, "vlm": 15.2, "total": 44.9},
    )

    job = "0" * 31 + "5"
    ex["05_done_partial"] = UiJobView(
        job_id=job,
        status=JobStatus.DONE,
        pipeline_mode="full",
        created_at=CREATED,
        elapsed_sec=231.0,
        perception_level=PerceptionLevel.FAST,
        result_level=ResultLevel.RULES,
        stages=_stages(
            StageCode.DONE,
            skipped={StageCode.VLM: StageStatus.SKIPPED},
            t={StageCode.PERCEPTION: 168.0},
        ),
        degradations=[
            UiDegradation(
                stage="perception",
                reason_ru="Ускоренный режим распознавания: длинное видео и очередь.",
            ),
            UiDegradation(
                stage="vlm",
                reason_ru="Проверка Qwen пропущена: не хватило времени до срока ответа.",
            ),
        ],
        video=_video(job),
        verdict="violation",
        violations=[
            _violation(
                job,
                1,
                "OTHER_VISIBLE_IRREGULARITY",
                52.4,
                "В 52,4 с наблюдается отклонение от обычного хода операции: посторонний предмет "
                "закрывает рабочую зону кассира.",
                vlm_status="skipped",
                confidence=0.55,
                kinds=("event", "context"),
                legal_note_ru="Конкретная норма для этого наблюдения не установлена; "
                "указан акт целиком "
                "(FAQ 31). Требуется проверка человеком.",
            ),
        ],
        timings={"perception": 168.0, "rules": 0.9, "total": 231.0},
    )

    job = "0" * 31 + "6"
    ex["06_failed"] = UiJobView(
        job_id=job,
        status=JobStatus.FAILED,
        pipeline_mode="full",
        created_at=CREATED,
        elapsed_sec=2.1,
        stages=[
            StageState(code=StageCode.QUEUED, status=StageStatus.DONE, progress=1.0),
            StageState(
                code=StageCode.PROBE,
                status=StageStatus.FAILED,
                progress=0.0,
                note_ru="Видео не читается",
            ),
        ],
        error=UiError(
            code="UNSUPPORTED_VIDEO",
            message_ru="Не удалось прочитать видео: файл повреждён или формат не поддерживается. "
            "Загрузите .mov или .mp4 (H.264/HEVC).",
        ),
    )

    job = "0" * 31 + "7"
    ex["07_stub_demo"] = UiJobView(
        job_id=job,
        status=JobStatus.DONE,
        pipeline_mode="stub",
        created_at=CREATED,
        elapsed_sec=0.4,
        perception_level=PerceptionLevel.FULL,
        result_level=ResultLevel.RULES,
        stages=_stages(StageCode.DONE, skipped={StageCode.VLM: StageStatus.SKIPPED}),
        degradations=[
            UiDegradation(
                stage="perception", reason_ru="Демонстрационный режим: AI-модели не подключены."
            )
        ],
        video=UiVideo(url=f"/api/ui/jobs/{job}/video.mp4", duration_sec=2.0, width=160, height=96),
        verdict="violation",
        violations=[
            UiViolation(
                id="stub-event-1",
                code="STUB_EVENT",
                title_ru="Демонстрационное событие (stub)",
                timecode_sec=0.0,
                start_sec=0.0,
                end_sec=0.0,
                confidence=1.0,
                vlm_status="skipped",
                provisional=False,
                description_ru="Синтетический тестовый сценарий. AI-анализ и оценка "
                "реального нарушения не выполнялись.",
                legal=[],
                legal_note_ru="Демонстрационный режим: нормативное основание не определяется.",
                evidence=_evidence(job, 1, 0.0, ("event",)),
            )
        ],
    )
    return ex


def render() -> dict[Path, str]:
    return {
        OUT_DIR / f"{name}.json": json.dumps(
            view.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        + "\n"
        for name, view in build_examples().items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    files = render()
    stale = [p for p, t in files.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
    if args.check:
        for p in stale:
            print(f"устарел: {p.relative_to(ROOT)}", file=sys.stderr)
        return 1 if stale else 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for p in stale:
        p.write_text(files[p], encoding="utf-8", newline="\n")
    print(f"записано {len(stale)} из {len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
