"""Эталонные ответы UI (tests/fixtures/ui): договор frontend и backend (точка K0)."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts import gen_ui_examples

from cashguard.contracts.catalog import CatalogEntry
from cashguard.contracts.jobs import JobStatus, ResultLevel
from cashguard.contracts.ui import UiJobView

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "tests/fixtures/ui").glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _views() -> dict[str, UiJobView]:
    return {p.stem: UiJobView.model_validate(_load(p)) for p in EXAMPLES}


def test_ui_examples_are_fresh() -> None:
    assert gen_ui_examples.main(["--check"]) == 0, "uv run python scripts/gen_ui_examples.py"


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_ui_example_validates_and_round_trips(path: Path) -> None:
    raw = _load(path)
    assert UiJobView.model_validate(raw).model_dump(mode="json") == raw


def test_ui_examples_cover_agreed_states() -> None:
    """Пять состояний, о которых договорились frontend и backend, + демонстрационный режим."""
    views = list(_views().values())
    assert any(v.status == JobStatus.QUEUED for v in views)
    assert any(v.status == JobStatus.RUNNING for v in views)
    assert any(v.status == JobStatus.DONE and v.verdict == "no_violation" for v in views)
    multi = [v for v in views if v.verdict == "violation" and len(v.violations) >= 2]
    assert multi and all(e.legal for v in multi for e in v.violations)
    assert any(v.result_level == ResultLevel.RULES and v.degradations for v in views)
    assert any(v.status == JobStatus.FAILED and v.error is not None for v in views)
    assert any(v.pipeline_mode == "stub" for v in views)


def _catalog(code: str) -> CatalogEntry | None:
    path = ROOT / "configs/catalog" / f"{code}.yaml"
    if not path.exists():
        return None
    return CatalogEntry.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_ui_example_legal_comes_from_catalog(path: Path) -> None:
    view = UiJobView.model_validate(_load(path))
    for event in view.violations:
        entry = _catalog(event.code)
        if entry is None:
            assert event.code == "STUB_EVENT"
            assert not event.legal
            assert event.legal_note_ru, (
                "без нормы сервер прямо сообщает, что основание не установлено"
            )
            continue
        assert event.title_ru == entry.title_ru
        known = {(str(r.act_code), r.clause, r.quote_ru) for r in entry.legal}
        for ref in event.legal:
            assert (ref.act_code, ref.clause, ref.quote_ru) in known, (
                f"{event.code}: норма не из каталога"
            )
        if not event.legal or all(r.clause is None for r in event.legal):
            assert event.legal_note_ru


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_ui_example_media_urls_belong_to_job(path: Path) -> None:
    view = UiJobView.model_validate(_load(path))
    if view.video is not None:
        assert view.video.url == f"/api/ui/jobs/{view.job_id}/video.mp4"
    items = [*view.violations, *view.signals]
    for item in items:
        for ev in item.evidence:
            assert ev.url.startswith(f"/api/ui/jobs/{view.job_id}/frames/")
    times = [v.timecode_sec for v in view.violations]
    assert times == sorted(times)
