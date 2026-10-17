# web/AGENTS.md — правила для агентов в `web/`

Назначение: как работать в `web/` (React SPA CashGuard), чтобы параллельные сессии Claude Code и Codex не ломали сборку, контракт и друг друга.
Владелец: S2 (web). Контракты API — S0, бэкенд `/api/ui` — S1, WebKit-прогоны на Mac — S6.
Статус: черновик v1, 17.10.2026.

## 1. Сначала прочитать

1. Корневой [AGENTS.md](../AGENTS.md): продукт, контракт жюри, общие запреты, порядок мерджа.
2. [docs/ui.md](../docs/ui.md) — спецификация UI: экраны, состояния, тексты, тесты. Это главный документ для этой папки.
3. [docs/api.md](../docs/api.md) — маршруты `/api/ui/*` и `/ready`.

Документация расходится с кодом — правь документацию в том же PR или спроси владельца.

## 2. Что здесь

- `web/` — статическое SPA: React 19 + Vite 8 + TypeScript 5.9.3, Tailwind v4 + shadcn, TanStack Query.
- Сборка `pnpm build` → `web/dist` (в git не попадает). FastAPI раздаёт её с того же origin. Node в проде не работает.
- Роутера нет: экран выбирается по `/?job=<id>` и `/?view=types` (`src/shared/lib/urlState.ts`).
- Владелец всего `web/**`, включая `e2e/`, — S2. Изменения вне `web/` — через владельцев папок.

## 3. Тулчейн

| Что | Версия | Как закреплено |
|---|---|---|
| Node | 24.22.0 LTS | `.node-version`; `engines.node: ">=24.15 <25"`; `.npmrc` с `engine-strict=true`. jsdom 30 требует Node ≥ 24.15: на dev-ПК Node 24.14.1 нужно обновить |
| pnpm | 10.34.7 | поле `packageManager: "pnpm@10.34.7"` в `package.json` |
| typescript | 5.9.3 | точная версия, проверка `tsc -b` отдельно от сборки |
| @biomejs/biome | 2.5.15 | линтер и форматтер, конфиг `biome.json` |

Зависимости — только точные версии (без `^` и `~`):

| Пакет | Версия |
|---|---|
| react, react-dom | 19.3.1 |
| vite | 8.3.4 |
| @vitejs/plugin-react | 6.1.2 |
| @tanstack/react-query | 5.104.3 |
| openapi-typescript | 7.13.0 |
| openapi-fetch | 0.17.0 |
| tailwindcss, @tailwindcss/vite | 4.3.3 |
| shadcn (CLI) | 4.22.0 |
| radix-ui | 1.8.1 |
| lucide-react | 1.59.0 |
| sonner | 2.0.8 |
| clsx | 2.1.1 |
| tailwind-merge | 3.7.0 |
| yet-another-react-lightbox | 3.32.2 |
| vitest | 5.0.5 |
| @testing-library/react | 16.3.3 |
| jsdom | 30.1.3 |
| @playwright/test | 1.64.1 |

Правила:
- Установка — всегда `pnpm install --frozen-lockfile`. npm и yarn не использовать: npm-локфайл теряет нативные бинарники других ОС (rolldown, lightningcss, Tailwind oxide). pnpm-локфайл, созданный на Windows, содержит бинарники darwin-arm64 для Mac.
- Новая зависимость или смена версии — только с согласия S0.
- Конфликт в `pnpm-lock.yaml`: взять версию из `main` и выполнить `pnpm install`. Руками не мерджить.

## 4. Запрещено

- TypeScript 6 и 7. Тег `latest` в npm — это TS 7.0.2: у него нет compiler API, и openapi-typescript (peer `^5`) ломается. `pnpm add -D typescript` без версии ставит именно его. TS 6.x требует override peer-зависимости — тоже нет.
- `react-router` любой версии и другие роутеры.
- `msw`. Для E2E-моков — `page.route` в Playwright, для unit — JSON-фикстуры.
- `axios`, `i18next`, Redux, Zustand и прочие библиотеки состояния. Единственное хранилище серверных данных — TanStack Query.
- ESLint, Prettier, typescript-eslint. Только Biome.
- pnpm 12 и npm.
- CDN, веб-шрифты (включая Google Fonts), внешние скрипты, аналитика, телеметрия. UI должен работать в офлайн-контуре банка.
- `EventSource` и SSE в v1. Маршрут `/api/ui/jobs/{id}/events` зарезервирован на P3.
- `fetch` для загрузки видео: у него нет прогресса отправки. Только XHR в `src/features/upload/uploadVideo.ts`.
- Вызовы `/api/v1/*`, заголовок `X-API-Key`, ключи в бандле, `/api/v1/admin/status`.
- base64-кадры в UI. Только URL кадров из ответа.
- `dangerouslySetInnerHTML`. Серверные тексты выводятся как текст.
- `localStorage` для чего-либо, кроме списка недавних проверок; любое обращение — в `try/catch`.
- Персональные данные, имя файла и таймкод в URL.

