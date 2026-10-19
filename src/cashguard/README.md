# `src/cashguard/` — пакет сервиса

> **Назначение:** Python-пакет CashGuard: контракты, API, очередь, воркеры, пайплайн, оценка.
> **Владелец:** S0 (структура); подпакеты — по таблице · **Статус:** API, очередь, PT-профиль и числовые правила работают; полный Core ML/VLM-пайплайн не завершён.

## Точки входа пакета

| Файл | Что делает |
|---|---|
| `__init__.py` | пустой, делает пакет импортируемым |
| `cli.py` | [S1] команда `cashguard`: `up`, `api`, `status` |

## Подпакеты

| Папка | Владелец | Что внутри |
|---|---|---|
| `contracts/` | S0 | Pydantic-модели, протоколы, замороженная схема жюри |
| `core/` | S1 | настройки, логи, пути, сроки, атомарная запись |
| `jobs/` | S1 | SQLite WAL: задачи, lease, CAS-переходы, кэш |
| `api/` | S1 | FastAPI: API жюри, API интерфейса, health |
| `workers/` | S1, `vlm_worker.py` — S6 | супервизор и воркеры perception, vlm |
| `pipeline/` | по подпапкам | этапы обработки видео |
| `eval/` | S1 | метрики, LOSO, устойчивость, jury-sim |

## Запуск

- `python -m cashguard.cli up --profile prod|dev` — супервизор.
- `python -m cashguard.workers --role perception|vlm|all` — воркер.
- `python -m cashguard.cli api` — API (обычно запускает супервизор).
- `python -m cashguard.contracts.export [--check]` — JSON Schema и `openapi.json` без импорта ML.

## Правила

- Слои: `api → jobs → core → contracts`, `workers → pipeline → contracts`.
- `api`, `jobs`, `core`, `contracts` без ML-библиотек (av в `api` можно).

## Документы

- [docs/architecture.md — процессы, режимы, поток данных](../../docs/architecture.md)
- [docs/structure.md — дерево, владельцы, имена](../../docs/structure.md)
