# ADR-0003. SQLite в режиме WAL — единственная очередь и хранилище состояния

> Назначение: зафиксировать, где и как хранятся задачи, очередь, аренды воркеров и метрики.
> Владелец: S0 (документ); реализует S1 (`jobs/db.py`, `jobs/repo.py`, `api/sweeper.py`). Статус: черновик v1, 17.10.2026.

## Статус

Принято. Проверить: версия SQLite в Python 3.12 от uv на Mac ≥ 3.35 (нужна для `RETURNING`).

## Контекст

- Задача, принятая с ответом 200, не должна теряться при рестарте API, воркера или ОС ([ADR-0004](0004-auth-before-body-streaming-upload.md)).
- На Mac 16 ГБ, на пике должно оставаться ≥ 3 ГБ свободно. Каждый лишний демон съедает память.
- Три процесса на одном хосте ([ADR-0002](0002-three-process-runtime-supervisor.md)) должны видеть одну очередь.
- Читатели частые: UI опрашивает раз в секунду, воркеры — каждые 250 мс.
- Финализацию задачи могут одновременно попытаться сделать воркер, sweeper и ленивый GET ([ADR-0005](0005-deadline-tiers-provisional-degradation.md)). Нужна идемпотентность.

## Решение

1. **stdlib `sqlite3`**, файл `var/db/cashguard.sqlite3`.
   - PRAGMA: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`.
   - `isolation_level=None` (транзакции явно).
   - Миграции через `PRAGMA user_version`.
   - В API — thread-local соединения.
2. **Таблицы:**
   - `jobs` — задача целиком: идентичность, состояние, уровни дедлайнов, аренда, видео, результат, ошибки, `timings_json`, `degradations_json`, `pipeline_version`, `config_hash`. Индексы `(stage, status, priority, received_at)`, `(sha256)`, `(status, finalize_at)`;
   - `job_events` — только добавление, не больше 2 записей в секунду на задачу;
   - `workers` — состояние воркеров (`warming|idle|busy|draining`), heartbeat, RSS, память MLX, загруженные модели, квантизация;
   - `result_cache` — PK `(sha256, pipeline_version, config_hash)`;
   - `perf_stats` — EWMA стоимости этапов.
   Полная схема — [architecture.md, раздел 5.2](../architecture.md#52-jobspy-и-sql-jobsdbpy).
3. **Атомарный claim:**
   ```
   BEGIN IMMEDIATE;
   UPDATE jobs SET status='running', lease_owner=?, lease_pid=?, alive_at=?, progress_at=?
    WHERE id=(SELECT id FROM jobs WHERE stage=? AND status='queued'
              ORDER BY priority, received_at LIMIT 1)
   RETURNING *;
   COMMIT;
   ```
4. **Каждый переход — compare-and-set**, например `UPDATE … WHERE id=? AND status!='done' AND (lease_owner=? OR ?)`. `rowcount == 1` — вызов победил. Один и тот же вызов безопасен из воркера, sweeper и ленивого GET.
5. **Аренды с двумя heartbeat:** `alive_at` (отдельный поток, каждые 2 с) и `progress_at` (основной цикл, на каждом шаге). Прогресс в БД — не чаще 2 раз в секунду.
6. **Порядок записи:** файлы задачи пишутся атомарно (tmp → fsync → `os.replace`) **до** смены статуса в БД.
7. Глубина очереди — один `COUNT(*)`. Позиция в очереди — запрос по тому же индексу.
8. `var/` — только на локальной, не синхронизируемой ФС (не iCloud, не OneDrive, не сетевой диск). На Linux — общий bind mount на локальной ФС одного хоста.

## Рассмотренные альтернативы

| Альтернатива | Почему отклонена |
|---|---|
| Celery, RQ или arq с Redis | Лишний демон и память на 16 ГБ машине |
| Huey (SqliteHuey) | Таймауты на SIGALRM не останавливают вызовы Metal |
| Procrastinate с Postgres | Лишний демон |
| SQLAlchemy или SQLModel | Абстракция над пятью таблицами. Агентам понятнее явный SQL с CAS-условиями |
| aiosqlite | Лишняя зависимость. Запросы короткие и идут по индексу |
| `multiprocessing.Queue` | Не переживает рестарт. `qsize()` бросает исключение на macOS |

## Последствия

Плюсы:
- Долговечность при рестартах API, воркеров и ОС без дополнительных демонов и памяти.
- В WAL читатели не блокируют писателя: опросы UI и GET жюри не мешают воркерам.
- Метрики очереди и позиции — простые запросы.

Минусы и цена:
- Один писатель: записи сериализуются. Нужна дисциплина: короткие транзакции, не больше 2 записей прогресса в секунду, `busy_timeout`.
- WAL не работает на сетевых и синхронизируемых ФС. Репозиторий на Mac — `~/cashguard`, не `~/Desktop` и не `~/Documents`.
- Миграции пишутся руками через `PRAGMA user_version`.

Что проверяет решение:
- `tests/jobs`, `tests/workers`: гонка claim, истечение аренды, идемпотентность CAS, `kill -9` посреди задачи.
- `tests/api`: дедупликация и связанные задачи, ленивая финализация.

Связанные решения: [ADR-0002](0002-three-process-runtime-supervisor.md), [ADR-0004](0004-auth-before-body-streaming-upload.md), [ADR-0005](0005-deadline-tiers-provisional-degradation.md). Обзор: [architecture.md, раздел 10](../architecture.md#10-надёжность).
