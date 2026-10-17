# `src/cashguard/pipeline/legal/` — нормы и описания

> **Назначение:** Загрузка каталога и актов, сборка описания нарушения с фразой о норме.
> **Владелец:** S5 · **Статус:** черновик v1, 17.10.2026 (каркас: кода ещё нет)

## Планируемые файлы

| Файл | Что делает |
|---|---|
| `catalog.py` | загрузка и проверка `configs/catalog/*.yaml` и `configs/legal/acts.yaml` |
| `describe.py` | `legal.describe(decision, scene, catalog) -> tuple[str, list[LegalRef]]`: `template_ru` через `str.format_map` + фраза о норме |

## Правила

- Фраза по силе нормы — четыре шаблона [docs/legal_sources.md](../../../../docs/legal_sources.md), раздел 6.2: прямая — `Основание: {clause} {act_cite}.`; косвенная — `Может свидетельствовать о несоблюдении {clause} {act_cite}.`; нет нормы — `Прямой нормы нет; ближайшая — {clause} {act_cite}.`; пункт не определён (FAQ 31) — `Прямой нормы нет; пункт {act_cite} не определён (FAQ 31); требуется проверка человеком.` `{clause}` хранится уже с «п.», `{act_cite}` — поле `cite_ru` акта.
- Цитаты дословно из `analysis/normative/*.txt`, акты — только из `analysis/case_analysis.md` §7. Утратившие силу (НБРК № 58, № 250, № 48, № 254) не цитировать.
- Описание — из фактов правила, без свободной генерации VLM и без jinja2.
- Тесты — `tests/unit/legal/` и snapshot описаний (syrupy). Только Python + numpy: модуль работает в режиме replay на Windows без моделей и Mac.

## Документы

- [docs/legal_sources.md — нормы и цитаты](../../../../docs/legal_sources.md)
- [docs/violation_catalog.md — коды нарушений и сигналов](../../../../docs/violation_catalog.md)
