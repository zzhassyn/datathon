# CashGuard — frontend

Минималистичный интерфейс из `codex/forte-frigate-ui`, перенесённый в `web/`. Разработка — ветка `codex/frontend-integration`; backend не изменяется.

## Запуск

Node >=24.15 <25, pnpm 10.34.7.

```sh
pnpm install --frozen-lockfile
pnpm dev
```

Прокси вычисляет порт по пути worktree как `scripts/port.py`. Для backend в другом checkout создайте `web/.env.local` с `CG_API_ORIGIN=http://127.0.0.1:8127`. Значение используется только Vite, браузер всегда обращается к своему origin.

## Состояние контракта

В main 41d7620 отсутствуют `contracts/ui.py` и экспортёр. S1 a46acdb отдаёт не типизированные JSON-ответы. `sync:api` сохраняет реальную OpenAPI из локального backend; `gen:api` генерирует TypeScript. `decode.ts` — временный валидирующий адаптер ответа, с выведенными типами представления. Он не заменяет согласованный контракт S0. После его публикации адаптер следует заменить импортами generated components и проверить drift.

Нормы, идентификаторы событий и метаданные кадров в текущем S1 отсутствуют. UI не сочиняет их. Для отсутствующего основания используется «Норма не указана».

## Проверки

```sh
pnpm gen:api
pnpm typecheck
pnpm lint
pnpm test
pnpm build
```

`gen:api` должен оставлять `schema.d.ts` без изменений. До готовности S0 это проверка воспроизводимости снимка S1 a46acdb, не проверка финальных моделей контрактов. Корневой `uv run just check` пока недоступен на базе main 41d7620: отсутствуют общий `justfile` и Python packaging.

Для E2E нужен запущенный S1 backend в stub-режиме и синтетические MOV. После объединения backend/frontend в одном checkout:

```sh
# Из web/, Python — из подготовленного backend-окружения:
python ../scripts/make_synthetic_mov.py e2e/fixtures/clean.mov --duration 12 --verdict clean
python ../scripts/make_synthetic_mov.py e2e/fixtures/violation.mov --duration 12 --verdict violation
pnpm build
# После первой сборки запустите/перезапустите backend: он монтирует assets при старте.
pnpm e2e --project=chrome-desktop --project=chrome-mobile
pnpm exec playwright install webkit
pnpm e2e --project=webkit --project=iphone
```

Chrome должен быть установлен. Адрес тестового стенда задаётся `CG_E2E_BASE_URL` (по умолчанию `http://127.0.0.1:8127`). Тесты многократно загружают синтетику: запускайте их на отдельном локальном стенде с повышенным `CG_UI_RATE_UPLOADS`, не на публичном сервисе в окно жюри. Данные стенда должны храниться в отдельном `CG_DATA_DIR`. Видео, traces, скриншоты и browser artifacts игнорируются Git.

Если frontend и backend находятся в разных checkout, backend запускается с `CG_WEB_DIST`, указывающим на абсолютный путь к `web/dist` этого frontend. Код backend для этого менять не нужно.

## Что подключить интегратору

1. Объединить утверждённый контракт S0 раньше зависимых UI-изменений. Экспортировать `UiJobView`, `UiViolation`, `UiEvidence`, `UiLegalRef`, `UiCatalog`, `AnalyzeAccepted`, `ReadyReport` и multipart request в OpenAPI.
2. Заменить временный `decode.ts` на импорт generated components, сохранив проверки безопасных URL/ответов на границе. `openapi.json` и `schema.d.ts` не редактировать руками.
3. Добавить стабильные `violations[].id`, проверенные `legal[]`, `evidence[].kind/width/height`, реальный каталог. UI уже показывает эти поля, если они приходят. Пока каталог пуст, UI не заявляет список поддерживаемых нарушений.
4. Подключить реальные модели через backend. На фронте нет выбора модели и вычисления вердикта. Необходимо проверить реальное видео, несколько событий, источники и время ответа вместе с ML-командой.
5. Проверить HEVC и поворот: интерфейс ожидает совместимый серверный H.264 preview. Текущий S1 только отмечает необходимость перекодирования.
6. Проверить итоговую раздачу статики и API с одного origin, production-настройки лимитов и retention. Демо-ролики из банковского кейса в этой ветке не публикуются.

Загрузка работает через XHR, GET — через openapi-fetch/TanStack Query. Таймаут одного GET 15 секунд; опрос возобновляется при сбое. Ни jury-ключа, ни вызовов `/api/v1/*`, ни облачных AI-вызовов в исполняемом UI-коде нет. К4 считается пройденной только после проверки реальных моделей; stub и browser fixtures её не заменяют.
