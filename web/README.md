# CashGuard — frontend

Минималистичный тёмный интерфейс Frigate/Forte. Интеграционная ветка:
`codex/fullstack-integration` (содержит backend 42d44d7 и frontend 205d7c9).
Старые ветки сохранены. В main изменения не объединены.

## Запуск целиком

Python 3.12, uv 0.13.1, Node >=24.15 <25, pnpm 10.34.7. Из корня:

```sh
uv sync --locked
pnpm -C web install --frozen-lockfile
uv run just build-web
CG_PIPELINE_MODE=stub uv run just dev 8131
```

Открыть http://127.0.0.1:8131/. В PowerShell переменная задаётся отдельно:
`$env:CG_PIPELINE_MODE='stub'`, затем `uv run just dev 8131`.
Статика и API работают с одного origin. Vite нужен только для разработки:
`pnpm -C web dev`; `web/.env.local` может содержать
`CG_API_ORIGIN=http://127.0.0.1:8131` для прокси.

## Контракт и проверки

Источник — `src/cashguard/contracts/ui.py`, примеры — `tests/fixtures/ui/*.json`.
`decode.ts` использует сгенерированные типы и проверяет ответ при получении:
job_id, статусы, вердикт, безопасные URL. Нормы и события приходят с сервера.

```sh
uv run just schemas
pnpm -C web gen:api
uv run just check
uv run just build-web
```

`just check` включает Python, lint-imports, pytest, экспорт схем, Biome, TypeScript,
Vitest и `check:api` (проверка дрейфа generated types без перезаписи файла).

## Сквозные проверки

Только на отдельном локальном стенде с синтетикой, не во время проверки жюри:

```sh
uv run python scripts/make_synthetic_mov.py web/e2e/fixtures/clean.mov --duration 12 --verdict clean
uv run python scripts/make_synthetic_mov.py web/e2e/fixtures/violation.mov --duration 12 --verdict violation
pnpm -C web exec playwright install webkit
# После build запустить backend с отдельным CG_DATA_DIR, CG_PIPELINE_MODE=stub,
# CG_UI_RATE_UPLOADS=200 и CG_UI_RATE_QUEUE=50.
CG_E2E_BASE_URL=http://127.0.0.1:8131 uv run just e2e
```

Chrome должен быть установлен. Проекты: chrome-desktop, chrome-mobile, webkit,
iphone. Видео и браузерные отчёты в Git не попадают. Тесты с `page.route` проверяют
состояния UI; они не заменяют прогон реального пайплайна.

## Готовность

K0–K3: загрузка XHR с прогрессом/отменой, очередь, опрос и восстановление по job_id,
H.264-превью, события, четырёхсекундный контекст, кадры, источники, ошибки и повторы.
Выбранное событие сохраняется по ID при обновлении предварительного списка.
Контекст воспроизводится в основном плеере; отдельного файла мини-клипа нет.

K4 остаётся открытой. Подготовлена передача `AnalysisResult` через backend в UI,
но в `pipeline/runner.py` ещё нет сборки реальных стадий. Не подключены распознавание,
сцена/правила/вердикт, VLM и веса. Запуск `full`/`replay` пока намеренно отклоняется.
Снятие запрета допустимо после реализации стадий и сквозной проверки на отложенных
видео. Stub явно обозначен; неизвестный реальный ролик в нём получает `failed`.

Ни ключа жюри, ни вызовов `/api/v1/*`, ни облачных AI в UI нет. GET — openapi-fetch
и TanStack Query, загрузка — XHR. Демо на главной — иллюстрированная синтетическая
камера из исходного дизайна, отдельно от результатов загрузки.
