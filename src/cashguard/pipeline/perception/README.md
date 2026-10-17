# `src/cashguard/pipeline/perception/` — распознавание

`object_check.py` — отдельная локальная проверка дообученного YOLO checkpoint.
Запускается API в свежем ML-интерпретаторе, использует CPU/один поток и локальные
веса. Два кадра в секунду выбираются по PTS; отчёт содержит реальные детекции,
confidence и SHA256 входа/весов, MP4 с рамками и JPEG. Обработка ограничена
180 секундами. Это детектор предметов, без трекинга, правил, VLM и вердикта.
Он не подменяет `PerceptionTrace` или ещё не реализованные стадии K4.

> **Назначение:** Детекция, трекинг, поза и классификация фрагментов по кадрам → `PerceptionTrace`.
> **Владелец:** S3 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `orchestrator.py` | кадр → детекция + BoT-SORT → поза → классификаторы фрагментов → `FrameObservation` |
| `plan.py` | уровень `full\|fast\|min` по EWMA из `perf_stats`, длительности и очереди: full — 3 кадра/с, fast — 1,5, min — 1 (только детекция и cls); задача, взятая позже t0 + 200 с, — сразу `min`; на рубеже `perception_hard_stop` (t0 + 235 с) — остановка и правила по обработанным кадрам (`perception_truncated`) |
| `gating.py` | поза только для треков в зонах стола, передачи и сейфа, ≤ 2 кадра/с; cls 1,5 кадра/с + триггеры смены состояния |
| `crops.py` | фрагменты 224 для `screen`, `safe_lock`, `door` ровно по `data/crops/crop_spec.json` (region, pad, upscale, min_side); letterbox — только при `letterbox: true` в crop_spec |
| `export.py` | (новый путь, S3-Mac) экспортёр сервиса: `.pt` → Core ML `models/<role>/vNNN/{best.pt,*.mlpackage,manifest.json}`, оба варианта входа, паритет `yolo val`, MLComputePlan; `training/scripts/10_export_coreml.py` для выпуска не используется |
| `trace_io.py` | чтение и запись `trace.json.gz` |
| `backends/` | реализации `PerceptionBackend` |

## Правила

- Трекер всегда передаётся явно: `configs/trackers/botsort_static.yaml`, состояние сбрасывается на каждую задачу. Иначе Ultralytics 8.4.x молча берёт TrackTrack.
- Без распознавания лиц и ReID по внешности (FAQ 29).
- Трассы задач жюри и UI никогда не копируются в `var/signals/`.
- Параметры — `configs/perception.yaml`.

## Документы

- [docs/pipeline.md — этапы обработки](../../../../docs/pipeline.md)
- [docs/training.md — обучение и экспорт моделей](../../../../docs/training.md)
- [docs/adr/ — архитектурные решения](../../../../docs/adr/)
