# Перенос изменений локального анализа на Mac

Пакет переносит код, тесты и документацию ветки `codex/local-video-analysis`.
Он не содержит видео, кадров, весов, калибровок банка, секретов, `.git`, runtime,
виртуальных окружений или `node_modules`. Реальные CUDA-прогоны выполнены на Windows.
Mac-прогона и экспорта Core ML не было. Обучение для переноса не требуется.

## Проверить пакет и применить код

Нужен существующий локальный репозиторий CashGuard на базе
`d1343a9b299248cab8acefe4e4a58d1667c23b1e`. Это пакет изменений, не полный проект.
Точная итоговая версия и список файлов находятся в `BASE_HEAD.json` и
`CHANGED_FILES.txt`. `changed-source/` содержит контрольные копии изменённых файлов;
патч применяется только после проверки совместимости.

```bash
# В папке с ZIP и внешним .sha256:
shasum -a 256 -c cashguard-mac-changes.zip.sha256
unzip cashguard-mac-changes.zip
cd cashguard-mac-changes
shasum -a 256 -c SHA256SUMS
```

Затем перейти в существующий checkout на Mac:

```bash
cd /absolute/path/to/cashguard
git status --short
git rev-parse HEAD
# Продолжать только при чистой рабочей копии и совпадении базы выше.
# При другой базе/незавершённой работе сначала вручную проверить совместимость.
git switch -c codex/mac-local-analysis-review
git apply --check /absolute/path/to/cashguard-mac-changes/changes.patch
git apply /absolute/path/to/cashguard-mac-changes/changes.patch
uv sync --locked
pnpm -C web install --frozen-lockfile
pnpm -C web build
uv run just check
```

Ничего не сбрасывать, не сливать и не публиковать автоматически. Нужны Python3.12,
Node24.22.0 и pnpm10.34.7; lock-файлы не обновлять. Сверить порядок классов и хеши
весов, если модели будут переноситься отдельно. Контракты jury/UI сохранены.

## Первый запуск UI/API на Mac

Это инфраструктурная проверка в **явном synthetic stub**, без распознавания банка.
Банковские видео для неё не использовать. Новый runtime не затрагивает прежние очереди.

```bash
export CG_DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cashguard-mac-review.XXXXXX")"
export CG_ENV_FILE="$CG_DATA_DIR/no-existing-secrets.env"
export CG_HOST=127.0.0.1
export CG_PORT=8127
export CG_PIPELINE_MODE=stub
export CG_VISION_BACKEND=stub
export CG_VLM_ENABLED=false
export CG_PIPELINE_VERSION=stub-v1
export CG_WEB_DIST="$PWD/web/dist"
uv run --no-sync cashguard up --profile prod --port 8127
```

Открыть `http://127.0.0.1:8127/`, проверить `/health` и `/ready`. Не занижать
минимум свободной памяти3ГБ. Демо-ролик, уже поставляемый с базовым UI, не является
доказательством работы моделей. Закрытие foreground-команды: Ctrl+C.

## Обученные PT на Mac: отдельная непроверенная совместимость

Текущий обработчик выбирает CUDA либо CPU; MPS/Core ML не реализованы.
Возможен технический запуск PT на CPU, но на Mac он не испытан, время и соблюдение
дедлайна235с не гарантируются. Это не рекомендованный готовый production backend.
Не выдавать запуск stub за проверку моделей. Не начинать обучение.

Для CPU-проверки нужны отдельно перенесённые веса:

| Файл | SHA-256 | Назначение |
|---|---|---|
| `main-best22.pt` | `b687a1e38127ae637ed1e9d9163da192dff82c2123bc63064ac5950a19cf12fb` | YOLO26s detect14 |
| `hands-best27.pt` | `544f49d455934b703e6516cc9139bed0fee1b7a4aaf2a3c495451004769c4dfc` | YOLO26s detect, class0=hand |
| `door-best6.pt` | `73c93111f5606cb218550e9f3b1da88e95cda84095c1af4a21768820896c406d` | YOLO26s-cls, closed/open |

```bash
shasum -a 256 /absolute/path/to/main-best22.pt \
  /absolute/path/to/hands-best27.pt /absolute/path/to/door-best6.pt
uv sync --locked --extra ml
```

Скопировать `configs/local_analysis.mac.example.json` в личную локальную папку.
Заменить `/ABSOLUTE/...` своими путями; указать локальное разрешённое видео,
новый файл калибровки и runtime. В шаблоне намеренно `device="cpu"`, нет координат
стола/двери и нет подтверждений оператора.

После завершения предыдущего сервиса и при достаточной RAM:

```bash
export CG_PIPELINE_MODE=full
export CG_VISION_BACKEND=torch
export CG_PIPELINE_VERSION=mac-pt-cpu-personal-items-review-v1
export CG_LOCAL_MODEL_PYTHON="$PWD/.venv/bin/python"
export CG_LOCAL_ANALYSIS_CONFIG=/absolute/path/to/local-analysis.mac.json
uv run --no-sync cashguard up --profile prod --port 8127
```

Сохраняются loopback, VLM=false, отдельный runtime и отключённая сеть ML-процесса.
Проверить загрузку трёх моделей и `/ready`; банковские кадры не отправлять агенту,
облачным сервисам или телеметрии. При отсутствии модели — ошибка, без подмены.

На `/local-calibration` оператор проверяет кадр после поворота и поверхность кассира.
Другая камера или видео требуют новой проверки SHA/размеров/поворота. Принадлежность
вещи сотруднику подтверждается отдельно: стол и близкая рука её не доказывают.
Если она неизвестна, оставить флажок выключенным; итог PERSONAL_ITEMS заблокирован.
Дверь требует отдельного ROI; стол и полный кадр для неё недопустимы. `closed`
означает закрытую дверь, не запертую.

## Границы проверки

Реальное положительное банковское событие не доказано. X-2 известен обучению и
служит только технической проверкой. Для personal_bag нет положительного банковского
val; отсутствие рамок не доказывает отсутствие нарушения. Новых независимых видео
не предоставлено. Синтетические фикстуры проверяют логику/публикацию/URL кадров.

Не подключены остальные нарушения, Qwen, уникальные объекты, Mac Core ML backend
и экспортёр. `just deploy/services/mode` и старый Mac runbook нельзя считать
проверенной инструкцией реального AI-запуска. Экспорт Core ML и его подключение
к worker требуют отдельной реализации и проверки на Mac без повторного обучения.