## 5. Команды

Из папки `web/` (PowerShell на Windows и zsh на Mac — одинаково). Имена скриптов, кроме `gen:api`, — предложение S2 и фиксируются в `package.json`.

```sh
pnpm install --frozen-lockfile
pnpm dev                    # Vite на :5173, прокси /api, /ready, /health на API текущего worktree
pnpm gen:api                # openapi-typescript src/shared/api/openapi.json -o src/shared/api/schema.d.ts
pnpm typecheck              # tsc -b
pnpm lint                   # biome ci .
pnpm format                 # biome check --write .
pnpm test                   # vitest run
pnpm build                  # vite build -> web/dist
pnpm e2e --project=chrome-desktop
```

Из корня репозитория:

```sh
uv run just dev             # API в режиме stub + воркер (CG_PIPELINE_MODE=stub); поднимает ли он Vite — проверить рецепт
uv run just check           # ruff, basedpyright, lint-imports, pytest, biome ci, tsc -b, проверка дрейфа gen:api и экспорта контрактов
uv run just e2e
uv run just build-web
```

Порт API в каждом worktree свой: `scripts/port.py` хеширует путь в диапазон 8100–8199. Как его получает `vite.config.ts` — проверить у S1. `/ready` и `/health` лежат вне `/api`, их тоже нужно проксировать.

## 6. Сгенерированные файлы — не редактировать

| Файл | Чем создаётся |
|---|---|
| `src/shared/api/openapi.json` | `uv run just schemas` в корне (`python -m cashguard.contracts.export`). Импортирует приложение без ML |
| `src/shared/api/schema.d.ts` | `pnpm gen:api` |
| `pnpm-lock.yaml` | `pnpm install` |

- `gen:api` с последующим `git diff --exit-code` входит в `just check`. Дрейф ломает мердж.
- Нужно новое поле API — сначала PR S0 в `src/cashguard/contracts/ui.py`, затем регенерация, затем код UI. Типы API руками не дублировать.
- Сгенерированные файлы исключены из проверок Biome в `biome.json`.
- Компоненты shadcn в `src/shared/ui/` добавляются через CLI: `pnpm dlx shadcn@4.22.0 add <component>`. Это обычный исходный код, но правь его минимально: композиция лучше правки.

## 7. Контракт API

