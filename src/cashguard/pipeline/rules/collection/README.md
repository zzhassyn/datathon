# `src/cashguard/pipeline/rules/collection/` — инкассация

> **Назначение:** Правила приёма инкассаторской сумки и её содержимого.
> **Владелец:** S5 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `collector_not_verified.py` | `COLLECTOR_NOT_VERIFIED`: инкассатор вошёл, `collection_bag` на столе, `id_document` в его сеансе не было; таймкод — сумка на столе |
| `bag_cash_no_recount.py` | `BAG_CASH_NO_RECOUNT`: `cash` из сумки попал в зону сейфа, минуя `counting_machine`; таймкод — вложение в сейф |

## Правила

- Каждый файл — класс с `@register_rule`, протокол `Rule`: `code`, `version`, `verdict_impact`, `evaluate(ctx) -> list[Candidate]`.
- Тест `tests/unit/rules/<CODE>_test.py`, фикстуры `tests/fixtures/timelines/<CODE>_{pos,neg}_*.json`.
- Кандидат не должен срабатывать на 19 чистых роликах (`uv run just eval-replay`).
- Только Python + numpy: модуль работает в режиме replay на Windows без моделей и Mac.

## Документы

- [docs/violation_catalog.md — коды нарушений и сигналов](../../../../../docs/violation_catalog.md)
- [docs/legal_sources.md — нормы и цитаты](../../../../../docs/legal_sources.md)
- [docs/structure.md — дерево, владельцы, имена](../../../../../docs/structure.md)
