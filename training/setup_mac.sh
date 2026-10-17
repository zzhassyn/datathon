#!/usr/bin/env bash
# Окружение обучения на Mac (Apple Silicon): Python 3.12 + PyTorch (MPS) + Ultralytics + transformers.
# Запуск из корня репозитория:  bash training/setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ "$(uname -s)" = "Darwin" ] || echo "Внимание: скрипт рассчитан на macOS"
command -v uv >/dev/null 2>&1 || { echo "Нет uv. Установите: brew install uv"; exit 1; }

echo "== Свободно на диске (нужно ≥ 20 ГБ под окружение, модели и кадры):"
df -h . | tail -1

# повторный запуск (после git pull) не пересоздаёт окружение, а только обновляет пакеты:
# uv venv на существующей папке завершается ошибкой «A virtual environment already exists»
[ -x .venv-train/bin/python ] || uv venv --python 3.12 .venv-train
# shellcheck disable=SC1091
source .venv-train/bin/activate
uv pip install -r training/requirements.txt

python - <<'PY'
import torch, ultralytics, transformers
print("torch", torch.__version__, "| MPS:", torch.backends.mps.is_available())
print("ultralytics", ultralytics.__version__, "| transformers", transformers.__version__)
PY
echo "Готово. Активация окружения: source .venv-train/bin/activate"
