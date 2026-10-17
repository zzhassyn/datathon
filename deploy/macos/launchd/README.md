# `deploy/macos/launchd/` — LaunchAgents

> **Назначение:** Шаблоны трёх user LaunchAgents.
> **Владелец:** S6 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `kz.cashguard.supervisor.plist.tmpl` | `~/cashguard/.venv/bin/python -m cashguard.cli up --profile prod`; RunAtLoad, KeepAlive, ProcessType=Interactive, ThrottleInterval 5, ExitTimeOut 30, NumberOfFiles 8192; офлайн-переменные `HF_*`, `YOLO_*`, `CG_ENV_FILE` |
| `kz.cashguard.tunnel.plist.tmpl` | `cloudflared tunnel --config ~/.cloudflared/config.yml run`, KeepAlive |
| `kz.cashguard.caffeinate.plist.tmpl` | `/usr/bin/caffeinate -ims`, KeepAlive |

## Правила

- Абсолютные пути, никогда `uv run` (он может синхронизировать окружение и выйти в сеть при загрузке).
- Файлы с LF (`.gitattributes`).

## Документы

- [docs/runbook_mac.md — эксплуатация Mac](../../../docs/runbook_mac.md)
