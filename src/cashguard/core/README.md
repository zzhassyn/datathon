# `src/cashguard/core/` — общая инфраструктура

> **Назначение:** Настройки, логи, пути, идентификаторы, сроки, атомарная запись, офлайн-окружение. Без ML.
> **Владелец:** S1 · **Статус:** P0-S1-01 реализован, 17.10.2026; адаптация к Python-контрактам S0 ожидается.

## Реализованные файлы

| Файл | Что делает |
|---|---|
| `settings.py` | pydantic-settings, префикс `CG_`, ключи как `SecretStr`; `CG_DATA_DIR` и совместимый alias `CG_VAR_DIR`, изолированные runtime-пути |
| `logging.py` | structlog JSON lines, `RotatingFileHandler` 10 МБ × 5 на процесс, `job_id` и `stage` через contextvars |
| `paths.py` | пути `var/`, `models/`, `web/dist`; проверка `is_relative_to` |
| `ids.py` | идентификаторы задач: uuid4, 32 hex |
| `budget.py` | Внутренние `DeadlineTiers`, `Budget`, `load_deadline_offsets`: сроки от t0, валидация `configs/deadlines.yaml` |
| `atomic.py` | запись файла: tmp → fsync → `os.replace` |
| `memory.py` | свободная память и RSS через psutil |
| `env.py` | `apply_offline_env`: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `YOLO_OFFLINE=1`, `YOLO_AUTOINSTALL=false` |
| `versions.py` | `config_hash` (sha256 нормализованного словаря конфигурации) и `pipeline_version` (git sha + хэш переданного манифеста моделей) |

## Использование и границы

`get_settings()` читает env и `CG_ENV_FILE` (по умолчанию `~/.config/cashguard/.env`).
Прямой `Settings(...)` подходит для изолированных тестов без чтения пользовательского файла.
`settings.paths.ensure()` создаёт только рабочие папки. Ключи задаются как
`CG_API_KEYS=jury:<token>,team:<token>`; значения нельзя логировать.

`Budget(job.tiers)` сохраняет общие wall-clock дедлайны и считает оставшееся время
по monotonic. Загрузка уже входит в бюджет. Значения 150/180/210/225/235 секунд
являются оценками ADR-0005, а не измерением скорости моделей.

`load_deadline_offsets(path)` проверяет YAML; `JobRepository(..., deadlines_path=path)`
применяет его при создании заданий. Пока отсутствуют общие модели конфигурации S0,
проверка остальных YAML-конфигов не реализована. Новые зависимости не добавлялись:
используются утверждённые `pydantic-settings`, `structlog`, `psutil`, `PyYAML`.

## Правила

- Не импортировать torch, ultralytics, coremltools, mlx, mlx_vlm, cv2 (проверяет import-linter в `uv run just check`).
- Сроки между процессами — wall-clock epoch, внутри процесса — monotonic.
- Логи никогда не содержат ключи API, base64 и кадры.

## Документы

- [docs/architecture.md — процессы, режимы, поток данных](../../../docs/architecture.md)
- [docs/adr/ — архитектурные решения](../../../docs/adr/)
