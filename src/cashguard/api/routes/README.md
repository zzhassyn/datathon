# `src/cashguard/api/routes/` — маршруты

> **Назначение:** Роутеры FastAPI по поверхностям API.
> **Владелец:** S1 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `jury.py` | `/api/v1`: `POST /analyze`, `GET /jobs/{job_id}` (ключ `X-API-Key`, без лимитов, `Cache-Control: no-store`) |
| `ui.py` | `/api/ui`: `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/video.mp4`, `GET /jobs/{id}/frames/{name}`, `GET /catalog`, необязательный `POST /session` |
| `health.py` | `/health` (живость, без I/O) и `/ready` (воркеры, модели, память, диск, очередь) |
| `admin.py` | `/api/v1/admin/status` — только ключ команды, ключ жюри получает 403 |

## Правила

- `job_id` должен совпадать с `^[0-9a-f]{32}$`, иначе 404 JSON.
- `GET /api/v1/jobs/{id}` финализирует просроченную задачу прямо в запросе (lazy finalize).
- Имя кадра: `^\d{1,2}_(event|zoom|context)\.jpg$`, путь проверяется `is_relative_to(job_dir/evidence)`.
- `/api/ui/jobs/{id}/events` (SSE) зарезервирован, в v1 его нет.

## Документы

- [docs/api.md — API жюри и интерфейса](../../../../docs/api.md)
