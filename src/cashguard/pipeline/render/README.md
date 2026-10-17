# `src/cashguard/pipeline/render/` — рендер результатов

> Владелец: S1. Рендер frozen jury JSON и адаптер утверждённого AnalysisResult.

## Интерфейсы

- `jury.render_jury(job_id, violations, *, duration_sec=None, status="done", safe_fallback=True, reencode_jpeg=None)` возвращает обычный `dict` с ровно четырьмя ключами замороженной схемы. Временный внутренний вход — mappings; это не замена `contracts/findings.py`.
- Нарушение содержит `type`, `pts_event` (или `timecode_sec`), `description`, `evidence_frames`. Кадр содержит `timestamp_sec` и `jpeg_bytes: bytes` либо строгий `image_base64`.
- `jury.validate_jury(document, *, duration_sec=None)` проверяет frozen JSON Schema, конечный JSON, статусы, инвариант вердикта, 0–5 нарушений по времени, 1–3 кадра, таймкоды и совпадение первого кадра с событием.
- `ui.build_ui_result_fragment(job_id, jury_result, *, evidence_names)` возвращает внутренний фрагмент с URL вместо base64. Это **не** `UiJobView`: lifecycle-поля и метаданные `AnalysisResult` добавляет API/worker adapter; после публикации S0-контракта фрагмент должен проходить через него.

Fallback ограничивает времена длительностью, округляет до сотых, сортирует и ограничивает списки. При несовпадении события с первым evidence используется PTS кадра: пикселям не назначается выдуманное время. Если округление до сотых выходит за дробную длительность, берётся ближайшая сотая внутри видео. Непустые `type`/`description` и отсутствующие кадры не выдумываются. Невосстановимый результат превращается в `failed`, `verdict=null`, `violations=[]`; worker должен сохранить соответствующий статус, а не `done`.

JPEG проверяется структурно (строгий base64, SOI/SOF/SOS/EOI, размеры и целостность сегментов), без импорта ML/image-библиотек. Это не полноценное декодирование entropy-потока. Производитель evidence обязан выдавать JPEG из действительного декодированного кадра; тесты дополнительно декодируют синтетический JPEG через PyAV. Для восстановления worker может передать `reencode_jpeg(bytes) -> bytes`. Renderer сам не читает исходное видео и не генерирует фиктивные кадры.

Каждый ответ валидируется по `contracts/schemas/jury_job.schema.json` через предусмотренный стеком `jsonschema`. Полные ответы/base64 не включаются в ошибки. Атомарную запись в attempt-папку и CAS после неё выполняет worker через `core.atomic`/`jobs.repo`.

## Проверка

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/render -q
```

`uv run pytest tests/unit/render tests/api/test_analysis_publication.py -q`.

## Адаптер K4

`analysis.render_analysis(result, evidence_dir)` принимает `AnalysisResult`, выдаёт
`RenderedAnalysis(jury, ui, evidence)`. Проверяет безопасные имена и расположение
кадров, декодирование JPEG, размеры, времена и связь первого кадра с событием,
уникальность ID и соответствие вердикта. Нарушения сортируются по времени; UI
получает все, jury — максимум пять. Сигналы не меняют jury-вердикт. Поля legal
переносятся из результата без придумывания норм; применимость проверяется выше.
`PerceptionWorker.publish_analysis` проверяет ID/sha256 исходного задания, атомарно
сохраняет файлы в новую attempt-папку и публикует их через lease/CAS.

Проверки адаптера используют только синтетику. Сборка реальных стадий runner и
VLM-планировщик ещё не реализованы; guards full/replay не сняты.

`ViolationFinding.explanation` и `AnalysisResult.visibility` переносятся в UI
без восстановления наблюдений из описаний или confidence. Адаптер повторно
валидирует модель (в том числе после `model_copy`), проверяет индексы evidence и
границы интервалов по исходной длительности. Диагностика не попадает в jury JSON
и не меняет вердикт. Сортировка событий сохраняет порядок кадров внутри события.
