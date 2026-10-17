import pytest
from fastapi.testclient import TestClient

from cashguard.api.main import create_app
from cashguard.core.settings import Settings
from scripts.make_synthetic_mov import make_synthetic_mov


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "runtime",
        api_keys="jury:test-jury,team:test-team",
        min_free_disk_gb=0,
        min_free_mem_gb=0,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def synthetic(tmp_path):
    path = tmp_path / "synthetic.mov"
    make_synthetic_mov(path, verdict="violation")
    return path


@pytest.fixture
def auth():
    return {"X-API-Key": "test-jury"}
