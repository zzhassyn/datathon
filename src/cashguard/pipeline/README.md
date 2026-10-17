# `src/cashguard/pipeline/` — пайплайн обработки видео

> **Назначение:** Этапы от проверки файла до готовых `jury.json` и `ui_result.json`. Оборудование спрятано за портами из `contracts/ports.py`.
> **Владелец:** S0 (`runner.py`), S1 (`stub.py`), подпапки — по таблице · **Статус:** 18.10.2026, первый backend-срез K4; реальные адаптеры ещё не подключены.

`runner.run_perception_stage` исполняет замороженные порты и возвращает `AnalysisResult`.
`runtime.bind_stages` добавляет к `StageSet` фабрику ресурсов задачи: backend, FrameStore,
план, конфиги, каталог, версии и progress. `stage_factory(mode, factory)` регистрирует
набор на срок жизни **конкретного spawned worker**; режим не подменяется stub.
`load_stages` без зарегистрированной фабрики завершается ошибкой. Глобальные API/supervisor
guards `full`/`replay` сохраняются: наличие оркестратора не означает готовность моделей.

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `runner.py` | [S0] оркестрация: `run_perception_stage(job, budget)`, `run_vlm_stage(job, budget)`; режимы `stub\|replay\|full`; сигнатуры этапов заморожены в P0 |
| `runtime.py` | [S0] явные фабрики режимов, `StageResources`, привязка прогресса worker |
| `execution.py` | [S0] исполнение стадий, проверенный результат и атомарные артефакты |
| `validation.py` | [S0] идентичность трассы, полнота выбранных кадров, PTS, сцена и решения |
| `stub.py` | [S1] детерминированный результат с задержкой по метатегу `cg-stub:clean|violation` |

Прежде чем сохранить `analysis.json`, runner проверяет JPEG через существующий адаптер
`render_analysis` и сопоставляет с ним jury/UI-рендер портов. Основной evidence привязывает
таймкод к реальному кадру PTS; интервал расширяется, если ближайший кадр чуть раньше якоря.
Трасса обязана покрыть все выбранные кадры из `FrameStore.index()`; индекс должен содержать
именно поток sampled-кадров текущего perception-прохода. Начало/конец проверяются с допуском
одного периода сэмплирования плюс одного среднего периода исходного кадра. Неполная трасса
завершается ошибкой: контракт пока не позволяет честно обозначить её усечение.

Артефакты попытки: `trace.json.gz`, `scene.json`, `candidates.json`, `decisions.json`,
`evidence/`, `jury.json`, `ui_result.json`, затем `analysis.json` последним. Публикация в
SQLite остаётся у worker и защищена lease/CAS. `run_vlm_stage` ещё не реализована.

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
