import runpy
from pathlib import Path

import pytest


@pytest.fixture
def make_video():
    # Keep synthetic fixtures inside S1's ownership until S0 approves
    # tests/fixtures/synthetic_video.py from the roadmap.
    root = Path(__file__).resolve().parents[2]
    return runpy.run_path(str(root / "scripts/make_synthetic_mov.py"))["make_synthetic_mov"]
