# `deploy/` — развёртывание

> **Назначение:** Шаблоны и файлы для боевого Mac, туннеля и Linux-варианта.
> **Владелец:** по папкам · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `macos/` | [S6] LaunchAgents и установка |
| `cloudflared/` | [S6] конфигурация named tunnel |
| `linux/` | [S1] Docker CUDA, compose, systemd |

## Правила

- Секреты, сертификаты туннеля и ключи API в git не попадают.

## Документы

- [docs/runbook_mac.md — эксплуатация Mac](../docs/runbook_mac.md)
- [docs/architecture.md — процессы, режимы, поток данных](../docs/architecture.md)