- Только `/api/ui/*` и `/ready`, относительные пути, тот же origin. CORS нет.
- Клиент: `src/shared/api/client.ts` (`openapi-fetch`, `createClient<paths>`). Хуки: `src/shared/api/queries.ts` (`useJob`, `useCatalog`, `useReady`).
- Опрос задания: 1 с, пока `queued` или `running`; после `done` и `failed` — `staleTime: Infinity` и без опроса. Конфигурация — в [docs/ui.md §8.3](../docs/ui.md#83-опрос-задания).
- URL видео и кадров берутся из ответа (`video.url`, `evidence[].url`). Пути не собирать.
- Вердикт — только поле `verdict`. UI не вычисляет вердикт и не фильтрует список нарушений.
- Типы статусов, вердиктов, кодов шагов — строковые объединения из `schema.d.ts`. На неизвестное значение UI не падает: показывает код как есть.

## 8. Тексты и формулировки

- Все тексты UI — в `src/shared/i18n/ru.ts`. Кириллица в `src/**/*.ts(x)` вне `i18n/` и тестов запрещена; это проверяет unit-тест. Таблица ключей — в [docs/ui.md §12](../docs/ui.md#12-тексты-интерфейса).
- Вердикты — дословно из ТЗ: «Нарушение не обнаружено», «Обнаружены признаки возможных нарушений».
- Тексты с сервера (`title_ru`, `description_ru`, `quote_ru`, `clause`, `act_title_ru`, `note_ru`, `reason_ru`, `message_ru`) показываются как есть: не править, не сокращать, не разбирать.
- Никаких юридических выводов: не писать «нарушение установлено», «нарушен пункт» для косвенных норм, «виновен», «хищение». Результат — «подсказка для проверки человеком».
- Никаких утверждений про лица и личность (FAQ 29). Только роли: кассир, клиент, инкассатор, посторонний.
- В коде UI нет захардкоженных норм, пунктов и цитат. Нормы приходят только с сервера.
- Числа и даты — через `Intl` с `ru-RU`. Формы множественного числа — `Intl.PluralRules`.

## 9. Код

- Папки верхнего уровня `src/` — строго по FOLDER_TREE: `pages/`, `features/{upload,progress,verdict,violations,evidence,player,signals,recent,health}/`, `shared/{api,ui,lib,i18n}/`, `styles/`. Новая папка — через S0.
- Одна фича — одна папка. Так параллельные сессии почти не трогают одни и те же файлы.
- Один компонент на файл, именованные экспорты.
- Логику без React держать в `.ts` рядом (`markers.ts`, `format.ts`, `urlState.ts`, `validateFile.ts`) и покрывать unit-тестами.
- Тесты рядом с кодом: `*.test.ts(x)`.
- TypeScript в режиме `strict`. `any` — только с комментарием о причине.
- Лайтбокс и экран типов — ленивые чанки (`React.lazy`). CSS лайтбокса импортируется внутри ленивого модуля.
- Иконки — именованный импорт из `lucide-react`.
- Доступность: интерактивное — это `<button>` или `<a>`; у картинок `alt`; видимый фокус; `aria-live` для статуса. Подробно — [docs/ui.md §11](../docs/ui.md#11-доступность).
- Внешние ссылки только на `url` норм (adilet.zan.kz), с `target="_blank" rel="noopener noreferrer"`.

## 10. Данные и приватность

- Не открывать видео и кадры датасета и тестовые видео жюри. Не просить людей их присылать.
- E2E-фикстуры — только синтетика из `scripts/make_synthetic_mov.py` или `tests/fixtures/synthetic_video.py`, генерируются перед прогоном в `web/e2e/fixtures/`. `*.mov` и `*.mp4` не коммитить (они в `.gitignore`).
- Не коммитить `web/dist`, `node_modules`, отчёты Playwright.
- E2E против публичного URL: не больше двух загрузок (лимит UI — 5 загрузок за 10 минут с IP). Во время окна жюри — никаких прогонов.

## 11. Перед тем как сказать «готово»

Все пункты зелёные:

1. `pnpm install --frozen-lockfile` — локфайл не изменился.
2. `pnpm gen:api` — `git diff --exit-code src/shared/api` пуст.
3. `pnpm typecheck` (`tsc -b`) — без ошибок.
4. `pnpm lint` (`biome ci .`) — без ошибок.
5. `pnpm test` (`vitest run`) — все тесты проходят; для новой логики есть новые тесты.
6. `pnpm build` — сборка прошла; начальный чанк не вырос больше чем на 20 % (иначе — к S0).
7. `pnpm e2e --project=chrome-desktop` против `uv run just dev` в режиме stub; для вёрстки — ещё `--project=chrome-mobile`.
8. Из корня: `uv run just check`.
9. Новые тексты — в `ru.ts` и в таблице [docs/ui.md §12](../docs/ui.md#12-тексты-интерфейса).
10. Изменения поведения отражены в [docs/ui.md](../docs/ui.md).

На Mac (S6): проекты `webkit` и `iphone` перед заморозкой релиза и после правок плеера, лайтбокса или загрузки.

## 12. Мердж

- Своя ветка в своём worktree: `git worktree add ../cg-wt/<slug> -b s2/<slug> main`, затем `pnpm install --frozen-lockfile`.
- Перед PR: rebase на `main`, полный список из §11.
- Мерджит S0, только fast-forward.
- Изменение контракта — отдельный маленький PR S0, он идёт первым. Ветка UI ребейзится после него.
- Окончания строк — LF: Biome по умолчанию форматирует с LF, и `biome ci` упадёт на CRLF. Сейчас `.gitattributes` задаёт `eol=lf` только для sh, py, yaml, md, csv. Добавить `*.ts`, `*.tsx`, `*.json`, `*.css`, `*.html` с `eol=lf` — запрос к S0 (проверить).
