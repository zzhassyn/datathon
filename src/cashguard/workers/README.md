# `src/cashguard/workers/` — супервизор и воркеры

> **Назначение:** Процессы `perception` и `vlm`, общий цикл воркера и кроссплатформенный супервизор `cashguard up` (ADR-0002).
> **Владелец:** S1; `vlm_worker.py` — S6 · **Статус:** synthetic stub работает; real/replay runner ожидается от S0.

## Рабочий запуск

При наличии окружения и `PYTHONPATH=src`: `python -m cashguard.cli up --profile dev`.
Supervisor поднимает API с reload и один `--role all` в свежих интерпретаторах.
Для стабильного smoke без reload: `python -m cashguard.cli up --profile prod --port 8100`.
Ключи берутся из `CG_API_KEYS` или `CG_ENV_FILE`; модельные импорты в API отсутствуют.

`CG_PIPELINE_MODE=stub` принимает только синтетические видео с метатегом
`comment=cg-stub:clean|violation`, созданные `scripts/make_synthetic_mov.py`.
Имя файла не влияет на исход. Видео без метатега завершается ошибкой
`UNSUPPORTED_VIDEO`; это не реальное обнаружение нарушений. `full` и `replay`
пока завершаются явной ошибкой: ports опубликованы, реализации runner/стадий ещё нет.

Воркеры используют отдельные liveness/progress heartbeat, сохраняют результат
в `jobs/<id>/attempt-<uuid>/`, затем публикуют путь атомарным CAS в SQLite.
У каждой попытки свои файлы: запоздавший воркер не перезапишет победивший
результат. UI evidence нужно искать относительно `ui_result_path`, а не
предполагать путь `jobs/<id>/evidence/`.

При SIGKILL supervisor повторяет обработку; всего допускается две попытки.
Результат stub имеет `result_level=rules` и не помещается в full-result cache.
`CG_STUB_DELAY_SEC` задаёт искусственную задержку для проверки сбоев; она
не измеряет скорость будущих YOLO/Qwen. Дедлайны отсчитываются от времени POST.

Проверка: `PYTHONPATH=src python -m pytest tests/workers -q` (synthetic-only).
Она включает настоящий SIGKILL, восстановление до `done`, предел двух попыток,
проверку JPEG, неправильного метатега и истечения бюджета.

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `__main__.py` | `python -m cashguard.workers --role perception\|vlm\|all` |
| `base.py` | цикл: опрос каждые 250 мс, захват, heartbeat, обработка SIGTERM |
| `perception_worker.py` | одна задача за раз, приоритет jury 0, ui 1; самоперезапуск при RSS > 3 ГБ или после 200 задач |
| `warmup.py` | прогрев каждой модели до состояния `idle`; `/ready` ждёт прогрева |
| `supervisor.py` | ~150 строк stdlib + psutil: запуск api → perception → vlm, рестарт с backoff 1, 2, 4 … 30 с, SIGKILL зависших |
| `vlm_worker.py` | [S6] Qwen3.5-4B на главном потоке, `CG_VLM_QUANT=auto` (8 → 6 → 4 bit по свободной памяти) |

## Правила

- Дочерние процессы — свежие интерпретаторы `subprocess.Popen([sys.executable, '-m', ...])`. Никакого fork: Metal и ObjC не fork-safe. Каждая точка входа вызывает `multiprocessing.set_start_method('spawn')`.
- Два heartbeat: `alive_at` из отдельного потока каждые 2 с, `progress_at` из главного цикла на каждом кадре или шаге.
- Супервизор убивает воркер при `alive_at` старше 20 с или `progress_at` старше 60 с (занят) / 300 с (прогрев), предварительно проверив cmdline pid.
- Супервизор не импортирует web- и ML-библиотеки. `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` не использовать.

## Документы

- [docs/architecture.md — процессы, режимы, поток данных](../../../docs/architecture.md)
- [docs/runbook_mac.md — эксплуатация Mac](../../../docs/runbook_mac.md)
- [docs/adr/ — архитектурные решения](../../../docs/adr/)


## Подготовка K4

`process_pipeline` вызывает frozen `load_stages/run_perception_stage`, затем
`publish_analysis`. Публикация типизированного `AnalysisResult` проверена на синтетике:
идентичность задания/sha256, безопасные JPEG, несколько событий, сигналы, нормы,
неизменяемая attempt-папка и lease/CAS. Guards full/replay сохранены.
Остались реальные стадии, передача прогресса, очередь VLM и прогрев; подробнее
`scripts/README_BACKEND_HANDOFF.md`, раздел 8. Это не готовый AI-пайплайн.
