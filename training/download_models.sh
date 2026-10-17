#!/usr/bin/env bash
# Загрузка всех весов на Mac (~9,5 ГБ, из них Qwen ~8,3 ГБ). Можно перезапускать: скачанное пропускается или докачивается.
# Запуск из корня репозитория:  bash training/download_models.sh [--no-qwen]
# Mac на время загрузки держать на зарядке и с открытой крышкой: скрипт сам не даёт ему уснуть (caffeinate).
# Код возврата: 0 — всё скачано, 1 — часть файлов не скачалась (перезапустите скрипт: докачает).
set -euo pipefail

# macOS засыпает при простое, и загрузка встаёт: перезапускаемся под caffeinate
if [ "$(uname -s)" = Darwin ] && [ -z "${KASSA_CAFFEINATED:-}" ] && command -v caffeinate >/dev/null 2>&1; then
  KASSA_CAFFEINATED=1 exec caffeinate -dims bash "$0" "$@"
fi
cd "$(dirname "$0")/.."

WITH_QWEN=1
[ "${1:-}" = "--no-qwen" ] && WITH_QWEN=0
command -v uv >/dev/null 2>&1 || { echo "Нет uv. Установите: brew install uv"; exit 1; }
FAILED=0

# 1) YOLO26 (Ultralytics, AGPL-3.0) — ~320 МБ
#    n/s/m/l/x — детекция (x — для автоматической разметки, m — профиль full_m), l-pose — поза,
#    s-cls/n-cls — классификаторы
mkdir -p models/pretrained
BASE="https://github.com/ultralytics/assets/releases/download/v8.4.0"
for w in yolo26n.pt yolo26s.pt yolo26m.pt yolo26l.pt yolo26x.pt yolo26l-pose.pt yolo26s-cls.pt yolo26n-cls.pt; do
  f="models/pretrained/$w"
  if [ -s "$f" ]; then echo "== $w уже есть"; continue; fi
  echo "== $w"
  # .part докачивается при обрыве; в итоговое имя переносится только целый файл.
  # `curl ... && mv` в цикле не останавливает скрипт по set -e — поэтому явная проверка.
  if curl -L --fail --retry 3 -C - -o "$f.part" "$BASE/$w"; then
    mv "$f.part" "$f"
  else
    echo "!! $w не скачан — перезапустите скрипт"
    FAILED=1
  fi
done

HF="uvx --from huggingface_hub>=1.0 hf"

# 2) Grounding DINO base (Apache-2.0) — 0,93 ГБ, для предварительной разметки; кладётся в кэш Hugging Face.
#    pytorch_model.bin (ещё 0,94 ГБ) не нужен: transformers читает model.safetensors
echo "== IDEA-Research/grounding-dino-base"
if ! $HF download IDEA-Research/grounding-dino-base --exclude "*.bin"; then
  echo "!! IDEA-Research/grounding-dino-base не скачан — перезапустите скрипт"
  FAILED=1
fi

# 3) Qwen3.5-4B для сервиса (Apache-2.0, MLX): 8 бит — основной (5,2 ГБ), 4 бит — запасной (3,1 ГБ)
if [ "$WITH_QWEN" = "1" ]; then
  for q in Qwen3.5-4B-8bit Qwen3.5-4B-4bit; do
    echo "== mlx-community/$q"
    if ! $HF download "mlx-community/$q" --local-dir "models/hf/$q"; then
      echo "!! mlx-community/$q не скачан — перезапустите скрипт"
      FAILED=1
    fi
  done
fi

if [ "$FAILED" = "0" ]; then
  echo "== Готово:"
else
  echo "!! Скачано не всё (см. строки «!!» выше). Перезапустите: bash training/download_models.sh"
fi
du -sh models/pretrained models/hf 2>/dev/null || true
exit "$FAILED"
