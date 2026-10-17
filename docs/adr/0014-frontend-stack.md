# ADR-0014. Фронтенд: React 19 + Vite 8 + TS 5.9.3 + Tailwind/shadcn + TanStack Query, типы из OpenAPI, раздача с того же origin

> Назначение: зафиксировать стек, сборку и способ раздачи веб-интерфейса.
> Владелец: S0 (документ); реализует S2 (`web/**`). Статус: черновик v1, 17.10.2026.

## Статус

Принято.

## Контекст

- ТЗ §4: веб-интерфейс с загрузкой, статусом и результатом. Критерий «Готовность продукта» — 10 баллов.
- Интерфейс пишут агенты (Codex или Claude) в своём worktree, без бэкенда на машине.
- Офлайн-контур банка: никаких CDN, веб-шрифтов, аналитики.
- Язык интерфейса — русский (FAQ 26 оставляет выбор команде).
- Разработка на Windows, прод на Mac: lock-файл должен работать на обеих платформах.
- Контракт UI задаётся Pydantic-моделями ([ADR-0008](0008-separate-ui-api.md)); расхождение типов должно ломать сборку.
- Факты из исследования стека:
  - тег npm `latest` у TypeScript указывает на 7.0.2, у которого нет compiler API; он ломает openapi-typescript (peer `^5`); TS 6.0.3 требует override peer-зависимости;
  - lock-файл pnpm, созданный на Windows, записывает нативные бинарники darwin-arm64 (rolldown, lightningcss, Tailwind oxide);
  - jsdom 30 требует Node ≥ 24.15, поэтому dev-ПК нужно перевести с 24.14.1.

## Решение

1. **Стек:**

   | Слой | Выбор | Версия |
   |---|---|---|
   | Фреймворк | React SPA на Vite, без SSR | react / react-dom 19.3.1, vite 8.3.4, @vitejs/plugin-react 6.1.2 |
   | Язык | TypeScript, точный пин; `tsc -b` отдельно от сборки | typescript 5.9.3 |
   | Данные | TanStack Query — единственная библиотека состояния | @tanstack/react-query 5.104.3 |
   | Типы API | openapi-typescript (только типы) + openapi-fetch | 7.13.0, 0.17.0 |
   | UI-кит | Tailwind v4 через `@tailwindcss/vite`, shadcn CLI на radix-ui, lucide-react, sonner, clsx + tailwind-merge | 4.3.3, 4.3.3, 4.22.0, 1.8.1, 1.59.0, 2.0.8, 2.1.1, 3.7.0 |
   | Кадры | yet-another-react-lightbox (Zoom, Captions), ленивая загрузка | 3.32.2 |
   | Инструменты | Node в `.node-version` и `engines` (engine-strict), pnpm через `packageManager`, Biome | node 24.22.0, pnpm 10.34.7, @biomejs/biome 2.5.15 |
   | Тесты | Vitest + Testing Library + jsdom; Playwright | vitest 5.0.5, @testing-library/react 16.3.3, jsdom 30.1.3, @playwright/test 1.64.1 |

   Лицензии: MIT, у typescript и @playwright/test — Apache-2.0, у lucide — ISC.
2. **Без роутера.** Вид выбирается по query: `/?job=<id>` — страница задачи, `/?view=types` — «Типы нарушений и нормы», иначе — загрузка. Глубокие ссылки не требуют SPA-fallback.
3. **Раздача с того же origin.** Сборка в `web/dist`; FastAPI отдаёт `index.html` с `no-cache`, `/assets/*` с `public, max-age=31536000, immutable`. CORS не нужен. Node в проде не нужен.
4. **Типы из OpenAPI.** `python -m cashguard.contracts.export` (без ML-импортов) пишет `web/src/shared/api/openapi.json`; из него генерируется `schema.d.ts`. Оба файла закоммичены и генерируются, руками не правятся. Проверка дрейфа — в `just check`.
5. **Данные:** опрос раз в секунду ([ADR-0007](0007-ui-polling-sse-deferred.md)); загрузка через XHR (у `fetch()` нет событий прогресса загрузки); мгновенное локальное превью через `URL.createObjectURL`.
6. **Без внешних ресурсов:** только системный шрифтовой стек (кириллица рендерится нативно), никаких CDN и аналитики. Русские строки — в `src/shared/i18n/ru.ts`.
7. **Разработка:** Vite dev server на :5173 проксирует `/api` на порт worktree с FastAPI в режиме stub. `pnpm install --frozen-lockfile` всегда.
8. **Тесты:** Vitest для единиц; Playwright E2E против FastAPI в режиме stub: channel `chrome` на Windows (кодеки H.264), WebKit и iPhone на Mac (запускает S6). Фикстуры — только синтетические ролики.
9. **Запрещено** (фиксируется в `AGENTS.md` и `web/AGENTS.md`): TypeScript 7 (и 6 без согласования), react-router, MSW, pnpm 12, CDN-ресурсы, ручная правка сгенерированных файлов.

## Рассмотренные альтернативы

| Альтернатива | Почему отклонена |
|---|---|
| SvelteKit static, Vue + Vite | Агенты стабильнее пишут React. Разница в размере бандла (≈ 70 КБ) несущественна рядом с загрузкой 29 МБ |
| HTMX + Jinja | Нет типизированного контракта с API; jinja2 в проекте не используется |
| orval или hey-api | Генерируют runtime-код; нужны только типы |
| TypeScript 6 или 7 | TS 7 ломает openapi-typescript; TS 6 требует override peer-зависимости |
| react-router 8 | Не нужен: вид выбирается по query |
| npm или pnpm 12 | pnpm 10.34.7 проверен на переносимость lock-файла Windows → Mac; другие менеджеры и мажорные версии не проверялись |
| MSW 3 | E2E идут против настоящего FastAPI в режиме stub; моки сети не нужны |

## Последствия

Плюсы:
- Стек, который агенты пишут надёжнее всего.
- ≈ 80 КБ gzip начального JS; лайтбокс — ленивый чанк.
- Расхождение контракта ломает `tsc` и проверку дрейфа ещё до слияния.
- Работает офлайн в контуре банка.

Минусы и цена:
- Dev-ПК нужно обновить до Node 24.22.0 и pnpm 10.34.x.
- Сгенерированные `openapi.json` и `schema.d.ts` нужно перегенерировать после каждого изменения контрактов; при конфликте — перегенерировать, а не сливать.
- Swagger UI на `/docs` грузит JS с CDN: в офлайн-контуре `CG_DOCS_ENABLED=false` или локальные ассеты.

Что проверяет решение:
- `biome ci`, `tsc -b`, Vitest, проверка дрейфа `gen:api` в `uv run just check`.
- Playwright: `upload.spec.ts`, `result.spec.ts`, `mobile.spec.ts`; та же спецификация прогоняется против публичного URL.

Связанные решения: [ADR-0001](0001-repository-shape.md), [ADR-0007](0007-ui-polling-sse-deferred.md), [ADR-0008](0008-separate-ui-api.md). Подробности: [ui.md](../ui.md).
