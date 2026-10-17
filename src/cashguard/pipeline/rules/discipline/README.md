# `src/cashguard/pipeline/rules/discipline/` — дисциплина

> **Назначение:** Правила про предметы и поведение кассира на рабочем месте.
> **Владелец:** S5 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `personal_items.py` | `PERSONAL_ITEMS`: `personal_bag` или `outerwear` в зоне стола ≥ N с; таймкод — появление |
| `food_drink.py` | `FOOD_DRINK`: `cup` у лица кассира (поза) или на столе при `cash`; таймкод — первый глоток; прямой нормы нет |
| `phone_cashier.py` | `PHONE_CASHIER`: `phone` в руках кассира, а не клиента; таймкод — появление |

## Правила

- Каждый файл — класс с `@register_rule`, протокол `Rule`: `code`, `version`, `verdict_impact`, `evaluate(ctx) -> list[Candidate]`.
- Тест `tests/unit/rules/<CODE>_test.py`, фикстуры `tests/fixtures/timelines/<CODE>_{pos,neg}_*.json`.
- Кандидат не должен срабатывать на 19 чистых роликах (`uv run just eval-replay`).
- Только Python + numpy: модуль работает в режиме replay на Windows без моделей и Mac.

## Документы

- [docs/violation_catalog.md — коды нарушений и сигналов](../../../../../docs/violation_catalog.md)
- [docs/legal_sources.md — нормы и цитаты](../../../../../docs/legal_sources.md)
- [docs/structure.md — дерево, владельцы, имена](../../../../../docs/structure.md)
