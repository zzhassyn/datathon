# `deploy/cloudflared/` — Cloudflare Tunnel

> **Назначение:** Шаблон конфигурации named tunnel.
> **Владелец:** S6 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `config.yml.tmpl` | UUID туннеля, `protocol: http2`, `metrics: 127.0.0.1:20241`, ingress → `http://127.0.0.1:8000`, последнее правило `http_status:404` |

## Правила

- Bot Fight Mode выключен, кэширование `/api` выключено.
- Второй коннектор того же туннеля одновременно с Mac не запускать: запросы разойдутся по машинам.
- Резерв — Tailscale Funnel (`tailscale funnel --bg 8000`). Quick tunnel — только для проверок P0.

## Документы

- [docs/runbook_mac.md — эксплуатация Mac](../../docs/runbook_mac.md)
- [docs/adr/ — архитектурные решения](../../docs/adr/)
