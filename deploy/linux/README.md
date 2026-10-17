# `deploy/linux/` — Linux с GPU

> **Назначение:** Тот же код и lock-файл на Linux с CUDA: переносимость в контур банка и аварийный резерв.
> **Владелец:** S1 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `Dockerfile.cuda` | две стадии: `ghcr.io/astral-sh/uv:0.13.1-python3.12-trixie-slim` для сборки, `python:3.12-slim-trixie` + `libgl1`, `libglib2.0-0` для запуска |
| `compose.yaml` | сервисы `api`, `perception` (`gpus: all`, `CG_VISION_BACKEND=torch`), `vlm` (`CG_VLM_BACKEND=transformers`); общий bind-mount `var/`, модели в `/models` только для чтения |
| `systemd/` | эквивалентные unit-файлы |

## Правила

- Проверяется в режиме «только правила» на Windows RTX 3050.
- Как резерв запускается, только если Mac недоступен и его коннектор туннеля остановлен.

## Документы

- [docs/architecture.md — процессы, режимы, поток данных](../../docs/architecture.md)
- [docs/runbook_mac.md — эксплуатация Mac](../../docs/runbook_mac.md)
