# `src/cashguard/pipeline/rules/signals/` — сигналы

> **Назначение:** Наблюдения для проверяющего. Не влияют на вердикт, не попадают в ответ жюри, видны только в блоке интерфейса «Наблюдения (не влияют на вердикт)».
> **Владелец:** S6 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `sig_seal_not_inspected.py` | `SIG_SEAL_NOT_INSPECTED`: не видно осмотра пломбы (пункт 17 списка команды) |
| `sig_bag_opened_without_collector.py` | `SIG_BAG_OPENED_WITHOUT_COLLECTOR`: сумку вскрывают без инкассатора (пункт 18) |
| `sig_recount_without_second_employee.py` | `SIG_RECOUNT_WITHOUT_SECOND_EMPLOYEE`: пересчёт без второго сотрудника (пункт 19) |
| `sig_staff_cash_transfer.py` | `SIG_STAFF_CASH_TRANSFER`: передача денег между сотрудниками (пункт 22) |
| `sig_doc_not_handed.py` | `SIG_DOC_NOT_HANDED`: клиенту не передан документ (пункт 23) |
| `sig_reject_tray_to_client.py` | `SIG_REJECT_TRAY_TO_CLIENT`: банкнота из лотка отбраковки ушла клиенту (пункт 25) |
| `sig_no_signature_seen.py` | `SIG_NO_SIGNATURE_SEEN`: не видно подписи клиента (пункт 27) |
| `sig_no_stamp_seen.py` | `SIG_NO_STAMP_SEEN`: не видно штампа кассира (пункт 28) |
| `sig_multi_doc_single_recount.py` | `SIG_MULTI_DOC_SINGLE_RECOUNT`: несколько документов — один пересчёт (пункт 29) |
| `sig_safe_closed_without_count.py` | `SIG_SAFE_CLOSED_WITHOUT_COUNT`: сейф закрыт без пересчёта остатка (пункт 36) |
| `sig_safe_without_pc.py` | `SIG_SAFE_WITHOUT_PC`: вложение в сейф без работы за компьютером (пункт 40) |
| `sig_cash_to_staff.py` | `SIG_CASH_TO_STAFF`: деньги сотруднику вне операции (пункт 44) |
| `sig_camera_obstructed.py` | `SIG_CAMERA_OBSTRUCTED`: камера перекрыта, потеря видимости (пункт 45) |

## Правила

- Каждый файл — класс с `@register_rule`, протокол `SignalRule`: `code`, `evaluate(ctx) -> list[Signal]`.
- Формулировки «не наблюдается…» — в `configs/catalog/signals.yaml`.
- Сигналы выводятся, потому что срабатывают на чистых роликах или подтверждение лежит вне кадра (`analysis/detection_design.md`, группа B2).
- Только Python + numpy: модуль работает в режиме replay на Windows без моделей и Mac.

## Документы

- [docs/violation_catalog.md — коды нарушений и сигналов](../../../../../docs/violation_catalog.md)
- [docs/structure.md — дерево, владельцы, имена](../../../../../docs/structure.md)
