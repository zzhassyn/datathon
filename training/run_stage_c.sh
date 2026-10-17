#!/usr/bin/env bash
# Этап C (машина, ~2-3 ч на M2 Pro): датасет из проверенной разметки (+ фолды LOSO) →
# полное обучение → оценка по классам → экспорт детектора и позы в Core ML (models/manifest.json).
#
# Запуск:  bash training/run_stage_c.sh
#          PROFILE=full_m bash training/run_stage_c.sh     # если full не помещается в память
# Ничего не спрашивает; повторный запуск пропускает готовые шаги.
# Переменные окружения: PROFILE (по умолчанию full), PYTHON — интерпретатор.
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

PROFILE="${PROFILE:-full}"
S=training/scripts
W="runs/detect/$PROFILE/weights/best.pt"
START=$(date +%s)

step() {
  local title="$1"
  shift
  echo
  echo "=== [$(date +%H:%M:%S)] $title"
  "$PY" "$@"
}

step "C1. Датасет reviewed + фолды LOSO"       "$S/07_build_dataset.py" --source reviewed --loso
step "C2. Полное обучение ($PROFILE)"          "$S/08_train_detect.py" --profile "$PROFILE"
step "C3. Оценка по классам (+ LOSO)"          "$S/09_eval_detect.py" --weights "$W" --loso-root
step "C4. Core ML: детектор"                   "$S/10_export_coreml.py" --weights "$W"
# готовая поза: models/pretrained/yolo26l-pose.pt (download_models.sh; если файла нет — скачается туда же)
step "C5. Core ML: поза"                       "$S/10_export_coreml.py" --weights yolo26l-pose.pt

ELAPSED=$(( $(date +%s) - START ))
echo
echo "=== Этап C готов за $((ELAPSED / 3600)) ч $(((ELAPSED % 3600) / 60)) мин."
echo "    Отчёт по классам: runs/detect/$PROFILE/eval_val.md (и eval_loso.md)"
echo "    Модели сервиса:   models/detect/$PROFILE.mlpackage, models/pose/yolo26l-pose.mlpackage, models/manifest.json"
echo "    Дальше — этап D: 11_extract_crops.py --weights $W, затем 12_train_cls.py --task screen|safe_lock|door"
