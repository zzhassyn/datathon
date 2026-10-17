"""Загрузка и проверка каталога: ``configs/catalog/*.yaml`` и ``configs/legal/acts.yaml``.

Нормы берутся только отсюда (проверенный каталог, контрактные тесты P0-S0-05). Если у кода нет
нормы, интерфейс получает ``legal=[]`` и честное ``legal_note_ru`` — сервер ничего не придумывает.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from cashguard.contracts.catalog import (
    ActsFile,
    CatalogEntry,
    LegalRef,
    SignalCatalog,
    ViolationCode,
)
from cashguard.contracts.ui import UiCatalog, UiCatalogSignal, UiCatalogType, UiLegalRef

DEFAULT_CONFIGS_DIR = Path(__file__).resolve().parents[4] / "configs"


@dataclass(frozen=True)
class Catalog:
    entries: dict[ViolationCode, CatalogEntry]
    signals: SignalCatalog
    acts: ActsFile
    config_hash: str
    """sha256 нормализованного содержимого (первые 16 hex) — ETag каталога."""


def load_catalog(configs_dir: Path = DEFAULT_CONFIGS_DIR) -> Catalog:
    """Читает и проверяет все файлы каталога. Падает при любом расхождении с контрактом."""
    digest = hashlib.sha256()
    entries: dict[ViolationCode, CatalogEntry] = {}
    signals: SignalCatalog | None = None
    files = [*sorted((configs_dir / "catalog").glob("*.yaml")), configs_dir / "legal" / "acts.yaml"]
    acts: ActsFile | None = None
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        digest.update(path.relative_to(configs_dir).as_posix().encode())
        digest.update(json.dumps(data, sort_keys=True, ensure_ascii=False).encode())
        if path.parent.name == "legal":
            acts = ActsFile.model_validate(data)
        elif path.name == "signals.yaml":
            signals = SignalCatalog.model_validate(data)
        else:
            entry = CatalogEntry.model_validate(data)
            if path.stem != entry.code:
                msg = f"{path.name}: имя файла должно совпадать с кодом {entry.code}"
                raise ValueError(msg)
            entries[entry.code] = entry
    missing = set(ViolationCode) - set(entries)
    if missing:
        msg = f"в каталоге нет кодов: {sorted(missing)}"
        raise ValueError(msg)
    if signals is None or acts is None:
        msg = "в каталоге нет signals.yaml или configs/legal/acts.yaml"
        raise ValueError(msg)
    return Catalog(entries, signals, acts, digest.hexdigest()[:16])


def ui_legal(ref: LegalRef) -> UiLegalRef:
    return UiLegalRef(
        act_code=str(ref.act_code),
        act_title_ru=ref.act_title_ru,
        clause=ref.clause,
        quote_ru=ref.quote_ru,
        url=ref.url,
        strength=ref.strength,
        note_ru=ref.note_ru,
    )


def legal_for(entry: CatalogEntry, facts: set[str]) -> list[UiLegalRef]:
    """Нормы кода с учётом ``applies_if``: условная норма — только при истинном факте."""
    return [ui_legal(r) for r in entry.legal if r.applies_if is None or r.applies_if in facts]


def to_ui_catalog(catalog: Catalog) -> UiCatalog:
    types = [
        UiCatalogType(
            code=entry.code,
            title_ru=entry.title_ru,
            group=entry.group,
            verdict_impact=entry.verdict_impact,
            legal=[ui_legal(r) for r in entry.legal],
        )
        for entry in sorted(catalog.entries.values(), key=lambda e: (e.group, e.code))
    ]
    signals = [UiCatalogSignal(code=s.code, title_ru=s.title_ru) for s in catalog.signals.signals]
    return UiCatalog(
        config_hash=catalog.config_hash, types=types, signals=signals, acts=catalog.acts.acts
    )
