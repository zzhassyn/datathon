# `src/cashguard/pipeline/render/` — рендер результатов

> Владелец: S1. Реализован рендер frozen jury JSON; Python-контракты S0 ещё отсутствуют.

## Интерфейсы первой итерации

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

После появления S0 `pyproject.toml`, `uv.lock` и `justfile`: `uv run pytest tests/unit/render -q`.

## Зависимости от S0/S3

- `contracts/{jury,findings,ui}.py`: заменить временные mappings адаптерами утверждённых моделей.
- `configs/catalog/*.yaml`: проверка русских названий типов по каталогу; stub явно помечен как synthetic.
- `jsonschema` должен присутствовать в runtime dependency set, поскольку validation выполняется при обработке, а не только в тестах.
- S3 evidence encoder и callback восстановления повреждённых пикселей.
