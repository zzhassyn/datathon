# `src/cashguard/pipeline/rules/` — движок правил

> **Назначение:** Правила по процедурам: из `RuleContext` выдают кандидатов нарушений и сигналы.
> **Владелец:** S0 (`base.py`, `registry.py`, `context.py`) → S4; группы — по таблице · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `base.py` | базовые классы правил и сигналов |
| `registry.py` | `@register_rule`, поиск через `pkgutil`; `rules.evaluate_all(ctx)` |
| `context.py` | помощники над `RuleContext` (сцена, трасса, параметры) |

## Группы

| Папка | Владелец | Коды |
|---|---|---|
| `leave/` | S4 | уход с рабочего места, присутствие |
| `access/` | S4 | доступ в кассу |
| `cashops/` | S5 | кассовые операции |
| `collection/` | S5 | инкассация |
| `discipline/` | S5 | дисциплина |
| `signals/` | S6 | 13 сигналов `SIG_*` (не влияют на вердикт) |

## Правила

- Один файл на код, имя — код в нижнем регистре. Центрального списка нет: правило находит `pkgutil` по `@register_rule`.
- Подпакеты групп не импортируют друг друга. Только Python + numpy: модуль работает в режиме replay на Windows без моделей и Mac.
- Пороги — `configs/rules/<CODE>.yaml`, тексты и нормы — `configs/catalog/<CODE>.yaml`.
- Порядок добавления типа — в `docs/structure.md`.

## Документы

- [docs/violation_catalog.md — коды нарушений и сигналов](../../../../docs/violation_catalog.md)
- [docs/structure.md — дерево, владельцы, имена](../../../../docs/structure.md)
- [docs/pipeline.md — этапы обработки](../../../../docs/pipeline.md)
