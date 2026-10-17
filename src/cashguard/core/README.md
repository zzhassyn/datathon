# `src/cashguard/core/` — общая инфраструктура

> **Назначение:** Настройки, логи, пути, идентификаторы, сроки, атомарная запись, офлайн-окружение. Без ML.
> **Владелец:** S1 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `settings.py` | pydantic-settings, префикс `CG_`, ключи как `SecretStr`; проверка YAML из `configs/` при старте |
| `logging.py` | structlog JSON lines, `RotatingFileHandler` 10 МБ × 5 на процесс, `job_id` и `stage` через contextvars |
| `paths.py` | пути `var/`, `models/`, `web/dist`; проверка `is_relative_to` |
| `ids.py` | идентификаторы задач: uuid4, 32 hex |
| `budget.py` | `DeadlineTiers`, `Budget`: сроки от t0 из `configs/deadlines.yaml` |
| `atomic.py` | запись файла: tmp → fsync → `os.replace` |
| `memory.py` | свободная память и RSS через psutil |
| `env.py` | `apply_offline_env`: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `YOLO_OFFLINE=1`, `YOLO_AUTOINSTALL=false` |
| `versions.py` | `config_hash` (sha256 нормализованного YAML) и `pipeline_version` (git sha + хэш манифеста моделей) |

## Правила

- Не импортировать torch, ultralytics, coremltools, mlx, mlx_vlm, cv2 (проверяет import-linter в `uv run just check`).
- Сроки между процессами — wall-clock epoch, внутри процесса — monotonic.
- Логи никогда не содержат ключи API, base64 и кадры.

## Документы

- [docs/architecture.md — процессы, режимы, поток данных](../../../docs/architecture.md)
- [docs/adr/ — архитектурные решения](../../../docs/adr/)
