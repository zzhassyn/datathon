# UI API

Владелец: S2. Только `/api/ui/*` и `/ready`, тот же origin.

- `openapi.json` — снимок реально работающего S1 a46acdb (`pnpm sync:api`).
- `schema.d.ts` — generated openapi-typescript (`pnpm gen:api`), руками не править.
- `client.ts` — openapi-fetch, таймаут GET 15 секунд.
- `queries.ts` — TanStack Query, опрос 1 с до terminal, без опроса после 401/404.
- `decode.ts` — временная проверка unknown-ответов и вывод типов представления: в S1 ещё нет формальных UI response models. Это не согласованный контракт S0.
- `errors.ts` — безопасные русские сообщения и Retry-After.

После выпуска S0 `contracts/ui.py`/exporter: обновить снимок, перегенерировать типы, заменить адаптер generated components и пройти contract drift. Backend и frozen jury schema этой веткой не меняются. Полный handoff — `web/README.md`.
