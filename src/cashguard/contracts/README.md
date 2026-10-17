# `src/cashguard/contracts/` — контракты

> **Назначение:** Pydantic v2 модели и протоколы, общие для API, воркеров, оценки и web. Единственный источник правды о формах данных.
> **Владелец:** S0 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `jury.py` | ЗАМОРОЖЕН: `AnalyzeAccepted`, `JuryEvidenceFrame`, `JuryViolation`, `JuryJobResponse` — ровно ТЗ §10 |
| `ui.py` | `UiJobView`, `UiViolation`, `UiSignal`, `UiCatalog` → OpenAPI → типы web |
| `jobs.py` | `JobStatus`, `JobStage`, `JobSource`, `PerceptionLevel`, `ResultLevel`, `ErrorCode`, `DeadlineTiers`, `Budget`, `Degradation`, `StageState` |
| `video.py` | `VideoMeta`, `SampledFrame` (не сериализуется), `FrameRef`, `FrameIndex` |
| `perception.py` | `DetClass` (14 классов в порядке `training/configs/classes.yaml`), `BBox`, `Detection`, `PoseKeypoints`, `CropState`, `FrameObservation`, `PerceptionPlan`, `PerceptionTrace` |
| `scene.py` | `Zone`, `SceneLayout`, `TrackRole`, `ServiceSession`, `StateInterval`, `SceneEvent`, `SceneModel` |
| `findings.py` | `RuleContext`, `Candidate`, `Signal`, `EvidenceRequest`, `VlmQuestion`, `VlmAnswer`, `KeyframeFinding`, `VlmReview`, `Decision`, `ViolationFinding`, `SignalFinding`, `AnalysisResult` |
| `catalog.py` | `ViolationCode`, `SignalCode`, `LegalStrength`, `LegalRef`, `TimecodePolicy`, `CatalogEntry`, `ActEntry`, `Acts` |
| `health.py` | `LiveReport`, `ReadyReport`, `AdminStatus` |
| `ports.py` | Protocol: `VideoSource`, `PerceptionBackend`, `FrameStore`, `Rule`, `SignalRule`, `VlmBackend`, `ProgressFn`, `Clock` |
| `export.py` | `python -m cashguard.contracts.export [--check]`: JSON Schema + `web/src/shared/api/openapi.json` |
| `schemas/jury_job.schema.json` | ЗАМОРОЖЕН, написан руками по ТЗ §10, `additionalProperties: false`; оракул тестов |
| `schemas/generated/*.schema.json` | [GEN] вывод `export.py`, руками не править |

## Правила

- Модели frozen, `extra='forbid'`. Поле `schema_version` — только у сохраняемых артефактов (`PerceptionTrace`, `AnalysisResult`, базовые линии eval). У моделей `jury.py` (`AnalyzeAccepted`, `JuryEvidenceFrame`, `JuryViolation`, `JuryJobResponse`) и у `UiJobView` его нет: форма jury JSON — ровно ТЗ §10, лишний ключ отклоняет замороженная схема.
- `catalog.py` содержит корневую модель `Acts{acts: list[ActEntry], do_not_cite: list[str]}`; у `ActEntry` есть поле `cite_ru` — краткая ссылка на акт для правовой фразы ([docs/legal_sources.md](../../../docs/legal_sources.md), раздел 6.2).
- `Degradation` (`jobs.py`): `perception_fast`, `perception_min`, `perception_truncated`, `vlm_deadline`, `vlm_memory`, `vlm_backlog`, `vlm_crash`, `vlm_disabled`, `deadline_finalize` ([ADR-0005](../../../docs/adr/0005-deadline-tiers-provisional-degradation.md), п. 8).
- Пакет ничего внутреннего не импортирует. Не импортировать torch, ultralytics, coremltools, mlx, mlx_vlm, cv2 (проверяет import-linter в `uv run just check`).
- Любое изменение — отдельный маленький PR к S0, мержится первым; зависимые ветки делают rebase.
- Схему жюри меняет только человек.
- Инварианты ответа жюри: `done` ⇒ `verdict` не null и `violation` ⇔ нарушений ≥ 1; `processing`/`failed` ⇒ `verdict: null`, `violations: []`; `timecode_sec = round(pts_event, 2)` и все `timestamp_sec = round(pts, 2)` одной функцией, поэтому `evidence_frames[0].timestamp_sec == timecode_sec` точно; 0 ≤ `timecode_sec` ≤ длительность; не больше 5 нарушений по времени; лишних ключей нет.

## Документы

- [docs/architecture.md — процессы, режимы, поток данных](../../../docs/architecture.md)
- [docs/api.md — API жюри и интерфейса](../../../docs/api.md)
- [docs/adr/ — архитектурные решения](../../../docs/adr/)
