# ADR-0006. Замороженный контракт жюри и заранее отрендеренный, проверенный jury.json

> Назначение: зафиксировать форму ответа API жюри и способ его выдачи.
> Владелец: S0 (`contracts/jury.py`, `contracts/schemas/jury_job.schema.json`); реализует S1 (`render/jury.py`, `api/routes/jury.py`). Статус: черновик v1, 17.10.2026.

## Статус

Принято. Умолчания (русский `title_ru` в `type`, норма внутри `description`, `verdict: null` и `violations: []` при processing, HTTP 200) ждут подтверждения на grill-me и, если возможно, у организаторов. Изменение замороженной схемы — только с одобрения человека.

## Контекст

- ТЗ §10 задаёт два метода и пример ответа: `job_id`, `status` (processing, done, failed), `verdict` (`violation` / `no_violation`), `violations[]` с `type`, `timecode_sec`, `description`, `evidence_frames[]` (1–3 JPEG в base64).
- FAQ 18: оценка автоматическая, результат в JSON по единой схеме. Наша трактовка: лишние поля могут сломать разбор.
- Один некорректный ответ стоит всего видео.
- `type` — свободный текст, оценивается автоматически (15 баллов за классификацию). Названия должны быть стабильными.
- FAQ 17: нужны конкретный кадр и таймкод.
- Сигналы (группа B2 из [detection_design.md](../../analysis/detection_design.md)) на вердикт не влияют и в ответ жюри не попадают.
- Жюри опрашивает GET до `done`, возможно часто и по нескольким задачам.

## Решение

1. **Оракул** — написанная вручную `src/cashguard/contracts/schemas/jury_job.schema.json` по ТЗ §10 с `additionalProperties: false`. Модели Pydantic в `contracts/jury.py`: `AnalyzeAccepted`, `JuryEvidenceFrame`, `JuryViolation`, `JuryJobResponse`.
2. **Только точные ключи.** Никаких дополнительных полей, никогда.
3. **Статусы:** внутренние `queued` и `running` → `processing`. При `processing` и `failed`: `verdict: null`, `violations: []`.
4. **`type`** = `CatalogEntry.title_ru` — стабильная русская строка из `configs/catalog/<CODE>.yaml`.
5. **`description`** = шаблон каталога, заполненный фактами (`str.format_map`), плюс фраза о норме по силе ссылки:
   - прямая: `Основание: {clause} {act_cite}.` (например «Основание: п. 20 ч. 1 Правил …»);
   - косвенная: `Может свидетельствовать о несоблюдении {clause} {act_cite}.`;
   - нормы нет, ближайший пункт есть: `Прямой нормы нет; ближайшая — {clause} {act_cite}.`;
   - пункт не определён (FAQ 31): `Прямой нормы нет; пункт {act_cite} не определён (FAQ 31); требуется проверка человеком.`
   `{clause}` хранится с префиксом «п.», `{act_cite}` — поле `cite_ru` акта. Шаблоны дословно — [legal_sources.md](../legal_sources.md), раздел 6.2. У каждого кода, влияющего на вердикт, есть хотя бы одна ссылка с актом из `configs/legal/acts.yaml` (ТЗ §4, пункт о нормативном источнике).
   Нормы — только из раздела 7 [case_analysis.md](../../analysis/case_analysis.md). `VlmAnswer.note` в описание не попадает никогда.
6. **Инварианты:**
   - done ⇒ `verdict` не null, и `violation` ⇔ хотя бы одно нарушение;
   - `timecode_sec = round(pts_event, 2)`; все `timestamp_sec = round(pts, 2)` той же функцией; поэтому `evidence_frames[0].timestamp_sec == timecode_sec` точно;
   - `0 ≤ timecode_sec ≤ duration`;
   - float — Python float;
   - не больше 5 нарушений, по возрастанию времени;
   - `image_base64` — сырой JPEG, начинается с `/9j/`, без префикса `data:`.
7. **Рендер один раз на этап.** `render.jury` пишет `jury.json` после perception (промежуточный) и после VLM (финальный). Перед публикацией — валидация по замороженной схеме. При ошибке — безопасный fallback: перекодировать evidence, зажать таймкоды в [0, duration], оставить ≤ 3 кадров, заново применить инвариант, снова валидировать. Запись tmp → fsync → `os.replace`, затем CAS в БД.
8. **Выдача:** `GET /api/v1/jobs/{id}` при done — `FileResponse(jury.json, application/json)`. Для processing и failed — фиксированные формы. Всегда 200 для известной задачи, 404 JSON для неизвестной или некорректной, `Cache-Control: no-store`.
9. Алиасы маршрутов со слэшем в конце: никогда 307.
10. Глобальные обработчики исключений возвращают JSON, никогда HTML.

## Рассмотренные альтернативы

| Альтернатива | Почему отклонена |
|---|---|
| Дополнительные поля в JSON жюри (`legal_reference`, `signals` и т. п.) | Риск сломать автоматический разбор. Норма передаётся текстом в `description`, сигналы — только в UI |
| `type` как английский код (`PC_UNLOCKED`) | Проверяющий и сопоставление типов, скорее всего, ориентированы на понятные названия. Решение подтверждается на grill-me |
| Не отдавать `verdict` при processing | Форма ответа становится разной для разных статусов. Явный `null` и `[]` предсказуемее для парсера |
| Сериализация на каждый GET | Тратит CPU на каждый опрос и даёт шанс получить разные ответы для одной задачи |
| base64 и в UI | Тяжёлые опросы. UI получает URL кадров ([ADR-0008](0008-separate-ui-api.md)) |

## Последствия

Плюсы:
- Каждый ответ проходит замороженную схему, даже при сбое рендера (fallback).
- GET жюри — дешёвая отдача файла (≤ 5 мс на origin, оценка).
- Результат детерминирован и одинаков при повторных опросах.

Минусы и цена:
- Жюри видит норму только как текст в `description`. Богатое представление норм — только в UI.
- Умолчания не подтверждены организаторами.
- Копия результата из `result_cache` для новой задачи должна подставить новый `job_id` в `jury.json` и `ui_result.json`.
- Таймкоды и кадры должны быть согласованы ещё до рендера ([ADR-0011](0011-pts-timecodes-frame-store.md)); fallback лишь страхует.

Что проверяет решение:
- `tests/contract`: замороженная схема; пример из ТЗ (`tests/fixtures/jury_example.json`) проходит валидацию; цитаты норм дословно есть в `analysis/normative`; полнота каталога (правило + норма + шаблон на каждый код).
- `tests/unit/render`: fallback на испорченных входах.
- schemathesis по приложению: нет 5xx.
- G0: curl из ТЗ с телефона через мобильную сеть получает валидный по схеме ответ.

Связанные решения: [ADR-0004](0004-auth-before-body-streaming-upload.md), [ADR-0005](0005-deadline-tiers-provisional-degradation.md), [ADR-0008](0008-separate-ui-api.md), [ADR-0011](0011-pts-timecodes-frame-store.md). Подробности: [api.md](../api.md), [legal_sources.md](../legal_sources.md), [architecture.md, раздел 5.1](../architecture.md#51-jurypy--заморожен).
