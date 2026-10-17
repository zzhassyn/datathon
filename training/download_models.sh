#!/usr/bin/env bash
# Загрузка всех весов на Mac (~11,5 ГБ). Можно перезапускать: скачанное пропускается или докачивается.
# Запуск из корня репозитория:  bash training/download_models.sh [--no-qwen]
set -euo pipefail
cd "$(dirname "$0")/.."

WITH_QWEN=1
[ "${1:-}" = "--no-qwen" ] && WITH_QWEN=0
command -v uv >/dev/null 2>&1 || { echo "Нет uv. Установите: brew install uv"; exit 1; }

# 1) YOLO26 (Ultralytics, AGPL-3.0) — ~270 МБ
#    n/s/l/x — детекция (x — для автоматической разметки), l-pose — поза, s-cls/n-cls — классификаторы
mkdir -p models/pretrained
BASE="https://github.com/ultralytics/assets/releases/download/v8.4.0"
for w in yolo26n.pt yolo26s.pt yolo26l.pt yolo26x.pt yolo26l-pose.pt yolo26s-cls.pt yolo26n-cls.pt; do
  f="models/pretrained/$w"
  if [ -s "$f" ]; then echo "== $w уже есть"; continue; fi
  echo "== $w"
  # .part докачивается при обрыве; в итоговое имя переносится только целый файл
  curl -L --fail --retry 3 -C - -o "$f.part" "$BASE/$w" && mv "$f.part" "$f"
done

HF="uvx --from huggingface_hub>=1.0 hf"

# 2) Grounding DINO base (Apache-2.0) — 1,9 ГБ, для предварительной разметки; кладётся в кэш Hugging Face
echo "== IDEA-Research/grounding-dino-base"
$HF download IDEA-Research/grounding-dino-base

# 3) Qwen3.5-4B для сервиса (Apache-2.0, MLX): 8 бит — основной (5,2 ГБ), 4 бит — запасной (3,1 ГБ)
if [ "$WITH_QWEN" = "1" ]; then
  for q in Qwen3.5-4B-8bit Qwen3.5-4B-4bit; do
    echo "== mlx-community/$q"
    $HF download "mlx-community/$q" --local-dir "models/hf/$q"
  done
fi

echo "== Готово:"
du -sh models/pretrained models/hf 2>/dev/null || true
