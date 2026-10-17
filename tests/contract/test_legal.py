"""Нормы каталога v0 (P0-S0-05): акты, дословные цитаты, сила связи, утратившие силу акты.

Правило: акты и пункты — только из ``analysis/case_analysis.md`` §7, цитаты — дословно из
``analysis/normative/*.txt`` (AGENTS.md, правило 8).
Запуск: ``uv run pytest tests/contract -k legal``.
"""

import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from cashguard.contracts.catalog import (
    ActCode,
    ActsFile,
    ActStatus,
    CatalogEntry,
    LegalRef,
    LegalStrength,
    ViolationCode,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = ROOT / "configs" / "catalog"
SIGNALS_FILE = CATALOG_DIR / "signals.yaml"
LEGAL_DIR = ROOT / "configs" / "legal"
ACTS_FILE = LEGAL_DIR / "acts.yaml"
NORMATIVE_DIR = ROOT / "analysis" / "normative"

D = LegalStrength.DIRECT
I = LegalStrength.INDIRECT  # noqa: E741
N = LegalStrength.NONE
PKO, POP, POO, TIB = ActCode.PKO, ActCode.POP, ActCode.POO, ActCode.TIB

# Ожидаемые записи ``legal`` по кодам: (акт, пункт, сила) в порядке строк таблиц.
# Источник — docs/legal_sources.md: раздел 5.1 (типы датасета), 5.2 (B1) и раздел 9 (пробелы).
# Первая запись — основная (её пункт идёт в описание жюри).
# Пункт None — ссылка на уровне акта (FAQ 31).
# Меняется только вместе с docs/legal_sources.md в одном PR (раздел 10 там же).
EXPECTED_LEGAL: dict[ViolationCode, list[tuple[ActCode, str | None, LegalStrength]]] = {
    # --- раздел 5.1
    ViolationCode.NO_CLIENT_ID: [(PKO, "п. 19 пп. 1)", D), (PKO, "п. 31 пп. 1)", D)],
    ViolationCode.RECOUNT_WITHOUT_CLIENT: [(PKO, "п. 20 ч. 1", D)],
    ViolationCode.CASH_WITHHELD: [(PKO, "п. 31 пп. 4)", D), (PKO, "п. 35", D), (PKO, "п. 14", I)],
    # По §7 прямая; решение по умолчанию — косвенная из-за исключения п. 41.
    ViolationCode.NO_AUTH_CHECK: [(PKO, "п. 19 пп. 2)", I), (PKO, "п. 9", I), (PKO, "п. 41", I)],
    ViolationCode.PREV_CASH_ON_DESK: [(PKO, "п. 20 ч. 2", D), (PKO, "п. 20 ч. 2", D)],
    ViolationCode.MIXED_CLIENT_CASH: [(PKO, "п. 20 ч. 2", D), (PKO, "п. 21", D)],
    ViolationCode.VALUABLES_LEFT: [(PKO, "п. 20 ч. 2", D), (PKO, "п. 22", D), (PKO, "п. 80", I)],
    ViolationCode.SAFE_OPEN: [(PKO, "п. 20 ч. 2", I)],
    ViolationCode.SAFE_KEY_LEFT: [(PKO, "п. 80", I)],
    ViolationCode.PC_UNLOCKED: [(TIB, "п. 73 пп. 6)", I), (TIB, "п. 71 пп. 9)", I)],
    ViolationCode.DOOR_NOT_CLOSED: [(POP, "п. 16 пп. 1)", I), (POP, "п. 8", I)],
    ViolationCode.OUTSIDER_IN_CASH_ROOM: [
        (POP, "п. 8", I),
        (POP, "п. 16 пп. 1)", I),
        (PKO, "п. 75", I),
        (POO, "п. 41", I),
    ],
    ViolationCode.THIRD_PERSON_DURING_SERVICE: [
        (POP, "п. 8", I),
        (POP, "п. 16 пп. 1)", I),
        (PKO, "п. 75", I),
        (POO, "п. 41", I),
    ],
    # По §7 п. 24 пп. 1) прямая; решение по умолчанию — косвенная (видно только непредъявление).
    ViolationCode.COLLECTOR_NOT_VERIFIED: [
        (PKO, "п. 24 пп. 1)", I),
        (PKO, "п. 24 пп. 3)", D),
        (PKO, "п. 24 пп. 4)", D),
        (PKO, "п. 101", I),
    ],
    ViolationCode.BAG_CASH_NO_RECOUNT: [(PKO, "п. 41", I), (PKO, "п. 42", I), (PKO, "п. 25", I)],
    ViolationCode.PERSONAL_ITEMS: [(PKO, "п. 12 ч. 1", D)],
    ViolationCode.FOOD_DRINK: [(PKO, "п. 12 ч. 1", N)],
    # --- раздел 5.2
    ViolationCode.LEFT_DURING_SERVICE: [(PKO, "п. 22", D)],
    ViolationCode.PAYOUT_WITHOUT_RECOUNT: [(PKO, "п. 31 пп. 4)", D)],
    ViolationCode.PHONE_CASHIER: [(PKO, "п. 12 ч. 2", I)],
    ViolationCode.STORAGE_UNESCORTED: [(PKO, "п. 75", I)],
    # --- раздел 9: нормы нет или она не сверена
    ViolationCode.CHANGE_BEFORE_PREPARED: [],  # ПКО п. 34 ещё не внесён в §7; verdict_impact: false
    ViolationCode.CASH_CONCEALED: [(PKO, None, N), (PKO, "п. 14", N)],
    ViolationCode.CASH_TRANSFER_OUTSIDE_OP: [(PKO, "п. 15", N)],
    ViolationCode.UNATTENDED_PRESENCE: [(PKO, None, N)],
    ViolationCode.OTHER_VISIBLE_IRREGULARITY: [(PKO, None, N)],
}

# Пункты, которые analysis/case_analysis.md, раздел 7, сопоставляет с типами нарушений
# (таблица «тип → норма» и подсказки). Других пунктов в каталоге быть не может.
SECTION7_CLAUSES: dict[ActCode, frozenset[str]] = {
    PKO: frozenset(
        {
            "п. 9",
            "п. 12 ч. 1",
            "п. 12 ч. 2",
            "п. 14",
            "п. 15",
            "п. 19 пп. 1)",
            "п. 19 пп. 2)",
            "п. 20 ч. 1",
            "п. 20 ч. 2",
            "п. 21",
            "п. 22",
            "п. 24 пп. 1)",
            "п. 24 пп. 3)",
            "п. 24 пп. 4)",
            "п. 25",
            "п. 31 пп. 1)",
            "п. 31 пп. 4)",
            "п. 35",
            "п. 41",
            "п. 42",
            "п. 75",
            "п. 80",
            "п. 101",
        }
    ),
    POP: frozenset({"п. 8", "п. 16 пп. 1)"}),
    POO: frozenset({"п. 41"}),
    TIB: frozenset({"п. 71 пп. 9)", "п. 73 пп. 6)"}),
}

# Утратившие силу акты (AGENTS.md, правило 8; docs/legal_sources.md, раздел 8.1):
# НБРК № 58 от 03.03.2001 (рег. 1482), № 250 от 24.08.2012 (рег. 8080),
# № 48 от 27.03.2018 (рег. 16772), № 254 от 24.08.2012 (рег. 7995).
REPEALED_NUMBERS = ("58", "250", "48", "254")
REPEALED_PATTERNS = (
    re.compile(r"№\s*(?:58|250|48|254)(?!\d)"),
    re.compile(r"03\.03\.2001|24\.08\.2012|27\.03\.2018"),
    re.compile(r"3 марта 2001|24 августа 2012|27 марта 2018"),
    re.compile(r"(?<!\d)(?:1482|8080|16772|7995)(?!\d)"),
)


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _norm(text: str) -> str:
    """Схлопывает любые пробельные символы (включая неразрывные и переводы строк) в один пробел."""
    return re.sub(r"\s+", " ", text).strip()


@cache
def _acts() -> ActsFile:
    return ActsFile.model_validate(_load_yaml(ACTS_FILE))


@cache
def _catalog() -> dict[ViolationCode, CatalogEntry]:
    entries: dict[ViolationCode, CatalogEntry] = {}
    for path in sorted(CATALOG_DIR.glob("*.yaml")):
        if path == SIGNALS_FILE:
            continue
        entry = CatalogEntry.model_validate(_load_yaml(path))
        entries[entry.code] = entry
    return entries


def _normative_files(act: ActCode) -> list[Path]:
    return sorted(NORMATIVE_DIR.glob(f"{act}_*.txt"))


@cache
def _normative_text(act: ActCode) -> str:
    files = _normative_files(act)
    assert files, f"нет текста акта {act} в analysis/normative/"
    return "\n".join(_norm(p.read_text(encoding="utf-8")) for p in files)


def _all_refs() -> list[tuple[str, LegalRef]]:
    return [
        (f"{code}-{i}", ref)
        for code, entry in sorted(_catalog().items())
        for i, ref in enumerate(entry.legal)
    ]


def _acts_text_before_do_not_cite() -> str:
    raw = ACTS_FILE.read_text(encoding="utf-8")
    top_keys = re.findall(r"^([A-Za-z_]+):", raw, flags=re.MULTILINE)
    assert top_keys[-1] == "do_not_cite", "do_not_cite — последний блок acts.yaml"
    return raw.split("\ndo_not_cite:", 1)[0]


# ---------------------------------------------------------------- акты


def test_legal_acts_yaml_covers_every_act_once() -> None:
    acts = _acts().acts
    assert sorted(a.act_code for a in acts) == sorted(ActCode)
    for act in acts:
        assert act.status is ActStatus.IN_FORCE, (
            f"{act.act_code}: в каталоге только действующие акты"
        )
        assert act.url == f"https://adilet.zan.kz/rus/docs/{act.adilet_id}"
        assert act.cite_ru.strip()
        assert act.number in act.cite_ru


@pytest.mark.parametrize("act_code", list(ActCode))
def test_legal_act_title_verbatim_in_normative_text(act_code: ActCode) -> None:
    act = next(a for a in _acts().acts if a.act_code == act_code)
    assert any(act.adilet_id in p.name for p in _normative_files(act_code))
    assert _norm(act.title_ru) in _normative_text(act_code)


def test_legal_poo_scope_is_exchange_point_only() -> None:
    poo = next(a for a in _acts().acts if a.act_code == ActCode.POO)
    assert poo.scope_note_ru is not None
    assert "обменного пункта" in poo.scope_note_ru


def test_legal_do_not_cite_lists_repealed_acts() -> None:
    entries = _acts().do_not_cite
    assert len(entries) == len(REPEALED_NUMBERS)
    for number in REPEALED_NUMBERS:
        hits = [e for e in entries if re.search(rf"№\s*{number}(?!\d)", e.title_ru)]
        assert len(hits) == 1, f"НБРК № {number} должен быть в do_not_cite ровно один раз"
        assert hits[0].reason_ru.strip()


# ---------------------------------------------------------------- ссылки каталога


@pytest.mark.parametrize(("ref_id", "ref"), _all_refs(), ids=[r[0] for r in _all_refs()])
def test_legal_quote_verbatim_in_normative_text(ref_id: str, ref: LegalRef) -> None:
    if ref.quote_ru is None:
        assert ref.clause is None, f"{ref_id}: у ссылки на пункт нужна цитата"
        return
    assert "…" not in ref.quote_ru, f"{ref_id}: цитата непрерывная, без «…»"
    assert "..." not in ref.quote_ru, f"{ref_id}: цитата непрерывная, без «...»"
    assert _norm(ref.quote_ru) in _normative_text(ref.act_code), (
        f"{ref_id}: цитата не найдена дословно в analysis/normative/{ref.act_code}_*.txt"
    )


@pytest.mark.parametrize(("ref_id", "ref"), _all_refs(), ids=[r[0] for r in _all_refs()])
def test_legal_ref_matches_acts_yaml(ref_id: str, ref: LegalRef) -> None:
    acts = {a.act_code: a for a in _acts().acts}
    assert ref.act_code in acts, f"{ref_id}: акта нет в acts.yaml"
    assert ref.url == acts[ref.act_code].url, f"{ref_id}: url не совпадает с acts.yaml"
    assert ref.act_title_ru == acts[ref.act_code].title_ru, f"{ref_id}: название акта"


@pytest.mark.parametrize(("ref_id", "ref"), _all_refs(), ids=[r[0] for r in _all_refs()])
def test_legal_clause_from_section7_or_faq31(ref_id: str, ref: LegalRef) -> None:
    if ref.clause is None:
        assert ref.note_ru is not None
        assert "FAQ 31" in ref.note_ru, f"{ref_id}: без пункта — только со ссылкой на FAQ 31"
        assert ref.strength is LegalStrength.NONE
    else:
        assert ref.clause in SECTION7_CLAUSES[ref.act_code], (
            f"{ref_id}: {ref.act_code} {ref.clause} нет в analysis/case_analysis.md, раздел 7"
        )


@pytest.mark.parametrize("code", list(ViolationCode))
def test_legal_verdict_code_has_norm_from_acts_yaml(code: ViolationCode) -> None:
    entry = _catalog()[code]
    if not entry.verdict_impact:
        return
    known = {a.act_code for a in _acts().acts}
    assert entry.legal, f"{code}: код вердикта без записи legal"
    assert all(ref.act_code in known for ref in entry.legal)


def test_legal_expected_mapping_covers_every_code() -> None:
    assert set(EXPECTED_LEGAL) == set(ViolationCode)


@pytest.mark.parametrize("code", list(ViolationCode))
def test_legal_strength_matches_legal_sources(code: ViolationCode) -> None:
    actual = [(ref.act_code, ref.clause, ref.strength) for ref in _catalog()[code].legal]
    assert actual == EXPECTED_LEGAL[code], (
        f"{code}: legal расходится с docs/legal_sources.md, разделы 5 и 9"
    )


# ---------------------------------------------------------------- утратившие силу акты


def _texts_to_scan() -> list[tuple[str, str]]:
    texts = [
        (p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8"))
        for p in sorted(CATALOG_DIR.glob("*.yaml"))
    ]
    texts += [
        (p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8"))
        for p in sorted(LEGAL_DIR.glob("*.yaml"))
        if p != ACTS_FILE
    ]
    texts.append(("configs/legal/acts.yaml#before-do_not_cite", _acts_text_before_do_not_cite()))
    return texts


@pytest.mark.parametrize(("name", "text"), _texts_to_scan(), ids=[t[0] for t in _texts_to_scan()])
def test_legal_no_repealed_acts_outside_do_not_cite(name: str, text: str) -> None:
    for pattern in REPEALED_PATTERNS:
        match = pattern.search(text)
        assert match is None, f"{name}: упоминание утратившего силу акта: {match.group(0)!r}"
