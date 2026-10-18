"""Каталог v0 (P0-S0-05): полнота и форма ``configs/catalog/<CODE>.yaml`` и ``signals.yaml``.

Нормы (цитаты, сила, акты) проверяет ``test_legal.py``.
Запуск: ``uv run pytest tests/contract -k catalog``.
"""

import string
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from cashguard.contracts.catalog import (
    CatalogEntry,
    SignalCatalog,
    SignalCode,
    ViolationCode,
    ViolationGroup,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = ROOT / "configs" / "catalog"
SIGNALS_FILE = CATALOG_DIR / "signals.yaml"
RULES_DIR = ROOT / "src" / "cashguard" / "pipeline" / "rules"

# Коды без файла правила по замыслу: кандидата создаёт pipeline/verdict/decide.py
# (docs/violation_catalog.md, раздел 3.9).
NO_RULE_FILE_CODES: frozenset[ViolationCode] = frozenset({ViolationCode.OTHER_VISIBLE_IRREGULARITY})

# Коды, чьи правила ещё не написаны. Код убирается отсюда в PR своего правила
# (src/cashguard/pipeline/rules/<group>/<code>.py). К гейту G2 список должен быть пуст
# (docs/roadmap.md, P0-S0-05).
PENDING_RULE_CODES: frozenset[ViolationCode] = frozenset(
    {
        ViolationCode.CASH_WITHHELD,
        ViolationCode.NO_CLIENT_ID,
        ViolationCode.MIXED_CLIENT_CASH,
        ViolationCode.RECOUNT_WITHOUT_CLIENT,
        ViolationCode.NO_AUTH_CHECK,
        ViolationCode.SAFE_KEY_LEFT,
        ViolationCode.PC_UNLOCKED,
        ViolationCode.DOOR_NOT_CLOSED,
        ViolationCode.OUTSIDER_IN_CASH_ROOM,
        ViolationCode.COLLECTOR_NOT_VERIFIED,
        ViolationCode.BAG_CASH_NO_RECOUNT,
        ViolationCode.FOOD_DRINK,
        ViolationCode.PHONE_CASHIER,
        ViolationCode.LEFT_DURING_SERVICE,
        ViolationCode.PAYOUT_WITHOUT_RECOUNT,
        ViolationCode.CHANGE_BEFORE_PREPARED,
        ViolationCode.STORAGE_UNESCORTED,
        ViolationCode.CASH_CONCEALED,
        ViolationCode.CASH_TRANSFER_OUTSIDE_OP,
        ViolationCode.UNATTENDED_PRESENCE,
    }
)

# Владельцы по группам: AGENTS.md («Владение папками»), docs/violation_catalog.md (шапка).
OWNER_BY_GROUP: dict[ViolationGroup, str] = {
    ViolationGroup.LEAVE: "S4",
    ViolationGroup.ACCESS: "S4",
    ViolationGroup.CASHOPS: "S5",
    ViolationGroup.COLLECTION: "S5",
    ViolationGroup.DISCIPLINE: "S5",
    ViolationGroup.OTHER: "S6",
}

# Шаблон описывает только наблюдаемое: правовую фразу добавляет pipeline/legal/describe.py,
# а запрещённые слова — из docs/legal_sources.md, раздел 6.4.
LEGAL_PHRASE_MARKERS = (
    "Основание:",
    "Может свидетельствовать",
    "Прямой нормы нет",
    "НБРК",
    "АРРФР",
)
FORBIDDEN_WORDS = (
    "нарушил",
    "нарушен пункт",
    "является нарушением",
    "виновен",
    "умышленно",
    "незаконно",
    "хищени",
    "краж",
    "украл",
    "присвоил",
    "утаил",
)

PDF_ITEMS_RANGE = range(1, 56)  # список команды из 55 пунктов


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _catalog_files() -> list[Path]:
    return sorted(p for p in CATALOG_DIR.glob("*.yaml") if p != SIGNALS_FILE)


@cache
def _catalog() -> dict[ViolationCode, CatalogEntry]:
    entries: dict[ViolationCode, CatalogEntry] = {}
    for path in _catalog_files():
        entry = CatalogEntry.model_validate(_load_yaml(path))
        entries[entry.code] = entry
    return entries


@cache
def _signals() -> SignalCatalog:
    return SignalCatalog.model_validate(_load_yaml(SIGNALS_FILE))


def _placeholders(template: str) -> list[str]:
    return [field for _, field, _, _ in string.Formatter().parse(template) if field is not None]


def _check_template(template: str, required_facts: list[str]) -> None:
    fields = _placeholders(template)
    assert all(f.isidentifier() for f in fields), f"поле шаблона — простое имя: {fields}"
    missing = set(fields) - set(required_facts)
    assert not missing, f"поля шаблона не перечислены в required_facts: {sorted(missing)}"
    unused = set(required_facts) - set(fields)
    assert not unused, f"required_facts не используются в шаблоне: {sorted(unused)}"
    assert len(required_facts) == len(set(required_facts)), "повтор в required_facts"
    rendered = template.format_map({f: "1,0" for f in required_facts})
    assert "{" not in rendered
    assert "}" not in rendered


# ---------------------------------------------------------------- коды нарушений


def test_catalog_has_one_file_per_violation_code() -> None:
    stems = [p.stem for p in _catalog_files()]
    assert sorted(stems) == sorted(str(c) for c in ViolationCode)
    assert not list(CATALOG_DIR.glob("*.yml")), "расширение каталога — только .yaml"


@pytest.mark.parametrize("path", _catalog_files(), ids=lambda p: p.stem)
def test_catalog_entry_validates_and_matches_file_name(path: Path) -> None:
    entry = CatalogEntry.model_validate(_load_yaml(path))
    assert entry.code == path.stem


@pytest.mark.parametrize("code", list(ViolationCode))
def test_catalog_entry_has_title_template_and_timecode(code: ViolationCode) -> None:
    entry = _catalog()[code]
    assert entry.title_ru.strip() == entry.title_ru
    assert entry.title_ru
    assert entry.template_ru.strip()
    assert entry.timecode_policy


@pytest.mark.parametrize("code", list(ViolationCode))
def test_catalog_template_placeholders_match_required_facts(code: ViolationCode) -> None:
    entry = _catalog()[code]
    _check_template(entry.template_ru, entry.required_facts)


@pytest.mark.parametrize("code", list(ViolationCode))
def test_catalog_template_has_no_legal_text_or_forbidden_words(code: ViolationCode) -> None:
    template = _catalog()[code].template_ru
    for marker in LEGAL_PHRASE_MARKERS:
        assert marker not in template, f"правовую фразу добавляет describe.py: {marker!r}"
    lowered = template.lower()
    for word in FORBIDDEN_WORDS:
        assert word not in lowered, f"запрещённое слово в шаблоне: {word!r}"


def test_catalog_title_ru_unique() -> None:
    titles = Counter(e.title_ru for e in _catalog().values())
    assert [t for t, n in titles.items() if n > 1] == []


def test_catalog_change_before_prepared_not_in_verdict() -> None:
    # ПКО п. 34 нет в analysis/case_analysis.md, раздел 7 (docs/legal_sources.md, раздел 9).
    assert _catalog()[ViolationCode.CHANGE_BEFORE_PREPARED].verdict_impact is False


@pytest.mark.parametrize("code", list(ViolationCode))
def test_catalog_owner_matches_group(code: ViolationCode) -> None:
    entry = _catalog()[code]
    assert entry.owner == OWNER_BY_GROUP[entry.group]


@pytest.mark.parametrize("code", list(ViolationCode))
def test_catalog_vlm_question_and_pdf_items(code: ViolationCode) -> None:
    entry = _catalog()[code]
    if entry.vlm_question_ru is not None:
        assert entry.vlm_question_ru.endswith("?"), "вопрос VLM — да/нет, заканчивается «?»"
    assert all(i in PDF_ITEMS_RANGE for i in entry.pdf_items)
    assert len(entry.pdf_items) == len(set(entry.pdf_items))


@pytest.mark.parametrize("code", list(ViolationCode))
def test_catalog_code_has_rule_module_or_is_pending(code: ViolationCode) -> None:
    entry = _catalog()[code]
    rule_file = RULES_DIR / entry.group / f"{code.lower()}.py"
    if code in NO_RULE_FILE_CODES:
        assert code not in PENDING_RULE_CODES
        assert not rule_file.exists(), f"{code}: файла правила быть не должно"
    elif rule_file.exists():
        assert code not in PENDING_RULE_CODES, (
            f"{code}: правило есть, уберите из PENDING_RULE_CODES"
        )
    else:
        assert code in PENDING_RULE_CODES, f"{code}: нет {rule_file.relative_to(ROOT)}"


# ---------------------------------------------------------------- сигналы


def test_catalog_signals_cover_every_signal_code_once() -> None:
    codes = Counter(s.code for s in _signals().signals)
    assert set(codes) == set(SignalCode)
    assert all(n == 1 for n in codes.values())


@pytest.mark.parametrize("code", list(SignalCode))
def test_catalog_signal_text_and_placeholders(code: SignalCode) -> None:
    signal = next(s for s in _signals().signals if s.code == code)
    assert signal.title_ru.strip() == signal.title_ru
    assert signal.title_ru
    assert "не наблюдается" in signal.template_ru.lower(), (
        "формулировка сигнала — «не наблюдается…»"
    )
    _check_template(signal.template_ru, signal.required_facts)
    for marker in LEGAL_PHRASE_MARKERS:
        assert marker not in signal.template_ru, "у сигналов в v1 норм нет"
    assert signal.pdf_items
    assert all(i in PDF_ITEMS_RANGE for i in signal.pdf_items)


def test_catalog_signal_titles_unique_and_distinct_from_violations() -> None:
    titles = [s.title_ru for s in _signals().signals]
    assert len(titles) == len(set(titles))
    assert not set(titles) & {e.title_ru for e in _catalog().values()}
