# `web/src/features/progress/` — ход обработки

> **Назначение:** Блок «ход обработки» интерфейса CashGuard.
> **Владелец:** S2 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Что планируется

| Что | Подробности |
|---|---|
| `useJob` | опрос `GET /api/ui/jobs/{id}` раз в 1 с, пока `queued` или `running`; `staleTime: Infinity` после `done` |
| `StageStepper` | этапы `queued`, `probe`, `perception`, `rules`, `evidence`, `vlm`, `done`; позиция в очереди, ETA |

## Документы

- [docs/ui.md — интерфейс](../../../../docs/ui.md)
