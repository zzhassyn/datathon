import math

import pytest

from cashguard.core.atomic import atomic_write_bytes, atomic_write_json, durable_replace
from cashguard.core.budget import Budget, DeadlineTiers
from cashguard.core.env import OFFLINE_ENV, apply_offline_env
from cashguard.core.ids import new_job_id, valid_job_id
from cashguard.core.logging import redact_secrets
from cashguard.core.paths import RuntimePaths, safe_child
from cashguard.core.settings import Settings
from cashguard.core.versions import config_hash


def test_ids_and_runtime_paths(tmp_path):
    job_id = new_job_id()
    assert valid_job_id(job_id)
    paths = RuntimePaths(tmp_path)
    paths.ensure()
    assert paths.input_path(job_id) == tmp_path / "jobs" / job_id / "input.mov"
    assert paths.db_path.parent.is_dir()
    for bad in ("../bad", "A" * 32, "", "a" * 31):
        with pytest.raises(ValueError):
            paths.job_dir(bad)
    with pytest.raises(ValueError):
        safe_child(tmp_path, "..", "escape")


def test_symlink_cannot_escape_runtime(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_child(tmp_path, "linked", "secret")


def test_atomic_write_and_no_nan(tmp_path):
    path = tmp_path / "result.json"
    atomic_write_json(path, {"a": 1})
    assert path.read_text() == '{"a":1}'
    with pytest.raises(ValueError):
        atomic_write_json(path, {"bad": math.nan})
    assert path.read_text() == '{"a":1}'
    assert list(tmp_path.glob("*.tmp")) == []
    source = atomic_write_bytes(tmp_path / "upload.part", b"content")
    durable_replace(source, tmp_path / "upload.bin")
    assert not source.exists()


def test_budget_uses_monotonic_and_preserves_request_origin():
    mono = [10.0]
    wall = [150.0]
    budget = Budget(
        DeadlineTiers.from_received_at(100),
        wall_clock=lambda: wall[0],
        monotonic=lambda: mono[0],
    )
    assert budget.remaining("vision_target") == 100
    wall[0] += 10_000
    mono[0] += 5
    assert budget.now() == 155
    assert budget.remaining("vision_target") == 95
    assert budget.allows(100, margin_sec=0)
    assert not budget.allows(125, margin_sec=0)
    mono[0] += 1000
    assert budget.remaining() == 0
    for value in (math.nan, -1):
        with pytest.raises(ValueError):
            budget.allows(value)


def test_settings_environment_and_redaction(monkeypatch, tmp_path):
    monkeypatch.setenv("CG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CG_API_KEYS", "jury:secret-one,team:secret-two")
    settings = Settings()
    assert settings.var_dir == tmp_path
    assert settings.parsed_api_keys() == {"jury": "secret-one", "team": "secret-two"}
    assert "secret-one" not in repr(settings)
    assert settings.max_upload_bytes == 100_000_000
    assert Settings(var_dir=tmp_path / "override").var_dir == tmp_path / "override"
    result = redact_secrets(
        None,
        "info",
        {"job_id": "ok", "headers": {"X-API-Key": "secret"}, "image_base64": "/9j/"},
    )
    assert result["headers"]["X-API-Key"] == "[redacted]"
    assert result["image_base64"] == "[redacted]"


def test_offline_environment_and_stable_config_hash():
    environ = {"HF_HUB_OFFLINE": "0"}
    apply_offline_env(environ)
    assert environ == OFFLINE_ENV
    assert config_hash({"b": 2, "a": 1}) == config_hash({"a": 1, "b": 2})
    assert config_hash({"a": 1}) != config_hash({"a": 2})


def test_jury_ui_profile_preserves_explicit_overrides(monkeypatch):
    settings = Settings(ui_rate_profile="jury")
    assert (
        settings.ui_rate_uploads,
        settings.ui_rate_active,
        settings.ui_rate_queue,
    ) == (30, 5, 20)
    assert Settings(ui_rate_profile="jury", ui_rate_uploads=7).ui_rate_uploads == 7
    monkeypatch.setenv("CG_UI_RATE_PROFILE", "jury")
    monkeypatch.setenv("CG_UI_RATE_ACTIVE", "8")
    from_env = Settings()
    assert from_env.ui_rate_active == 8
    assert from_env.ui_rate_uploads == 30
