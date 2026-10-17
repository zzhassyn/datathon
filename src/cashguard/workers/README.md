# `src/cashguard/workers/` — супервизор и воркеры

> **Назначение:** Процессы `perception` и `vlm`, общий цикл воркера и кроссплатформенный супервизор `cashguard up` (ADR-0002).
> **Владелец:** S1; `vlm_worker.py` — S6 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

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
