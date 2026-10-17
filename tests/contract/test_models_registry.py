"""Реестр моделей ``configs/models.yaml`` — каркас P0-S0-05 (контракт ``ModelsRegistry``).

Имена тестов содержат ``catalog``: реестр входит в приёмку P0-S0-05
(``uv run pytest tests/contract -k "legal or catalog"``).
"""

import re
from collections import Counter
from functools import cache
from pathlib import Path, PurePosixPath

import yaml

from cashguard.contracts.model_registry import ModelEntry, ModelRole, ModelsRegistry

ROOT = Path(__file__).resolve().parents[2]
MODELS_FILE = ROOT / "configs" / "models.yaml"

# Закреплённые ревизии Qwen3.5-4B (AGENTS.md, «Закреплённые версии»).
VLM_REVISIONS = {"8bit": "1704583a", "6bit": "fbb1092a", "4bit": "0e7ffd5c"}
VLM_DEFAULT_QUANT = "8bit"

# Роли YOLO: models/<role>/vNNN/... (docs/training.md, раздел 15.1).
YOLO_ROLES = frozenset(ModelRole) - {ModelRole.VLM}
# Детектор и поза работают на Core ML (Mac) и torch (Windows, Linux).
COREML_AND_TORCH_ROLES = frozenset({ModelRole.DETECTOR, ModelRole.POSE})


@cache
def _registry() -> ModelsRegistry:
    with MODELS_FILE.open(encoding="utf-8") as f:
        return ModelsRegistry.model_validate(yaml.safe_load(f))


def _entry(role: ModelRole) -> ModelEntry:
    return next(m for m in _registry().models if m.role == role)


def test_catalog_models_yaml_validates() -> None:
    assert _registry().models


def test_catalog_models_yaml_has_every_role_once() -> None:
    roles = Counter(m.role for m in _registry().models)
    assert set(roles) == set(ModelRole)
    assert all(n == 1 for n in roles.values())


def test_catalog_models_paths_relative_to_models_dir() -> None:
    for model in _registry().models:
        assert model.artifacts, f"{model.role}: нет артефактов"
        for artifact in model.artifacts:
            path = PurePosixPath(artifact.path)
            assert not path.is_absolute(), f"{model.role}: путь относительно CG_MODELS_DIR"
            assert ".." not in path.parts
            assert "\\" not in artifact.path
            assert not artifact.path.startswith("models/"), "префикс models/ задаёт CG_MODELS_DIR"


def test_catalog_models_yolo_roles_versioned_layout() -> None:
    for role in YOLO_ROLES:
        model = _entry(role)
        assert re.fullmatch(r"v\d{3}", model.version), f"{role}: версия vNNN"
        assert model.imgsz
        for artifact in model.artifacts:
            assert artifact.path.startswith(f"{role}/{model.version}/"), artifact.path
        backends = {a.backend for a in model.artifacts}
        assert "coreml" in backends, f"{role}: нужен Core ML для Mac"
        if role in COREML_AND_TORCH_ROLES:
            assert "torch" in backends, f"{role}: нужен torch для Windows и Linux"


def test_catalog_models_vlm_pins_revisions() -> None:
    vlm = _entry(ModelRole.VLM)
    assert vlm.revision == VLM_REVISIONS[VLM_DEFAULT_QUANT]
    assert {a.backend for a in vlm.artifacts} == {"mlx"}
    paths = [a.path for a in vlm.artifacts]
    for quant in VLM_REVISIONS:
        assert sum(p.endswith(f"-{quant}") for p in paths) == 1, f"нет варианта {quant}"
    by_quant = {a.quant: a for a in vlm.artifacts}
    assert set(by_quant) == set(VLM_REVISIONS), "по артефакту на каждый квант"
    for quant, revision in VLM_REVISIONS.items():
        art = by_quant[quant]  # pyright: ignore[reportArgumentType]
        assert art.revision == revision, f"ревизия {quant} не закреплена"
        assert art.repo == f"mlx-community/Qwen3.5-4B-{quant}"


def test_catalog_models_coreml_artifacts_declare_imgsz() -> None:
    for role in (ModelRole.DETECTOR, ModelRole.POSE):
        for art in _entry(role).artifacts:
            if art.backend == "coreml":
                assert art.imgsz in {"864x480", "480x864"}, f"{role}: {art.path} без imgsz"
                assert art.imgsz in art.path
