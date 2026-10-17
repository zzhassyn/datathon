# `src/cashguard/api/` — HTTP-сервис

> **Назначение:** FastAPI-приложение: API жюри `/api/v1`, API интерфейса `/api/ui`, `/health`, `/ready`, раздача `web/dist`.
> **Владелец:** S1 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `main.py` | `create_app()`, lifespan (БД, каталог, sweeper), JSON-обработчики ошибок, GZip (кроме mp4 и jpeg), `/assets` и index |
| `deps.py` | `require_api_key`: sha256 ключа + `hmac.compare_digest` против каждого ключа из `CG_API_KEYS` |
| `upload.py` | потоковый приём: `request.stream()` → streaming-form-data, sha256 на лету, лимит 100 МБ, проверка диска, атомы ISO-BMFF, fsync, `os.replace` |
| `ingest_service.py` | общий приём для жюри и UI: probe, дедупликация по sha256, INSERT задачи, commit |
| `ratelimit.py` | токен-бакет по IP только для UI, два профиля: обычный — 5 загрузок за 10 мин, 2 активные, глобально 10 в очереди; «окно жюри» — 30, 5 и 20 ([docs/api.md](../../../docs/api.md), раздел 6.1) |
| `sweeper.py` | каждые 2 с: просроченные задачи → результат по правилам, мёртвые lease → повтор; раз в час — очистка |
| `views.py` | сборка ответов GET для жюри и UI |
| `preview.py` | remux `preview.mp4` в пуле потоков |
| `routes/` | маршруты по поверхностям API |

## Правила

- Процесс API не импортирует ML-библиотеки; av можно. Перезапуск API никогда не перезагружает модели.
- Обработчик загрузки принимает только `request: Request`: ключ проверяется до первого байта тела. `UploadFile` и `Form` запрещены (тест `tests/api/test_auth_before_body.py`).
- Ошибки — всегда JSON `{"detail": ...}`. Пути с `/` в конце — скрытые алиасы, без 307. POST отвечает 200.
- Привязка только к `127.0.0.1`. IP клиента из `CF-Connecting-IP` — только если пир `127.0.0.1`. `TrustedHostMiddleware` не используется.
- SSE и WebSockets в v1 не делаются.

## Документы

- [docs/api.md — API жюри и интерфейса](../../../docs/api.md)
- [docs/adr/ — архитектурные решения](../../../docs/adr/)
