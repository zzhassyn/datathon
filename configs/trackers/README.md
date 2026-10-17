# `configs/trackers/` — трекер

> **Назначение:** Настройки BoT-SORT для статичной камеры.
> **Владелец:** S3 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `botsort_static.yaml` | `with_reid` False, `gmc_method` none, track_high 0.4, track_low 0.1, new_track 0.5, match 0.8, `fuse_score` True, `track_buffer` 15 |

## Правила

- Точные имена ключей — как в `botsort.yaml` из ultralytics 8.4.x (проверить при создании).
- Файл всегда передаётся явно (`tracker=`): трекер по умолчанию в 8.4.x — TrackTrack.
- ReID по внешности выключен (FAQ 29).

## Документы

- [docs/pipeline.md — этапы обработки](../../docs/pipeline.md)
- [docs/adr/ — архитектурные решения](../../docs/adr/)
