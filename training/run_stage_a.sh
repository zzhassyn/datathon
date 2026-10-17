#!/usr/bin/env bash
# Этап A (машина, ~2–2,5 ч на M2 Pro): кадры → предварительная разметка → датасет pseudo →
# пробное обучение smoke (проверка MPS) → замер calib_full (скорость и память YOLO26l@864, прогноз
# времени этапа C) → базовый детектор pseudo.
#
# Запуск:  bash training/run_stage_a.sh
# Ничего не спрашивает. Все шаги идемпотентны: при повторном запуске готовое пропускается,
# прерванное обучение продолжается с last.pt.
# Переменные окружения: PYTHON — интерпретатор (по умолчанию python из .venv-train);
#                       CALIB=0 — пропустить замер calib_full (~10–20 мин).
set -euo pipefail

# macOS засыпает при простое (на батарее — вместе с выключением экрана), и обучение встаёт.
# Перезапускаемся под caffeinate: Mac не уснёт, пока идёт этап. Держите его на зарядке, крышку не закрывайте.
if [ "$(uname -s)" = Darwin ] && [ -z "${KASSA_CAFFEINATED:-}" ] && command -v caffeinate >/dev/null 2>&1; then
  KASSA_CAFFEINATED=1 exec caffeinate -dims bash "$0" "$@"
fi
cd "$(dirname "$0")/.."

if [ -f .venv-train/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv-train/bin/activate
elif [ -f .venv-train/Scripts/activate ]; then
  # shellcheck disable=SC1091
  source .venv-train/Scripts/activate
fi

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python >/dev/null 2>&1; then PY=python
  elif command -v python3 >/dev/null 2>&1; then PY=python3
  else echo "Нет python. Сначала: bash training/setup_mac.sh"; exit 1
  fi
fi
export PYTHONUTF8=1
export PYTORCH_ENABLE_MPS_FALLBACK=1
# MPS по умолчанию (1.7) разрешает занять больше памяти, чем есть у 16-ГБ Mac: обучение уходит в swap
# и медленно ползёт вместо ошибки. При 1.0 MPS сообщает «out of memory», и Ultralytics в первой
# эпохе сам уменьшает batch вдвое. Своё значение можно задать переменной окружения.
export PYTORCH_MPS_HIGH_WATERMARK_RATIO="${PYTORCH_MPS_HIGH_WATERMARK_RATIO:-1.0}"

S=training/scripts
START=$(date +%s)

step() {
  local title="$1"
  shift
  echo
  echo "=== [$(date +%H:%M:%S)] $title"
  "$PY" "$@"
}

SOFT_FAILED=""
soft_step() {  # шаг, сбой которого не останавливает этап (замер нужен только для прогноза)
  local title="$1"
  shift
  echo
  echo "=== [$(date +%H:%M:%S)] $title"
  if ! "$PY" "$@"; then
    echo "!! «$title» не удался — этап продолжается без него"
    SOFT_FAILED="${SOFT_FAILED:+$SOFT_FAILED; }$title"
  fi
}

step "A0. Проверка окружения"                  "$S/00_check_env.py"
step "A1. Ролики, сцены, события"              "$S/01_prepare_raw.py"
step "A2. Кадры"                               "$S/02_extract_frames.py"
step "A3. Предварительная разметка"            "$S/03_prelabel.py"
step "A4. Датасет pseudo"                      "$S/07_build_dataset.py" --source pseudo
step "A5. Пробное обучение (smoke)"            "$S/08_train_detect.py" --profile smoke
step "Прогноз времени по замеру smoke"         "$S/08_train_detect.py" --profile smoke --show-timing
if [ "${CALIB:-1}" != "0" ]; then
  # YOLO26l, 864, freeze=10, 2 эпохи на 15 % кадров: точный прогноз для full/full_m и проверка памяти
  soft_step "A5b. Замер для этапа C (calib_full)" "$S/08_train_detect.py" --profile calib_full
  soft_step "Прогноз этапа C по замеру calib_full" "$S/08_train_detect.py" --profile calib_full --show-timing
fi
step "A6. Базовый детектор (pseudo)"           "$S/08_train_detect.py" --profile pseudo

ELAPSED=$(( $(date +%s) - START ))
echo
echo "=== Этап A готов за $((ELAPSED / 3600)) ч $(((ELAPSED % 3600) / 60)) мин."
echo "    Базовый детектор: runs/detect/pseudo/weights/best.pt"
echo "    Замер скорости:   runs/detect/smoke/timing.json; прогноз этапа C: runs/detect/calib_full/timing.json"
if [ -n "$SOFT_FAILED" ]; then
  echo "!!  Не удалось: $SOFT_FAILED"
fi
echo "    Дальше — этап B (ручная правка разметки), затем bash training/run_stage_c.sh"
