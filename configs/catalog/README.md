# `configs/catalog/` — каталог нарушений и сигналов

> **Назначение:** Одна запись на код нарушения: всё, что нужно для названия, описания, вопроса VLM и нормы.
> **Владелец:** S0 (v0), затем владелец кода; нормы проверяет S5 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `<CODE>.yaml` | `code`, `title_ru` (стабильная строка `type` для жюри), `group` (`leave\|access\|cashops\|collection\|discipline\|other`; модуль присутствия — часть `leave`), `verdict_impact`, `timecode_policy` (`event\|exit\|state_start\|omission_due`), `template_ru` (поля `str.format_map`), `required_facts`, `vlm_question_ru`, `legal`, `owner` |
| `signals.yaml` | 13 кодов `SIG_*` с формулировками «не наблюдается…»; поля `legal` нет: в v1 у сигналов норм нет ([docs/legal_sources.md](../../docs/legal_sources.md), раздел 4). v0 пишет S0, дальше — S6 через PR |

## Правила

- v0 переносится дословно из `analysis/case_analysis.md` §7 и `analysis/normative/*.txt`.
- `legal[]`: `act_code` (`PKO|POP|POO|TIB`), `act_title_ru`, `clause` (с префиксом, например `п. 73 пп. 6)`; можно опустить только для ссылки на уровне акта, FAQ 31), `quote_ru` (дословно), `url`, `strength` (`direct|indirect|none`). Первая запись — основная, её пункт идёт в описание жюри.
- У каждого кода с `verdict_impact: true` есть хотя бы одна запись `legal` с `act_code` из `configs/legal/acts.yaml`; `strength` совпадает с [docs/legal_sources.md](../../docs/legal_sources.md), разделы 5 и 9 (проверяет `tests/contract/`).
- `title_ru` после сдачи не меняется: по нему жюри сопоставляет тип.
- Полноту (правило + норма + шаблон на каждый код) проверяет `tests/contract/`.

## Документы

- [docs/violation_catalog.md — коды нарушений и сигналов](../../docs/violation_catalog.md)
- [docs/legal_sources.md — нормы и цитаты](../../docs/legal_sources.md)
- [docs/structure.md — дерево, владельцы, имена](../../docs/structure.md)
