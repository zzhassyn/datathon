"""Portable S1 bootstrap while shared packaging remains owned by S0.

Run with: uv run --no-project --python 3.12 python scripts/setup_backend.py
Only the documented backend dependencies are installed; no models are downloaded.
"""

import os
import subprocess
from pathlib import Path

DEPENDENCIES = (
    "fastapi==0.143.3",
    "starlette==1.8.0",
    "pydantic==2.14.1",
    "pydantic-settings==2.16.0",
    "uvicorn==0.54.0",
    "streaming-form-data==2.1.0",
    "structlog==26.1.0",
    "psutil==7.2.2",
    "av==19.0.1",
    "numpy==2.3.5",
    "PyYAML==6.0.3",
    "jsonschema==4.26.0",
    "httpx==0.28.1",
    "pytest==9.1.1",
    "ruff==0.17.1",
)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not environment.exists():
        subprocess.run(["uv", "venv", "--python", "3.12", str(environment)], cwd=root, check=True)
    if not python.is_file():
        raise SystemExit(
            ".venv существует, но Python не найден. Сохраните её отдельно и повторите setup."
        )
    subprocess.run(
        [
            str(python),
            "-c",
            "import sys; assert sys.version_info[:2] == (3, 12), 'Python 3.12 is required'",
        ],
        check=True,
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), *DEPENDENCIES],
        cwd=root,
        check=True,
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    subprocess.run(
        [
            str(python),
            "-c",
            "import cashguard; print('CashGuard import OK (S1 bootstrap)')",
        ],
        cwd=root,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
