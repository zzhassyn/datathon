# `web/src/shared/api/` — клиент API

> **Назначение:** Типизированный доступ к `/api/ui` по OpenAPI бэкенда.
> **Владелец:** S2; `openapi.json` и `schema.d.ts` генерируются · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `openapi.json` | [GEN] вывод `uv run just schemas`; коммитится, руками не править |
| `schema.d.ts` | [GEN] openapi-typescript из `openapi.json`; руками не править |
| `client.ts` | openapi-fetch; загрузка файла — через XHR ради событий прогресса |
| `queries.ts` | запросы TanStack Query: `refetchInterval` 1000 мс пока `queued` или `running` |

## Правила

- Расхождение `openapi.json` с бэкендом ломает `tsc -b` и проверку дрейфа в `just check`.

## Документы

- [docs/ui.md — интерфейс](../../../../docs/ui.md)
- [docs/api.md — API жюри и интерфейса](../../../../docs/api.md)
