"""Генерация JSON Schema контрактов и OpenAPI для web. Проверка дрейфа.

    uv run python -m cashguard.contracts.export          # перегенерировать
    uv run python -m cashguard.contracts.export --check  # упасть, если файлы устарели

Без ML-зависимостей: импортирует только контракты и FastAPI.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from cashguard.contracts.catalog import ActsFile, CatalogEntry, SignalCatalog
from cashguard.contracts.findings import AnalysisResult
from cashguard.contracts.health import AdminStatus, ReadyReport
from cashguard.contracts.jury import JuryJobResponse
from cashguard.contracts.model_registry import ModelsRegistry
from cashguard.contracts.perception import PerceptionTrace
from cashguard.contracts.scene import SceneModel
from cashguard.contracts.surface import build_contract_app
from cashguard.contracts.ui import UiCatalog, UiJobView

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[2]
GENERATED_DIR = PACKAGE_DIR / "schemas" / "generated"
OPENAPI_PATH = REPO_ROOT / "web" / "src" / "shared" / "api" / "openapi.json"

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "JuryJobResponse": JuryJobResponse,
    "PerceptionTrace": PerceptionTrace,
    "SceneModel": SceneModel,
    "AnalysisResult": AnalysisResult,
    "UiJobView": UiJobView,
    "UiCatalog": UiCatalog,
    "CatalogEntry": CatalogEntry,
    "SignalCatalog": SignalCatalog,
    "ActsFile": ActsFile,
    "ModelsRegistry": ModelsRegistry,
    "ReadyReport": ReadyReport,
    "AdminStatus": AdminStatus,
}


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_all() -> dict[Path, str]:
    """Все генерируемые файлы: путь → содержимое."""
    out: dict[Path, str] = {}
    for name, model in SCHEMA_MODELS.items():
        schema = model.model_json_schema(mode="serialization")
        out[GENERATED_DIR / f"{name}.schema.json"] = _dump(schema)
    out[OPENAPI_PATH] = _dump(build_contract_app().openapi())
    return out


def _stale_files(expected: dict[Path, str]) -> list[Path]:
    stale = [
        path
        for path, text in expected.items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]
    known = set(expected)
    stale += [p for p in GENERATED_DIR.glob("*.schema.json") if p not in known]
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="проверить дрейф, ничего не писать")
    args = parser.parse_args(argv)

    expected = render_all()
    stale = _stale_files(expected)
    if args.check:
        if stale:
            for path in stale:
                print(f"устарел: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            print("запустите: uv run just schemas", file=sys.stderr)
            return 1
        print(f"схемы актуальны ({len(expected)} файлов)")
        return 0

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    for path in stale:
        if path in expected:
            path.write_text(expected[path], encoding="utf-8", newline="\n")
        else:
            path.unlink()
    print(f"записано {len(stale)} из {len(expected)} файлов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
