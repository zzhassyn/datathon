# `src/cashguard/pipeline/vlm/backends/` — бэкенды VLM

> **Назначение:** Реализации протокола `VlmBackend`: `name`, `load(path, quant)`, `warmup()`, `ask(q, images, deadline_ts) -> VlmAnswer`, `scan_keyframes(images, times, deadline_ts) -> list[KeyframeFinding]`.
> **Владелец:** S6 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `mlx.py` | mlx-vlm `stream_generate`; `mx.set_cache_limit(1 GB)`, `mx.set_memory_limit(7 GB)`, `mx.clear_cache()` после задачи; модель только из главного потока |
| `transformers.py` | transformers на Linux с CUDA (extra `cuda`) |
| `replay.py` | записанные ответы для replay на Windows |
| `null.py` | VLM выключен |

## Правила

- Веса Qwen3.5-4B — из `models/hf/<repo>` с закреплённой ревизией (8-bit `1704583a`, 6-bit `fbb1092a`, 4-bit `0e7ffd5c`).
- `mlx_vlm.server` по HTTP не используется.

## Документы

- [docs/pipeline.md — этапы обработки](../../../../../docs/pipeline.md)
- [docs/adr/ — архитектурные решения](../../../../../docs/adr/)
