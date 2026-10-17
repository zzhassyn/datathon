# `deploy/macos/` — macOS

> **Назначение:** Установка сервиса на MacBook Pro M2 Pro как user LaunchAgents.
> **Владелец:** S6 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `launchd/` | шаблоны `.plist.tmpl` |
| `install.py` | рендер шаблонов в `~/Library/LaunchAgents/` для `just services-install`; `just mode serve\|train\|dev` оборачивает `launchctl bootstrap` и `bootout` |

## Правила

- Репозиторий на Mac — `~/cashguard`: не Desktop, Documents, Downloads и не iCloud.
- Чек-лист дня проверки — `docs/jury_mode.md`. Единственная копия — в `docs/` (так в [docs/structure.md](../../docs/structure.md) и [docs/architecture.md](../../docs/architecture.md), раздел 14).
- Docker на macOS не используется: в контейнере нет Metal и ANE.

## Документы

- [docs/runbook_mac.md — эксплуатация Mac](../../docs/runbook_mac.md)
- [docs/jury_mode.md — чек-лист дня проверки](../../docs/jury_mode.md)
