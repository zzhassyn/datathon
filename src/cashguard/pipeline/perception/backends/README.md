# `src/cashguard/pipeline/perception/backends/` — бэкенды распознавания

> **Назначение:** Реализации протокола `PerceptionBackend` (`name`, `warmup()`, `info()`, `run(samples, plan, progress) -> PerceptionTrace`).
> **Владелец:** S3 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `coreml.py` | Core ML `.mlpackage` на `CPU_AND_NE`: det 864×480 и 480×864 (ленивая загрузка), pose 864×480, `cls_screen`, `cls_safe_lock`, `cls_door` |
| `torch.py` | PyTorch для CUDA на Linux и Windows |
| `replay.py` | читает готовые трассы из `var/signals/` |
| `stub.py` | синтетические наблюдения для тестов |
| `null.py` | пустой бэкенд |

## Правила

- Compute units только `CPU_AND_NE`; `ALL` и `CPU_AND_GPU` падают из-за ошибки MPSGraph.
- Рамки рисуются в `pipeline/evidence` через cv2; `Results.plot()` не использовать.
- Веса — только локальные, sha256 сверяется с `configs/models.yaml` при старте.

## Документы

- [docs/pipeline.md — этапы обработки](../../../../../docs/pipeline.md)
- [docs/training.md — обучение и экспорт моделей](../../../../../docs/training.md)
- [docs/adr/ — архитектурные решения](../../../../../docs/adr/)
