# `web/src/features/player/` — плеер

> **Назначение:** Блок «плеер» интерфейса CashGuard.
> **Владелец:** S2 · **Статус:** frontend реализован в `codex/frontend-integration`, 17.10.2026; ограничения интеграции — в `web/README.md`

## Назначение

| Что | Подробности |
|---|---|
| `VideoPlayer` | `<video controls playsInline preload=metadata>` с `/api/ui/jobs/{id}/video.mp4` |
| `EventTimeline` | маркеры на t/длительность; клик — переход на `max(0, t − 2)` |

## Документы

- [docs/ui.md — интерфейс](../../../../docs/ui.md)
