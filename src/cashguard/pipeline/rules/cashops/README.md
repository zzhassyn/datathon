# `src/cashguard/pipeline/rules/cashops/` — кассовые операции

> **Назначение:** Правила приёма, пересчёта и выдачи наличных.
> **Владелец:** S5 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `prev_cash_on_desk.py` | `PREV_CASH_ON_DESK`: клиент A вышел, `cash` на столе, вошёл клиент B; таймкод — вход B |
| `mixed_client_cash.py` | `MIXED_CLIENT_CASH`: в сеансе B на столе две группы `cash`, одна от A; таймкод — появление второй группы |
| `no_client_id.py` | `NO_CLIENT_ID`: `cash` перешёл к кассиру, `id_document` в сеансе не было; таймкод — передача денег |
| `recount_without_client.py` | `RECOUNT_WITHOUT_CLIENT`: клиент вышел раньше, чем `cash` побывал у `counting_machine`; таймкод — выход клиента |
| `cash_withheld.py` | `CASH_WITHHELD`: у кассира две группы `cash`, клиенту передана одна; таймкод — разделение пачки |
| `no_auth_check.py` | `NO_AUTH_CHECK`: принятый `cash` ни разу не был у `auth_detector`; таймкод — конец пересчёта; трактовка спорна (ПКО п. 41) |
| `payout_without_recount.py` | `PAYOUT_WITHOUT_RECOUNT`: выдача без пересчёта при клиенте (пункт 30 списка команды) |
| `change_before_prepared.py` | `CHANGE_BEFORE_PREPARED`: размен до выдачи подготовленных денег (пункт 20) |
| `cash_concealed.py` | `CASH_CONCEALED`: деньги в карман, сумку или скрытую зону (пункт 42) |
| `cash_transfer_outside_op.py` | `CASH_TRANSFER_OUTSIDE_OP`: передача денег вне операции (пункт 43) |

## Правила

- Каждый файл — класс с `@register_rule`, протокол `Rule`: `code`, `version`, `verdict_impact`, `evaluate(ctx) -> list[Candidate]`.
- Тест `tests/unit/rules/<CODE>_test.py`, фикстуры `tests/fixtures/timelines/<CODE>_{pos,neg}_*.json`.
- Кандидат не должен срабатывать на 19 чистых роликах (`uv run just eval-replay`).
- Только Python + numpy: модуль работает в режиме replay на Windows без моделей и Mac.
- Для нарушений «по бездействию» таймкод — момент, когда шаг уже должен был случиться (`timecode_policy: omission_due`).

## Документы

- [docs/violation_catalog.md — коды нарушений и сигналов](../../../../../docs/violation_catalog.md)
- [docs/legal_sources.md — нормы и цитаты](../../../../../docs/legal_sources.md)
- [docs/structure.md — дерево, владельцы, имена](../../../../../docs/structure.md)
