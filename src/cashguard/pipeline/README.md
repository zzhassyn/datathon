# `src/cashguard/pipeline/` — пайплайн обработки видео

> **Назначение:** Этапы от проверки файла до готовых `jury.json` и `ui_result.json`. Оборудование спрятано за портами из `contracts/ports.py`.
> **Владелец:** S0 (`runner.py`), S1 (`stub.py`), подпапки — по таблице · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `runner.py` | [S0] оркестрация: `run_perception_stage(job, budget)`, `run_vlm_stage(job, budget)`; режимы `stub\|replay\|full`; сигнатуры этапов заморожены в P0 |
| `stub.py` | [S1] детерминированный результат с задержкой: подсказка в имени файла → violation или clean |

## Подпапки

| Папка | Владелец | Функция этапа |
|---|---|---|
| `ingest/` | S1 | `ingest.probe(path) -> VideoMeta`, `ingest.make_preview(path, meta, out) -> Path \| None` |
| `decode/` | S3 | `VideoSource`, `FrameStore` |
| `perception/` | S3 | `perception.run(path, meta, plan, backend, store, progress) -> PerceptionTrace` |
| `scene/` | S4 | `scene.build(trace, cfg) -> SceneModel` |
| `rules/` | S0 → S4, S5, S6 | `rules.evaluate_all(ctx) -> tuple[list[Candidate], list[Signal]]` |
| `verdict/` | S5 | `verdict.decide(cands, review \| None, cfg) -> list[Decision]` |
| `vlm/` | S6 | `vlm.plan_questions(decisions, store, cfg) -> list[VlmQuestion]` |
| `evidence/` | S3 | `evidence.render(decisions, store, out_dir) -> dict[str, list[EvidenceImage]]` |
| `legal/` | S5 | `legal.describe(decision, scene, catalog) -> tuple[str, list[LegalRef]]` |
| `render/` | S1 | `render.jury(result, evidence_dir) -> JuryJobResponse`, `render.ui(result)` |

## Правила

- `scene`, `rules`, `verdict`, `legal`, `render` — чистый Python + numpy.
- Режим задаёт `CG_PIPELINE_MODE`, бэкенды — `CG_VISION_BACKEND` (`coreml|torch|replay|stub|null`) и `CG_VLM_BACKEND` (`mlx|transformers|replay|null`).
- Сначала всегда пишется полный результат по правилам (provisional); VLM его только уточняет.

## Документы

- [docs/pipeline.md — этапы обработки](../../../docs/pipeline.md)
- [docs/architecture.md — процессы, режимы, поток данных](../../../docs/architecture.md)
- [docs/adr/ — архитектурные решения](../../../docs/adr/)
