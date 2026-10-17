# `src/cashguard/jobs/` — очередь и состояние задач

> **Назначение:** SQLite в режиме WAL — единственная очередь и хранилище состояния (ADR-0003).
> **Владелец:** S1 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `db.py` | подключение: WAL, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`, `isolation_level=None`; миграции через `PRAGMA user_version`; таблицы `jobs`, `job_events`, `workers`, `result_cache`, `perf_stats` |
| `repo.py` | `create_job`, `link_job`, `claim_next`, `beat_alive`, `beat_progress`, `report_progress`, `set_provisional`, `finalize`, `requeue_dead`, `finalize_overdue`, `fail`, `queue_position`, `queue_depth` |
| `cache.py` | кэш результатов по (`sha256`, `pipeline_version`, `config_hash`), только для уровня `full` |
| `perfstats.py` | EWMA стоимости этапов (`perception_sec_per_video_sec@full\|fast\|min`, `vlm_group_call_sec`, `keyframe_pass_sec`) |
| `retention.py` | удаление папок задач старше 24 ч |

## Правила

- Захват задачи: `BEGIN IMMEDIATE … UPDATE … RETURNING`.
- Каждый переход — compare-and-set (`UPDATE … WHERE id=? AND status!='done' …`); `rowcount == 1` значит «этот вызов выиграл». Поэтому финализация безопасна из воркера, sweeper и GET.
- Без ORM: stdlib `sqlite3`. SQLite ≥ 3.35 нужен для `RETURNING` (проверить на Mac).
- Не импортировать torch, ultralytics, coremltools, mlx, mlx_vlm, cv2 (проверяет import-linter в `uv run just check`).

## Документы

- [docs/architecture.md — процессы, режимы, поток данных](../../../docs/architecture.md)
- [docs/adr/ — архитектурные решения](../../../docs/adr/)
