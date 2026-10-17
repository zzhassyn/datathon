# `src/cashguard/jobs/` — очередь и состояние задач

> **Назначение:** SQLite в режиме WAL — единственная очередь и хранилище состояния (ADR-0003).
> **Владелец:** S1 · **Статус:** P0-S1-01 реализован, 17.10.2026; `JobRecord` пока внутренний тип, ожидается `contracts/jobs.py` от S0.

## Реализованные файлы

| Файл | Что делает |
|---|---|
| `db.py` | подключение: WAL, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`, `isolation_level=None`; миграции через `PRAGMA user_version`; таблицы `jobs`, `job_events`, `workers`, `result_cache`, `perf_stats` |
| `repo.py` | `create_job`, `link_job`, `claim_next`, `beat_alive`, `beat_progress`, `report_progress`, `set_provisional`, `finalize`, `requeue_dead`, `finalize_overdue`, `fail`, `queue_position`, `queue_depth` |
| `cache.py` | кэш результатов по (`sha256`, `pipeline_version`, `config_hash`), только для уровня `full` |
| `perfstats.py` | EWMA стоимости этапов (`perception_sec_per_video_sec@full\|fast\|min`, `vlm_group_call_sec`, `keyframe_pass_sec`) |
| `retention.py` | удаление папок задач старше 24 ч |

## Интерфейс и восстановление

- `JobRepository(path)` создаёт/мигрирует SQLite. Соединения локальны для потока и процесса.
- `create_job(...)` возвращает отдельный `JobRecord` для каждого запроса; `get_job(id)`
  читает его, `resolve_job(id)` разрешает ссылку на каноническую задачу.
- `claim_next(owner, pid=None, stage='perception')` атомарно захватывает одну задачу.
  Приоритет жюри — 0, UI — 1. Количество попыток увеличивается при claim.
- `finalize(..., lease_owner=owner, jury_path=..., verdict=..., n_violations=...)`
  меняет только активную задачу с действующей арендой. Повторный вызов ничего не меняет.
  Опубликованные файлы должны существовать до перехода. Воркер публикует файлы
  в отдельном каталоге попытки: проигравший CAS не может перезаписать готовый результат.
- `beat_alive` и `beat_progress` независимы. Записи progress ограничены 2/с.
  `register_worker`, `worker_heartbeat`, `workers` обслуживают supervisor и readiness.
- `requeue_dead` перевыдаёт только задачи подтверждённо мёртвого PID. У живого
  зависшего процесса supervisor сначала проверяет личность и завершает процесс.
  Это уточнение ADR-0003 предотвращает параллельную работу двух владельцев при
  задержанном heartbeat. Perception получает максимум две попытки, вторая — `fast`.
- `set_provisional` публикует промежуточный результат; `finalize_overdue` завершает
  его после `finalize_at`. Если результата нет к t0+250, задача `failed` с
  `DEADLINE_EXCEEDED`: отсутствие доказательств не превращается в чистый вердикт.
- Дедупликация проверяет SHA256, версию пайплайна и хэш конфигурации. Готовый кэш
  используется только для `full`. Связанные задачи не запускают второй inference.
  При финализации им назначаются пути родителя; API материализует ответ с собственным
  `job_id`, не меняя канонический результат. Ошибка родителя передаётся связанным задачам.
- `purge_expired` удаляет только завершённые задачи. Родитель сохраняется, пока есть
  ссылки. Сначала коммитится удаление строк, затем удаляются каталоги; прерванная
  очистка подбирает осиротевшие каталоги. `retention_hours=0` выключает очистку.

## Проверки

`PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core tests/unit/jobs tests/jobs -q`
проверяет реальные гонки двух `spawn`-процессов, идемпотентность финализации,
WAL и миграции, перезапуск, аренды, дедлайны, кэш, очистку и изоляцию путей.
Общий `uv run just check` зависит от ещё не опубликованного каркаса S0.

## Правила

- Захват задачи: `BEGIN IMMEDIATE … UPDATE … RETURNING`.
- Каждый переход — compare-and-set (`UPDATE … WHERE id=? AND status!='done' …`); `rowcount == 1` значит «этот вызов выиграл». Поэтому финализация безопасна из воркера, sweeper и GET.
- Без ORM: stdlib `sqlite3`. SQLite ≥ 3.35 нужен для `RETURNING` (проверить на Mac).
- Не импортировать torch, ultralytics, coremltools, mlx, mlx_vlm, cv2 (проверяет import-linter в `uv run just check`).

## Документы

- [docs/architecture.md — процессы, режимы, поток данных](../../../docs/architecture.md)
- [docs/adr/ — архитектурные решения](../../../docs/adr/)
