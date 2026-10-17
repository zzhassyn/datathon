# `src/cashguard/pipeline/rules/access/` — доступ в кассу

> **Назначение:** Правила про дверь и людей, которые входят в кассовое помещение.
> **Владелец:** S4 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `door_not_closed.py` | `DOOR_NOT_CLOSED`: после входа дверь `open` ≥ N с, у двери никого; таймкод — вход + N |
| `outsider_in_cash_room.py` | `OUTSIDER_IN_CASH_ROOM`: новый трек с ролью `outsider`; таймкод — появление |
| `third_person_during_service.py` | `THIRD_PERSON_DURING_SERVICE`: идёт сеанс, в комнате второй не-кассир; таймкод — появление |
| `storage_unescorted.py` | `STORAGE_UNESCORTED`: вход в зону хранения без сопровождения (пункт 39 списка команды) |

## Правила

- Каждый файл — класс с `@register_rule`, протокол `Rule`: `code`, `version`, `verdict_impact`, `evaluate(ctx) -> list[Candidate]`.
- Тест `tests/unit/rules/<CODE>_test.py`, фикстуры `tests/fixtures/timelines/<CODE>_{pos,neg}_*.json`.
- Кандидат не должен срабатывать на 19 чистых роликах (`uv run just eval-replay`).
- Только Python + numpy: модуль работает в режиме replay на Windows без моделей и Mac.

## Документы

- [docs/violation_catalog.md — коды нарушений и сигналов](../../../../../docs/violation_catalog.md)
- [docs/structure.md — дерево, владельцы, имена](../../../../../docs/structure.md)
