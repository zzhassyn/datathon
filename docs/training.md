# Обучение и жизненный цикл моделей

> Назначение: как на Mac готовим данные, размечаем, обучаем, экспортируем в Core ML, проверяем, выпускаем и откатываем модели CashGuard.
> Владелец: S3 (perception & training). Статус: черновик v1, 17.10.2026.

Этот документ не заменяет [training/README.md](../training/README.md). README — пошаговая инструкция для человека у Mac. Здесь — правила вокруг неё: окружения, режимы Mac, данные, рецепты, экспорт, реестр моделей, выпуск и откат. Номера этапов и шагов (A0–A6, B1–B2, C1–C5, D1–D2) те же, что в README, в скриптах `training/scripts/` и в `training/run_stage_a.sh` / `training/run_stage_c.sh`.

Связанные документы: [eval.md](eval.md) (метрики и пороги приёмки), [architecture.md](architecture.md) (режимы, процессы, контракты), [roadmap.md](roadmap.md) (фазы и гейты), [runbook_mac.md](runbook_mac.md), [jury_mode.md](jury_mode.md), [detection_design.md](../analysis/detection_design.md) (что и зачем обучаем), [labels_typed.csv](../analysis/labels_typed.csv) (типизированная разметка).

## Содержание

1. [Что обучаем](#1-что-обучаем)
2. [Жизненный цикл модели](#2-жизненный-цикл-модели)
3. [Режимы Mac: обучение и сервис не одновременно](#3-режимы-mac-обучение-и-сервис-не-одновременно)
4. [Окружения](#4-окружения)
5. [Данные и правила обращения с ними](#5-данные-и-правила-обращения-с-ними)
6. [Этап 0 — подготовка Mac](#6-этап-0--подготовка-mac)
7. [Этап A — автоматическая часть](#7-этап-a--автоматическая-часть)
8. [Этап B — ручная правка разметки в Label Studio](#8-этап-b--ручная-правка-разметки-в-label-studio)
9. [Наборы данных: pseudo, reviewed, сцены, LOSO](#9-наборы-данных-pseudo-reviewed-сцены-loso)
10. [Этап C — детектор YOLO26l](#10-этап-c--детектор-yolo26l)
11. [Этап D — классификаторы состояний](#11-этап-d--классификаторы-состояний)
12. [Поза](#12-поза)
13. [RF-DETR-M — конкурент детектора](#13-rf-detr-m--конкурент-детектора)
14. [Экспорт в Core ML и проверка паритета](#14-экспорт-в-core-ml-и-проверка-паритета)
15. [Реестр моделей](#15-реестр-моделей)
16. [Выпуск новой версии и откат](#16-выпуск-новой-версии-и-откат)
17. [Что доработать в training/](#17-что-доработать-в-training)
18. [Время целиком](#18-время-целиком)
19. [Открытые вопросы](#19-открытые-вопросы)

---

## 1. Что обучаем

| Роль в реестре | Модель | Обучаем? | Где обучается | Где работает в сервисе | Лицензия |
|---|---|---|---|---|---|
| `detector` | YOLO26l detect, 14 классов из [classes.yaml](../training/configs/classes.yaml) | да | `.venv-train`, MPS | Core ML на Neural Engine, вход 864x480 (портрет) и 480x864 (ландшафт, грузится лениво) | AGPL-3.0 (разрешено организаторами) |
| `cls_screen` | YOLO26s-cls, `active` / `locked` / `off` | да | `.venv-train`, MPS | Core ML, 224 | AGPL-3.0 |
| `cls_safe_lock` | YOLO26s-cls, `key` / `no_key` | да | `.venv-train`, MPS | Core ML, 224 | AGPL-3.0 |
| `cls_door` | YOLO26s-cls, `closed` / `open` | да | `.venv-train`, MPS | Core ML, 224 | AGPL-3.0 |
| `pose` | YOLO26l-pose, готовые веса | нет, только экспорт | — | Core ML, 864x480 (+ 480x864) | AGPL-3.0 |
| `vlm` | Qwen3.5-4B, MLX 8 бит (запас 6 и 4 бит) через mlx-vlm | нет | — | GPU (MLX), отдельный процесс `vlm` | Apache-2.0 (веса Qwen; у репозиториев mlx-community нет карточки, происхождение пишем в манифест) |
| — (конкурент) | RF-DETR Medium | по желанию, ночью | `.venv-train`, MPS | в сервис не идёт, пока не выиграет (раздел 13) | Apache-2.0 (только чекпойнты N/S/M/L) |
| — (только этап A) | YOLO26x COCO + Grounding DINO base | нет | пред-разметка `03_prelabel.py` | никогда | AGPL-3.0 / Apache-2.0 (Grounding DINO: проверить по карточке модели) |

14 классов детектора идут в фиксированном порядке: `person, cash, collection_bag, safe_closed, safe_open, laptop, door, counting_machine, auth_detector, id_document, phone, cup, personal_bag, outerwear`. Тот же порядок записан в контракте `DetClass` (`src/cashguard/contracts/perception.py`). Порядок и состав классов после начала разметки не меняются. Новый класс — это изменение контракта (отдельный PR через S0), новая разметка и новая версия детектора.

Почему так, а не одна большая модель: [detection_design.md](../analysis/detection_design.md), раздел «Решение: гибрид, а не одна модель».

## 2. Жизненный цикл модели

```
data/raw/open/videos ── A1–A4 (.venv-train) ──► data/yolo/pseudo ── A5 smoke, A6 pseudo ──► runs/detect/pseudo
        │                                                                     │ (опц.: экспорт → detector v001,
        │                                                                     │  трассы v0, P1-S3-04)
        └─ B: Label Studio (.venv-ls, люди) ─► data/labels ─► C1: data/yolo/reviewed (+ reviewed_loso_L1…L5)
                                                                   │
                     C2: 08_train_detect --profile full ◄──────────┘
                     C3: 09_eval_detect (сцена L4, фолды LOSO)
                     C4–C5: 10_export_coreml в .venv-train (как есть; выход сервис не читает)
                     D:  11_extract_crops → ручная сортировка → 12_train_cls × 3
                     (по желанию) 13_train_rfdetr — конкурент
                                   │
     экспортёр СЕРВИСА в .venv (режим DEV): cashguard.pipeline.perception.export → models/<role>/vNNN/
     + паритет .pt vs .mlpackage + размещение на ANE + sha256 в manifest.json
                                   │
     PR (S3 → S0): configs/models.yaml → рестарт воркеров → just extract-signals
     → just sync-signals → just eval-replay / just eval-full → eval/baselines/main.json
                                   │
     откат: configs/models.yaml на прошлую vNNN → рестарт воркеров
```

Ключевые правила цикла:
- Обучение — в `.venv-train`. Экспорт для сервиса и проверка паритета — экспортёром сервиса `src/cashguard/pipeline/perception/export.py` (новый путь, S3) в сервисном `.venv` (ADR-0001). Так `.mlpackage` собирается теми же ultralytics, coremltools и numpy 2.3.5, что работают в сервисе.
- Скрипты `training/scripts/00–12` и `run_stage_*.sh` не переписываются: их пишет живая сессия обучения, после P0-S0-01 в них идут только аддитивные правки по согласованию с её автором (раздел 17).
- Версия модели неизменна. Новый экспорт — новая `vNNN`, старую не перезаписываем.
- Сервис видит только то, что записано в `configs/models.yaml`. Выпуск и откат — это правка этого файла и рестарт воркеров.
- После смены любой модели меняется `models_hash`. Replay-трассы на Windows пересобираются (`just extract-signals`, `just sync-signals`), иначе правила тюнятся на старой модели.

## 3. Режимы Mac: обучение и сервис не одновременно

MacBook Pro M2 Pro, 16 ГБ — и машина обучения, и боевой хост. Память общая. Сервис в работе занимает ≈ 12–13,7 ГБ (оценка, см. [architecture.md](architecture.md), раздел 8). Поэтому три режима взаимоисключающие. Переключение — `just mode serve|train|dev`: `mode train|dev` = `launchctl bootout` + `launchctl disable` агента supervisor (после перезагрузки сервис сам не стартует), `mode serve` = `launchctl enable` + `bootstrap` ([runbook_mac.md](runbook_mac.md), раздел 6).

Рецепты запускаются как `uv run just <рецепт>`. Ниже для краткости — `just <рецепт>`.

| Режим | Что запущено | Что можно | Что нельзя |
|---|---|---|---|
| `serve` | supervisor → api, perception, vlm; cloudflared; caffeinate | демонстрация, окно жюри | обучение, Label Studio, `eval-full`, `robustness`. В окно жюри — вообще ничего, кроме сервиса и туннеля: ни браузера, ни IDE |
| `train` | supervisor выгружен и отключён (`launchctl disable`) | одно обучение через `just train-a` / `just train-c`; сервер Label Studio в `.venv-ls` (разметчики — со своих ноутбуков по LAN) | сервис: туннель может остаться и отдаёт 502; браузер на Mac. **В окно жюри режим `train` запрещён** |
| `dev` | supervisor с `--profile dev` | экспорт в Core ML, паритет и MLComputePlan (`.venv`), тесты S3/S6, `tests/mac`, `just extract-signals`, `just eval-full`, `just robustness` | одновременно с обучением; `just perf` |

`just perf` меряет сервис в условиях окна жюри и запускается только в режиме `serve` ([eval.md](eval.md), раздел 8.1): рецепт отказывается работать при запущенном `--profile dev` или обучении.

Правила:
1. На Mac в каждый момент идёт одна тяжёлая задача: обучение, или прогон eval/perf, или сервис. S3 и S6 не запускают тяжёлые вычисления одновременно. Пока Mac в режиме `train` (этапы C и D), S6 работает на Windows ([roadmap.md](roadmap.md), раздел 6.2).
2. Перед обучением:
   - `just mode train`;
   - закрыть браузер, IDE и прочие приложения (сервер Label Studio во время A6 допустим, браузер на Mac — нет);
   - питание от сети;
   - `python training/scripts/00_check_env.py` — MPS, память, диск, данные. Скрипт по умолчанию требует ≥ 20 ГБ свободного места; для всего плана (модели ≈ 13,6 ГБ, три окружения, кэш uv, видео, `runs/`, `data/yolo`, worktree) нужно ≥ 40 ГБ (оценка, [runbook_mac.md](runbook_mac.md), раздел 3.1).
3. Обучение запускать только через обёртки `just train-a` и `just train-c` [macos] (P1-S3-05): они отказываются стартовать, если `pgrep -f 'cashguard.cli up'` что-то находит, затем выполняют `caffeinate -i bash training/run_stage_{a,c}.sh`. Сами `run_stage_*.sh` защиты не имеют и не меняются. Так перезагрузка посреди обучения не поднимет сервис рядом с возобновлённым обучением. Исключение — этап A до появления обёрток (раздел 7).
4. Во время обучения следить за swap в «Мониторинге системы». Рост swap — сигнал перейти на `batch: 4` или профиль `full_m` (раздел 10).
5. После обучения: выйти из `.venv-train`, `just mode serve` (или `dev`), дождаться `GET /ready` = 200.
6. После тега `jury-v1` (фаза P4) обучение и выпуск моделей заморожены. Подробно — [jury_mode.md](jury_mode.md).

## 4. Окружения

Все три окружения — Python 3.12, создаются через uv. Они не смешиваются: у каждого свои конфликтующие зависимости.

| Окружение | Как создать | Для чего | Чего там нет |
|---|---|---|---|
| `.venv` (сервис) | `uv sync --locked --extra ml --extra mac` по `uv.lock` (после того как в `main` есть `uv.lock`) | сервис `cashguard`; экспорт в Core ML и паритет (`python -m cashguard.pipeline.perception.export`); `just extract-signals`, `eval-full`, `perf`, `robustness`; `tests/mac` | Grounding DINO для пред-разметки, pandas, openpyxl, Label Studio, rfdetr |
| `.venv-train` | `bash training/setup_mac.sh` по `training/requirements.txt` | этапы A–D, обучение на MPS, Grounding DINO, RF-DETR-M | сервис его не импортирует: `training/` и `kassa_train` не импортируются кодом `src/cashguard` (import-linter) |
| `.venv-ls` | `uv venv --python 3.12 .venv-ls && uv pip install --python .venv-ls/bin/python label-studio==1.23.2` (без `--python` команда поставит пакет в сервисный `.venv`) | Label Studio, этап B | — |

**Одинаковые версии в `.venv` и `.venv-train`.** `.pt`, обученный одной версией ultralytics, экспортируется другой. Чтобы не ловить расхождения, пины совпадают:

| Пакет | Версия | Комментарий |
|---|---|---|
| ultralytics | 8.4.179 | проверить на PyPI; при регрессии откатить на 8.4.178 сразу в обоих окружениях |
| torch / torchvision | 2.14.1 / 0.29.1 | запасной вариант только для экспорта — 2.8.x / 0.23.x (ниже) |
| numpy | 2.3.5 | единственная numpy 2, с которой работают и ultralytics на Darwin, и его проверка Core ML |
| coremltools | 9.0 | только в `.venv`: экспорт делаем там |

Сейчас `training/requirements.txt` не закреплён (`ultralytics>=8.4.179`, `numpy` без версии, `torch>=2.7`). Закрепить — аддитивный PR P0-S3-02 по согласованию с сессией обучения (раздел 17).

**Запасное окружение экспорта.** coremltools 9.0 проверен с torch около 2.7/2.8, а в lock-файле torch 2.14.1. Если конвертация в Core ML падает, экспорт запускается в изолированном окружении:

```bash
uv run --isolated --with-editable . --with torch==2.8.* --with numpy==2.3.5 --with coremltools==9.0 \
  --with ultralytics==<пин из uv.lock> python -m cashguard.pipeline.perception.export --role <role> --weights <...> --version vNNN
# точная команда — проверить при первом падении экспорта
```

Версии, с которыми реально собран `.mlpackage`, записываются в `manifest.json` (раздел 15).

**Телеметрия.** В `.venv-train` один раз выполнить `yolo settings sync=False` (сервис делает то же через `YOLO_CONFIG_DIR`). Label Studio 1.23.2: проверить, есть ли отправка аналитики, и выключить.

## 5. Данные и правила обращения с ними

### 5.1. Где что лежит (Mac, `~/cashguard`)

Репозиторий на Mac — `~/cashguard`. Не `~/Desktop`, `~/Documents`, `~/Downloads`: TCC блокирует там launchd-задачи, а iCloud выгрузит видео. В [training/README.md](../training/README.md) клон пока называется `datathon` — обновить (раздел 17). Корень данных можно переопределить переменной `KASSA_DATA_DIR`.

| Путь | Что | Кто пишет |
|---|---|---|
| `data/raw/open/videos/*.mov`, `data/raw/open/open_labels.xlsx` | исходники организаторов (`open.zip`) | человек |
| `data/manifest.csv` | 40 уникальных роликов: sha256, длительность, fps, размер, сцена, события, `duplicate_of` | A1 `01_prepare_raw.py` |
| `data/frames/<video_id>/*.jpg`, `data/frames/index.csv` | кадры | A2 `02_extract_frames.py` |
| `data/prelabels/<video_id>/<stem>.{txt,json}`, `_static.json` | автоматическая разметка | A3 `03_prelabel.py` |
| `data/labelstudio/{label_config.xml,tasks.json}` | задачи Label Studio | B1 `04_labelstudio_export.py` |
| `data/labels/<video_id>/<stem>.txt` | проверенная человеком разметка | B2 `05_labelstudio_import.py` |
| `data/external/`, `data/external_converted/<name>/` | внешние датасеты | человек, `06_merge_external.py` |
| `data/yolo/<name>/` (`pseudo`, `reviewed`, `reviewed_loso_<сцена>`) | датасеты Ultralytics: `images/`, `labels/`, `dataset.yaml`, `stats.json` | `07_build_dataset.py` |
| `data/crops/<task>/{_unsorted,<class>}/`, `data/crops/<task>_split/`, `data/crops/crop_spec.json` | фрагменты для классификаторов | D1 `11_extract_crops.py`, человек, D2 `12_train_cls.py` |
| `runs/detect/<name>/`, `runs/classify/<task>/` | запуски обучения: веса, `args.yaml`, `results.csv`, `timing.json`, отчёты `eval_*.md` | `08`, `09`, `12` |
| `models/pretrained/*.pt`, `models/hf/Qwen3.5-4B-{8,6,4}bit/` | скачанные веса | `download_models.sh` |
| `models/<role>/vNNN/` | выпущенные модели | экспортёр сервиса, раздел 14 |
| `models/detect/full.mlpackage`, `models/pose/yolo26l-pose.mlpackage`, `models/manifest.json` | выход C4–C5 `run_stage_c.sh` | сервис их не читает |

### 5.2. Правила (FAQ п. 6, ТЗ разделы 7 и 10)

1. **Данные хакатона используются только в рамках хакатона** (FAQ п. 6). Видео, кадры, фрагменты и разметка не покидают машины команды. Запрещено: облачные хранилища (iCloud, OneDrive и т. п.), облачная разметка (Roboflow и т. п.), облачные AI-сервисы. Видео и кадры живут на Mac.
2. **Ничего из данных не коммитим.** `data/`, `var/`, `models/` (целиком, включая манифесты; версии и sha256 — в `configs/models.yaml`), `runs/`, `*.mov`, `*.mp4`, `*.zip`, `*.pt`, `result_*.json` — в `.gitignore`. Хук `check-added-large-files` (800 КБ) дополнительно блокирует видео и веса.
3. **Агенты не читают данные.** Claude Code, Codex и любые облачные LLM не читают `data/`, `var/`, `models/`, `*.mov`. Для Claude Code это запреты в `.claude/settings.json` (`Read(./data/**)`, `Read(./var/**)`, `Read(./models/**)`, `Read(./runs/**/*.jpg)`, `Read(**/*.mov)`, `Read(**/*.mp4)`). На Mac относительные правила не закрывают `~/cashguard/...` из worktree `~/cg-wt/<slug>`, поэтому человек ставит пользовательский `~/.claude/settings.json` с абсолютными путями ([roadmap.md](roadmap.md), раздел 4.3). Codex этот файл не читает — для него то же правило в `AGENTS.md`. Кадры смотрят только люди. Агенты работают с кодом, метриками, логами и replay-трассами (там только числа).
4. **Картинки в `runs/`.** Ultralytics кладёт в `runs/` превью батчей с кадрами датасета (`train_batch*.jpg`, `val_batch*.jpg`). Их закрывает запрет `Read(./runs/**/*.jpg)` (P0-S0-02; [../AGENTS.md](../AGENTS.md), правило 4). Числовые отчёты (`results.csv`, `timing.json`, `eval_*.md`, `eval_*.json`) агенты читать могут.
5. **Описания кадров тоже данные.** Агент не пересказывает содержимое кадров в чат или промпт. Столбец `what_is_seen` в `analysis/labels_typed.csv` — тоже описание кадров: код оценки его не читает, план переноса вне git — [eval.md](eval.md), раздел 3.1. Можно ли агентам смотреть кадры датасета при отладке — открытый вопрос к организаторам; по умолчанию нельзя.
6. **Видео жюри и UI не используются для обучения и оценки** (ТЗ раздел 10). Они не попадают в `data/`, `var/signals`, обучение и eval.
7. **Между машинами ходят только числа.** Трассы `var/signals/*.json.gz` переносятся `just sync-signals` (scp по LAN или Tailscale). Никогда через облачные хранилища.
8. **Внешние датасеты:** лицензия должна разрешать коммерческое использование (FAQ п. 24 и 34), поле `license` в `external_datasets.yaml` обязательно. Внешние кадры — только в train.
9. **Своя съёмка** (FAQ п. 7 разрешает) хранится так же, как данные хакатона: в `data/`, вне git. Предложение пути: `data/raw/own/videos/` (в дереве репозитория не закреплён, проверить с S0).
10. **После хакатона** `data/`, `var/` и `runs/` удаляются. Что делать с весами, обученными на данных хакатона, решает команда (проверить).

## 6. Этап 0 — подготовка Mac

Время: 20–40 мин, в основном загрузки (по README; оценка).

```bash
sw_vers                                 # macOS ≥ 14, рекомендуется 15
cd ~/cashguard
brew install uv                         # если ещё нет
bash training/setup_mac.sh              # .venv-train: PyTorch (MPS), Ultralytics, transformers
bash training/download_models.sh --no-qwen   # YOLO26 (ultralytics/assets v8.4.0), Grounding DINO base
# Qwen 8, 6 и 4 бит — один раз закреплёнными командами из runbook_mac.md, раздел 3.5
# .venv сервиса — позже, когда в main есть uv.lock (окно DEV Ч+3,75): uv sync --locked --extra ml --extra mac
source .venv-train/bin/activate
yolo settings sync=False
python training/scripts/00_check_env.py
```

`download_models.sh` можно перезапускать: скачанное пропускается. `--no-qwen` пропускает модели сервиса. Сейчас скрипт качает Qwen 8 и 4 бит без закреплённых ревизий, поэтому в P0-S3-01 он идёт с `--no-qwen`, а Qwen качается один раз по закреплённым ревизиям (иначе ≈ 8 ГБ, оценка, пришлось бы качать повторно). Аддитивный PR P0-S3-02: ревизии `--revision` (8 бит `1704583a`, 6 бит `fbb1092a`, 4 бит `0e7ffd5c`), загрузка 6 бит, манифест sha256. Ревизия Grounding DINO — проверить.

Данные: распаковать `open.zip` так, чтобы получилось `data/raw/open/videos/*.mov` и `data/raw/open/open_labels.xlsx`.

## 7. Этап A — автоматическая часть

Время ~2 ч машинного времени (оценка). Одной командой:

```bash
uv run just train-a                          # после P1-S3-05: проверяет, что сервис не запущен
caffeinate -i bash training/run_stage_a.sh   # до появления обёртки: из ветки training-plan, LaunchAgents ещё не установлены
```

Этап A можно запускать из ветки `training-plan`, как только скрипты запушены и данные на Mac, не дожидаясь мержа P0-S0-01 ([roadmap.md](roadmap.md), P0-S3-03).

Скрипт ничего не спрашивает. Все шаги идемпотентны: готовое пропускается, прерванное обучение продолжается с `last.pt`.

| Шаг | Команда | Результат | Время (оценка) |
|---|---|---|---|
| A0 | `python training/scripts/00_check_env.py` | проверка версий, MPS, памяти, диска, данных | секунды |
| A1 | `python training/scripts/01_prepare_raw.py` | `data/manifest.csv`: 40 уникальных роликов; X/Х нормализованы в ASCII `X-N`; дубли Х-47 = Х-4 и Х-48 = Х-5 найдены по sha256 | < 1 мин |
| A2 | `python training/scripts/02_extract_frames.py` | кадры: 1 кадр/с по ролику, 4 кадра/с в окне ±3 с вокруг нарушений, отсев почти одинаковых кадров (pHash), родное разрешение 464×848 | 2–5 мин, ~1–1,3 тыс. кадров |
| A3 | `python training/scripts/03_prelabel.py` | пред-разметка: COCO-модель YOLO26x (люди, ноутбук, телефон, посуда, сумки) + Grounding DINO (остальные классы); неподвижные предметы размножены по всем кадрам ролика | 15–30 мин |
| A4 | `python training/scripts/07_build_dataset.py --source pseudo` | `data/yolo/pseudo/`, валидация — вся сцена L4 | < 1 мин |
| A5 | `python training/scripts/08_train_detect.py --profile smoke` | пробное обучение: стабильность MPS и реальное время эпохи в `runs/detect/smoke/timing.json` | 5–10 мин |
| A6 | `python training/scripts/08_train_detect.py --profile pseudo` | черновой детектор на автоматической разметке | ~1–1,5 ч |

После A5 смотреть `timing.json`: секунды на эпоху и прогноз для профилей `pseudo`, `full`, `full_m` (`08_train_detect.py --profile smoke --show-timing`).

Что делать с результатом A6 (опционально, P1-S3-04). Черновой детектор (`runs/detect/pseudo/weights/best.pt`, YOLO26s) экспортёр сервиса выпускает как `detector` v001, и на нём S3 снимает replay-трассы v0 — только если код восприятия готов к Ч+6,5. Иначе карточка пропускается: S4/S5 пишут правила на синтетических трассах-фикстурах, первые реальные трассы — v1. Метрики этой модели не используются для приёмки.

## 8. Этап B — ручная правка разметки в Label Studio

Кто: люди, 3–5 ч на 1–2 человек, параллельно с A6. Агенты в этом этапе не участвуют: кадры смотрят только люди.

```bash
python training/scripts/04_labelstudio_export.py --only-event-frames   # сначала кадры у нарушений
uv venv --python 3.12 .venv-ls && uv pip install --python .venv-ls/bin/python label-studio==1.23.2
# переменные окружения для локальных файлов и шаги создания проекта печатает 04_labelstudio_export.py
.venv-ls/bin/label-studio start --internal-host 0.0.0.0 --port 8080   # флаг --internal-host — проверить
# разметчики открывают http://<LAN-адрес Mac>:8080 со своих ноутбуков; затем Export → JSON
source .venv-train/bin/activate
python training/scripts/05_labelstudio_import.py --export <файл экспорта JSON>
```

Как размечают двое одновременно, не нарушая правила «во время обучения браузер на Mac закрыт» (A6 идёт в режиме `train`):
- разметчики работают со своих ноутбуков (не с Windows-ПК агентов) по локальной сети;
- Label Studio слушает LAN-интерфейс; брандмауэр macOS пускает к порту 8080 только локальную сеть; через туннель Label Studio не публикуется никогда;
- браузер на Mac во время TRAIN не запускается;
- после этапа B разметчики чистят кэш браузера: кадры датасета не должны оставаться на их ноутбуках.

- Label Studio читает кадры с диска Mac. Кадры не копируются в облако и не загружаются во внешние сервисы; разметчики видят их только через браузер в LAN.
- Формат экспорта — **JSON** (понимается и `JSON_MIN`). Архитектура упоминает экспорт «YOLO with images», но `05_labelstudio_import.py` читает JSON: ему нужны `data.video_id` и `data.stem` из задач.
- Label Studio не запускать в режиме `serve` во время окна жюри: это лишняя память.

Как сэкономить время (из README):
1. **Неподвижные предметы** (сейф закрыт, ноутбук, дверь, машинка, детектор): достаточно поправить рамку на первом кадре ролика. При импорте она размножится на остальные проверенные кадры ролика. Отдельно размечаются кадры с открытым сейфом: в кадр с `safe_open` рамка `safe_closed` не добавляется.
2. **Сначала кадры вокруг нарушений** (`--only-event-frames`): они важнее всего для правил.
3. **Подсказки по классам** — поле `review` в [classes.yaml](../training/configs/classes.yaml). Самые шумные после пред-разметки: `cash`, `id_document`, `auth_detector`, `safe_open`.
4. **Хватит ~600–800 проверенных кадров.** Непроверенные кадры в `reviewed` не идут.
5. Повторный экспорт задач: `--skip-reviewed` пропускает уже проверенные кадры.

Осторожно с `--accept-predictions`: он записывает пред-разметку задач без разметки человека так, будто человек её принял. Тогда шум пред-разметки попадает в `reviewed`. Использовать только для кадров, которые человек действительно просмотрел.

## 9. Наборы данных: pseudo, reviewed, сцены, LOSO

### 9.1. Источники разметки (`07_build_dataset.py --source`)

| Источник | Откуда рамки | Для чего |
|---|---|---|
| `pseudo` | `data/prelabels/` | A6: черновой детектор, ранние трассы. Для приёмки не используется |
| `reviewed` | `data/labels/` (только кадры с таким файлом; пустой файл = кадр без объектов) | C2: основной детектор |
| `external` | только `data/external_converted/*` | предобучение на внешних данных, затем дообучение на `reviewed` |

Внешние датасеты подключаются по README (раздел «Внешние датасеты»): `external_datasets.yaml` → `06_merge_external.py` → `07_build_dataset.py --source reviewed --external a,b [--external-cap N]`. Каждая добавленная 1 тыс. кадров ≈ +2–3 ч обучения на профиле `full` (оценка). Если внешний датасет ухудшил точность на сцене L4, его отключают.

### 9.2. Деление по сценам, а не по кадрам

Ролики сняты парами: один сценарий с нарушением и без (Х-11/Х-12, Х-22/Х-23, Х-16/Х-17…). Случайное деление по кадрам переносит пару в обе выборки. Поэтому делим по сценам (ракурсам) L1–L5.

| Сцена | Уникальных роликов | С нарушением | Чистых |
|---|---|---|---|
| L1 | 9 | 5 (Х-1, Х-2, Х-4, Х-17, Х-18) | 4 (Х-3, Х-5, Х-16, Х-19) |
| L2 | 17 | 8 (Х-11, Х-13, Х-15, Х-20, Х-22, Х-24, Х-26, Х-28) | 9 (Х-8, Х-10, Х-12, Х-14, Х-21, Х-23, Х-25, Х-27, Х-29) |
| L3 | 3 | 2 (Х-30, Х-32) | 1 (Х-31) |
| L4 | 6 | 4 (Х-33, Х-40, Х-44, Х-46) | 2 (Х-43, Х-45) |
| L5 | 5 | 2 (Х-34, Х-38) | 3 (Х-35, Х-37, Х-39) |
| Итого | 40 | 21 | 19 |

Подсчёт по [labels_typed.csv](../analysis/labels_typed.csv); Х-47 и Х-48 — дубли Х-4 и Х-5 и отдельно не считаются.

- Валидация детектора — вся сцена L4 (`split.val_scenes: [L4]` в [pipeline.yaml](../training/configs/pipeline.yaml)). Это камера и раскладка, которых модель не видела.
- По комментарию в `12_train_cls.py` на сцене L4 может не быть сейфа. Тогда классы `safe_closed` / `safe_open` на L4 не оцениваются. Проверить по `data/yolo/reviewed/stats.json`; для этих классов смотреть фолд LOSO с L2.

### 9.3. Фолды LOSO

`07_build_dataset.py --source reviewed --loso` дополнительно собирает `data/yolo/reviewed_loso_<сцена>/` для каждой сцены L1–L5 (эта сцена — val). `09_eval_detect.py --loso-root` оценивает одни и те же веса на всех фолдах.

Честный только фолд той сцены, которой не было в обучении этих весов. Основной детектор `full` обучен без L4, поэтому честный фолд — `reviewed_loso_L4`. Остальные показывают качество на уже виденных сценах. Полный LOSO детектора — это 5 обучений по 2–3 ч (оценка). Он не на критическом пути; делать, только если остаётся машинное время.

LOSO порогов правил — отдельная процедура на replay-трассах, см. [eval.md](eval.md).

### 9.4. Воспроизводимость

`data/yolo/<name>/` пересобирается при изменении входных данных и не версионируется. Поэтому вместе с моделью в реестр пишем отпечаток набора: `dataset.yaml`, `stats.json` и sha256 по отсортированному списку файлов разметки (предложение, раздел 17).

## 10. Этап C — детектор YOLO26l

Время ~2–3 ч машинного времени (оценка).

```bash
uv run just train-c                  # C1–C5; обёртка проверяет, что сервис не запущен, внутри caffeinate -i bash training/run_stage_c.sh
PROFILE=full_m uv run just train-c   # если full не помещается в память (передачу PROFILE через just — проверить)
```

По шагам:

| Шаг | Команда | Результат |
|---|---|---|
| C1 | `python training/scripts/07_build_dataset.py --source reviewed --loso` | `data/yolo/reviewed/` + фолды LOSO |
| C2 | `python training/scripts/08_train_detect.py --profile full` | `runs/detect/full/weights/best.pt`, `last.pt`, `results.csv`, `timing.json` |
| C3 | `python training/scripts/09_eval_detect.py --weights runs/detect/full/weights/best.pt --loso-root` | `runs/detect/full/eval_val.{json,md}`, `eval_loso.{json,md}` |
| C4–C5 | `10_export_coreml.py` в `.venv-train` (`nms=True`), как и прежде | `models/detect/full.mlpackage`, `models/pose/yolo26l-pose.mlpackage`, `models/manifest.json`. Сервис эти файлы игнорирует: выпуск для сервиса — экспортёр сервиса в DEV (раздел 14) |

### 10.1. Профили ([train.yaml](../training/configs/train.yaml))

| Профиль | Модель | imgsz | Эпохи | patience | freeze | batch | Набор | Время (оценка) |
|---|---|---|---|---|---|---|---|---|
| `smoke` | yolo26n.pt | 640 | 3 (fraction 0.25) | — | — | 8 | pseudo | 5–10 мин |
| `pseudo` | yolo26s.pt | 864 | 30 | 8 | — | 8 | pseudo | ~1–1,5 ч |
| `full` | yolo26l.pt | 864 | 50 | 10 | 10 | 8 (при нехватке памяти — 4) | reviewed | ~2–3 ч |
| `full_m` | yolo26m.pt | 864 | 50 | 10 | 10 | 8 | reviewed | на ~20–25 % быстрее `full`, чуть ниже точность |

Общее для всех профилей: `device: auto` (на Mac — `mps`), `cache: false` (на 16 ГБ не включать), `seed: 26`. Аугментации под другой ракурс, других людей и свет: `hsv_h 0.015`, `hsv_s 0.6`, `hsv_v 0.4`, `degrees 8.0`, `translate 0.1`, `scale 0.5`, `shear 2.0`, `perspective 0.0007`, `fliplr 0.5`, `mosaic 1.0`, `close_mosaic 10`.

Рецепт `full` в терминах Ultralytics: `yolo train model=yolo26l.pt data=data/yolo/reviewed/dataset.yaml imgsz=864 epochs=50 patience=10 freeze=10 batch=8 device=mps`. Запускать через `08_train_detect.py`: он пишет `timing.json`, продолжает прерванный запуск и отбрасывает неизвестные Ultralytics ключи.

Почему 864: кадр 464×848 не уменьшается, мелкие предметы видны лучше. В сервисе модель работает на входе 864x480 — тот же масштаб по длинной стороне.

### 10.2. Особенности MPS

Из исследования стека (ссылки на issues Ultralytics там же); проверить на месте:
- На `mps` тренер Ultralytics сам ставит `workers=0`. `workers: 4` в `train.yaml` ни на что не влияет.
- AMP на `mps` выключен: обучение в FP32, памяти нужно примерно вдвое больше.
- `batch=-1` (AutoBatch) на `mps` откатывается к batch 16 → swap. Не использовать.
- Известна утечка памяти MPS и рост swap на таком же M2 Pro 16 ГБ (#22621, #24036). Есть swap — `--batch 4` или `full_m`.
- mAP валидации внутри цикла обучения на MPS может отличаться от отдельного `yolo val` (#24399). Модель выбираем по `09_eval_detect.py` (отдельный прогон), а не по цифрам из лога обучения.
- `channels_last` на MPS считает неверно. Не включать.
- `run_stage_*.sh` ставят `PYTORCH_ENABLE_MPS_FALLBACK=1`: операции без MPS-реализации уходят на CPU.
- Если обучение на MPS нестабильно — `--device cpu` (намного медленнее).

### 10.3. Запуски и повторы

- Повторный запуск с тем же профилем и параметрами пропускается. Прерванный (есть `last.pt`) продолжается.
- Новый запуск рядом со старым: `--name <имя>`, например `--name full_2`. Обучить заново поверх: `--force`.
- Дообучение после предобучения на внешних данных: `08_train_detect.py --profile full --model runs/detect/<внешний>/weights/best.pt --name full_ft`.

### 10.4. Как читать `09_eval_detect`

- `eval_val.md`: mAP50, mAP50-95, P/R и AP50 по каждому классу на сцене L4.
- Классы с AP50 < 0,5 выделены. Их разметку и данные усиливаем в первую очередь.
- `eval_loso.md`: те же веса на фолдах; честный помечен.
- Это метрики детектора, а не продукта. Приёмка модели в сервис — по сквозной оценке ([eval.md](eval.md)): recall по видео, ложные срабатывания на чистых роликах, таймкоды.

## 11. Этап D — классификаторы состояний

Время ~30 мин людей + минуты обучения (оценка).

```bash
python training/scripts/11_extract_crops.py --weights runs/detect/full/weights/best.pt
# люди раскладывают data/crops/<task>/_unsorted по папкам классов
python training/scripts/12_train_cls.py --task screen      # active / locked / off
python training/scripts/12_train_cls.py --task safe_lock   # key / no_key
python training/scripts/12_train_cls.py --task door        # closed / open
```

Фрагменты (D1, `11_extract_crops.py`):

| Задача | Откуда | Геометрия |
|---|---|---|
| `screen` | рамка `laptop` | +5 % поля |
| `safe_lock` | рамка `safe_closed`, правая средняя часть (доли рамки x 0.55–1.0, y 0.25–0.75, `--lock-region`) | +15 % поля, увеличение ×4, не меньше 224 px по короткой стороне |
| `door` | рамка `door` | +5 % поля |

Известные по каталогу случаи скрипт раскладывает сам: `safe_lock` — Х-22 с 14 с → `key`, Х-23 с 12 с → `no_key`; `screen` — Х-11 с 15 с и Х-28 с 7 с → `active`, Х-29 с 6 с и Х-23 с 5 с → `locked`. Остальное сортируют люди. Можно взять рамки из разметки вместо детектора: `--from-labels`.

Обучение (D2, `12_train_cls.py`): параметры из `train.yaml → cls`: `yolo26s-cls.pt`, `imgsz 224`, 30 эпох, `patience 8`, `batch 32`. Деление по сценам, как у детектора. Если в val нет ни одного фрагмента (например, сейфа на L4), в val уходит каждый `--holdout-every`-й фрагмент. Такая оценка оптимистична: соседние кадры похожи.

Результат: `runs/classify/<task>/weights/best.pt`, `cls_info.json`, `crop_spec.json`.

Обязательные условия для сервиса:
- обучать с `erasing=0.0` и `auto_augment=None`: аддитивный PR P1-S3-05 в `cls.common` `train.yaml` (`12_train_cls.py` уже передаёт любой ключ из `DEFAULT_CFG_DICT`), смержить до этапа D;
- `src/cashguard/pipeline/perception/crops.py` повторяет `data/crops/crop_spec.json` один к одному: `region`, `pad`, `upscale`, `min_side`. Сейчас `11_extract_crops.py` letterbox не делает, и в `crop_spec.json` нет такого поля. Поэтому сервис тоже не дополняет фрагмент до квадрата и отдаёт его в Ultralytics как есть — так же, как на val при обучении. Letterbox в сервисе включается, только если в `crop_spec.json` появится `letterbox: true`, а это возможно только правкой на стороне обучения (по согласованию с сессией обучения). Иначе вход классификаторов `screen` и `safe_lock` исказится, а ключ в замке ~10 px;
- контрактный тест (P1-S3-02) сравнивает выход `crops.py` с `crop_spec.json` на синтетической рамке. Это контракт между обучением и сервисом; его держит S3.

## 12. Поза

YOLO26l-pose — готовые веса `models/pretrained/yolo26l-pose.pt`, не обучаем. Экспортируем в Core ML 864x480 (и 480x864) теми же флагами, что детектор (раздел 14). В сервисе поза вызывается только для треков в зонах стола, передачи и сейфа, не чаще 2 кадров/с.

Паритет: на позу нет нашего размеченного набора, поэтому `yolo val` неприменим. Проверка — сравнение `.pt` и `.mlpackage` на 20 кадрах: IoU рамок > 0,9 (тест в `tests/mac`). Порог для точек — проверить.

## 13. RF-DETR-M — конкурент детектора

По желанию, ночью, после основного обучения. Не на критическом пути. Время +2–3 ч на Mac (оценка из detection_design).

Зачем: сильная модель под Apache-2.0. Для банка это путь без ограничений AGPL-3.0 у Ultralytics.

Окружение: `.venv-train`.

```bash
source .venv-train/bin/activate
uv pip install "rfdetr[train,coreml]==1.11.2" "supervision==0.31.0"   # extras и совместимость с пинами — проверить
python training/scripts/13_train_rfdetr.py                             # скрипта нет; номер 13 согласовать с сессией обучения (P1-S3-05)
```

Рецепт для `13_train_rfdetr.py` (из исследования стека):
- `RFDETRMedium().train(dataset_dir=..., epochs=40, batch_size=2, grad_accum_steps=8, device="mps", amp_dtype=None, resolution=704, output_dir=runs/rfdetr/<name>)`;
- `resolution` 704 или 768: должно делиться на `patch_size * num_windows`;
- на MPS — только целый batch; автоподбор batch и FP8 работают только на CUDA;
- тот же набор `data/yolo/reviewed`, та же отложенная сцена L4;
- RF-DETR 1.11 сам распознаёт YOLO-раскладку вида «`data.yaml` + папки `train`/`valid`». Наш `07_build_dataset.py` пишет `images/{train,val}`, `labels/{train,val}`, `dataset.yaml` — проверить. Если не распознаётся, скрипт собирает представление через жёсткие ссылки или конвертирует в COCO через supervision 0.31 (`DetectionDataset.from_yolo(...).as_coco(...)`);
- брать только чекпойнты N/S/M/L (Apache-2.0). Чекпойнт скачивается при первом запуске — скачать заранее; куда он кладётся, проверить.

**Правило продвижения.** RF-DETR-M идёт в сервис, только если обходит YOLO26l на **≥ 3 mAP50** на отложенной сцене. Обе модели оцениваются одним инструментом на одном наборе (какой инструмент — проверить: метрики Ultralytics и supervision могут расходиться).

Даже при выигрыше этого мало. На Mac RF-DETR работал бы на GPU (MPS или Core ML с `ALL`) и конкурировал бы с Qwen (MLX). Поэтому для продвижения нужны ещё:
- бэкенд perception под RF-DETR (сейчас `coreml` рассчитан на Ultralytics), работа S3;
- `just perf` в бюджете и ≥ 3 ГБ свободной памяти на пике;
- все сквозные гейты [eval.md](eval.md).

Иначе RF-DETR остаётся задокументированной альтернативой ([limitations.md](limitations.md), [roadmap.md](roadmap.md)).

## 14. Экспорт в Core ML и проверка паритета

Где: Mac, режим `dev`, **сервисное окружение** `.venv`. Экспорт, паритет и MLComputePlan делает экспортёр сервиса `src/cashguard/pipeline/perception/export.py` (новый путь, S3-Mac; карточка P0-S3-04). Ему нужны только PyYAML, ultralytics и coremltools — всё есть в `uv.lock`. `training/scripts/10_export_coreml.py` не меняется и для выпуска в сервис не используется.

### 14.1. Целевые флаги экспорта (ADR-0009)

| Роль | Вход (imgsz = [h, w]) | Флаги |
|---|---|---|
| `detector` | 864,480 (портрет) и 480,864 (ландшафт) | `format=coreml nms=False quantize=16 batch=1` |
| `pose` | 864,480 и 480,864 | те же |
| `cls_*` | 224 | `format=coreml` (для classify `dynamic` и `nms` не применяются) |

- `nms=False` сохраняет NMS-free end2end-голову YOLO26: NMS на стороне хоста не нужен. По умолчанию у Ultralytics `nms=None` — это другая голова и NMS на CPU.
- `quantize=16` — FP16. `half` и `int8` устарели в пользу `quantize`.
- Прямоугольный вход 864x480 примерно на 44 % дешевле, чем 864x864, для кадров 464×848.
- Вычислительные устройства — `CPU_AND_NE`. Это зашито в `CoreMLBackend` Ultralytics. Не патчить на `ALL`: `ALL` и `CPU_AND_GPU` падают из-за ошибки MPSGraph.

Эквивалент в CLI Ultralytics: `yolo export model=best.pt format=coreml imgsz=864,480 nms=False quantize=16 batch=1`.

### 14.2. Команды (цель)

```bash
# Mac, режим DEV, корень репозитория, интерпретатор сервиса
.venv/bin/python -m cashguard.pipeline.perception.export --role detector --weights runs/detect/full/weights/best.pt --version v002
.venv/bin/python -m cashguard.pipeline.perception.export --role pose --weights models/pretrained/yolo26l-pose.pt --version v001
.venv/bin/python -m cashguard.pipeline.perception.export --role cls_screen --weights runs/classify/screen/weights/best.pt --version v001
.venv/bin/python -m cashguard.pipeline.perception.export --role cls_safe_lock --weights runs/classify/safe_lock/weights/best.pt --version v001
.venv/bin/python -m cashguard.pipeline.perception.export --role cls_door --weights runs/classify/door/weights/best.pt --version v001
```

Экспортёр сам делает оба варианта входа (864x480 и 480x864) для detector и pose, вызывает `YOLO(w).export(format='coreml', imgsz=..., nms=False, batch=1, <аргумент FP16 — проверить>)`, пишет `models/<role>/vNNN/{best.pt,*.mlpackage,manifest.json}` и запускает паритет `yolo val` и MLComputePlan. Имена флагов — проверить по реализации (P0-S3-04).

Для сравнения: `training/scripts/10_export_coreml.py` вызывает `model.export(format="coreml", imgsz=..., nms=nms)` — `nms=True` для detect, без `quantize` (точность выбирает экспорт по умолчанию, `half` и `int8` не передаются), один размер входа за запуск. Кладёт пакет в `models/<task>/<name>.mlpackage` и пишет общий `models/manifest.json`. Имя по умолчанию — имя запуска, поэтому два запуска с `--imgsz 864 480` и `--imgsz 480 864` пишут один и тот же `models/detect/full.mlpackage`, второй затирает первый. Если этот скрипт всё же нужен (например, для сравнения), задавать `--name det_864x480` и `--name det_480x864`.

Первая загрузка модели компилирует её для ANE — это десятки секунд (оценка; замер — `bench_mac.py`). Обновление macOS сбрасывает этот кэш, поэтому сервис прогревает каждую модель до `/ready`.

### 14.3. Проверки паритета (гейт выпуска)

Модель попадает в реестр как кандидат только после всех проверок:

| Проверка | Как | Порог |
|---|---|---|
| Точность detector | `yolo val model=det_864x480.mlpackage data=data/yolo/reviewed/dataset.yaml imgsz=864,480` против того же для `best.pt` на сцене L4 | падение ≤ 1 mAP50 |
| Размещение на ANE | `coremltools.models.compute_plan.MLComputePlan`: голова и attention-блоки не уходят на CPU | без отката на CPU. Если откат есть — сравнить с экспортом `nms=None` |
| Паритет выходов detector и pose | `tests/mac`: `.pt` против `.mlpackage` на 20 кадрах | IoU > 0,9 |
| cls | top-1 `.mlpackage` против `.pt` на val-фрагментах | не хуже `.pt`; допуск — проверить |
| Прогрев | загрузка и одна инференция каждой модели в воркере perception | без ошибок, время прогрева в отчёт |

Гейт фазы P2 (G2 по [roadmap.md](roadmap.md), раздел 3): модели выпущены с отставанием от `.pt` не больше 1 mAP50, размещение на ANE в порядке, трассы v1 пересняты.

## 15. Реестр моделей

### 15.1. Раскладка

```
models/                                   # в .gitignore целиком (и манифесты); только на машинах команды
├─ pretrained/*.pt                        # download_models.sh, ultralytics/assets v8.4.0
├─ hf/Qwen3.5-4B-8bit/  …-6bit/  …-4bit/  # mlx-community, закреплённые ревизии
├─ detector/vNNN/
│  ├─ best.pt
│  ├─ det_864x480.mlpackage
│  ├─ det_480x864.mlpackage
│  └─ manifest.json
├─ pose/vNNN/{best.pt, pose_864x480.mlpackage, pose_480x864.mlpackage, manifest.json}
├─ cls_screen/vNNN/{best.pt, cls_screen.mlpackage, manifest.json}
├─ cls_safe_lock/vNNN/{best.pt, cls_safe_lock.mlpackage, manifest.json}
└─ cls_door/vNNN/{best.pt, cls_door.mlpackage, manifest.json}
```

Имена ролей — как в дереве репозитория: `detector`, `pose`, `cls_screen`, `cls_safe_lock`, `cls_door`. Имена `.mlpackage` — как в процессной модели (`det_864x480`, `pose_864x480`, `cls_screen`…). У позы `best.pt` — копия готовых весов; источник пишется в манифест.

Правила:
- `vNNN` — три цифры, растёт отдельно для каждой роли: `v001`, `v002`…
- Папка версии после выпуска не меняется. Новый экспорт или новые веса — новая версия.
- Первая версия `detector` — черновой детектор этапа A6 (YOLO26s на pseudo). Предобученный COCO-детектор YOLO26l в реестр как `detector` не идёт: у него другие классы. Его экспорт в P0 нужен только для замера скорости (`scripts/bench_mac.py`).
- Удалять старые версии до конца хакатона не нужно: они нужны для отката.

### 15.2. `manifest.json`

Пишется экспортёром сервиса. Не коммитится (`models/` в `.gitignore` целиком): версии, пути и sha256 для сервиса — в `configs/models.yaml`. Поля (форма — предложение S3, по исследованию стека):

```json
{
  "role": "detector",
  "version": "v002",
  "task": "detect",
  "classes": ["person", "cash", "...", "outerwear"],
  "base_weights": {"name": "yolo26l.pt", "source": "ultralytics/assets v8.4.0", "sha256": "<sha256>"},
  "source_run": "runs/detect/full",
  "train_args": "<копия args.yaml запуска>",
  "dataset": {"name": "reviewed", "val_scenes": ["L4"], "fingerprint": "<sha256>", "stats": "<копия stats.json>"},
  "artifacts": {
    "best.pt": {"sha256": "<sha256>"},
    "det_864x480.mlpackage": {"sha256": "<sha256 каталога>", "imgsz": [864, 480],
                              "export_args": {"format": "coreml", "nms": false, "quantize": 16, "batch": 1}},
    "det_480x864.mlpackage": {"sha256": "<sha256 каталога>", "imgsz": [480, 864], "export_args": {"...": "..."}}
  },
  "metrics": {"pt_map50_L4": "<число>", "coreml_map50_L4": "<число>", "ane_placement_ok": true},
  "crop_spec": null,
  "versions": {"ultralytics": "<>", "torch": "<>", "coremltools": "<>", "numpy": "<>", "macos": "<>"},
  "git_sha": "<sha>",
  "license": "AGPL-3.0",
  "exported_at": "<ГГГГ-ММ-ДД>"
}
```

- `.mlpackage` — это каталог. Его sha256 считается по отсортированному списку пар (относительный путь, sha256 файла).
- У классификаторов `crop_spec` — содержимое `crop_spec.json`: сервис режет фрагменты так же.
- В манифесте только числа, пути и версии. Никаких кадров.

### 15.3. `configs/models.yaml`

Коммитится в git. Владелец — S0, правит S3 через PR. Сервис загружает только то, что здесь записано. Точную схему задаёт модель `ModelsRegistry` (S0); пример формы:

```yaml
detector:
  version: v002
  dir: models/detector/v002
  artifacts:
    det_864x480: {path: det_864x480.mlpackage, sha256: "<sha256>", imgsz: [864, 480]}
    det_480x864: {path: det_480x864.mlpackage, sha256: "<sha256>", imgsz: [480, 864]}
    torch:       {path: best.pt, sha256: "<sha256>", imgsz: 864}
  backend: {darwin-arm64: coreml, linux-x86_64: torch, win32-AMD64: torch}
pose:          {version: v001, dir: models/pose/v001, artifacts: {...}, backend: {...}}
cls_screen:    {version: v001, dir: models/cls_screen/v001, artifacts: {...}, backend: {...}}
cls_safe_lock: {version: v001, dir: models/cls_safe_lock/v001, artifacts: {...}, backend: {...}}
cls_door:      {version: v001, dir: models/cls_door/v001, artifacts: {...}, backend: {...}}
vlm:
  quant: auto                      # CG_VLM_QUANT: 8 → 6 → 4 бит по свободной памяти
  variants:
    8bit: {repo: mlx-community/Qwen3.5-4B-8bit, revision: 1704583a, path: models/hf/Qwen3.5-4B-8bit}
    6bit: {repo: mlx-community/Qwen3.5-4B-6bit, revision: fbb1092a, path: models/hf/Qwen3.5-4B-6bit}
    4bit: {repo: mlx-community/Qwen3.5-4B-4bit, revision: 0e7ffd5c, path: models/hf/Qwen3.5-4B-4bit}
```

Имена репозиториев 6 и 4 бит — по шаблону 8 бит; проверить. Хэши файлов Qwen — из манифеста загрузки `download_models.sh` (раздел 6). Ревизии и репозитории VLM хранятся только здесь; `configs/vlm.yaml` их не дублирует (там `model: {quant: auto}`). Ключи платформ в `backend` — как в `[tool.uv] environments`: `darwin-arm64`, `linux-x86_64`, `win32-AMD64`.

### 15.4. Как сервис использует реестр

- Воркеры при старте сверяют sha256 каждого артефакта с `configs/models.yaml`. При расхождении воркер не запускается, `/ready` отдаёт 503.
- Активные версии и хэши видны в `GET /api/v1/admin/status` (поле `models`) и в каждой трассе `PerceptionTrace.models`.
- `pipeline_version` = git sha + хэш манифестов моделей. Он входит в ключ кэша результатов, поэтому новая модель не отдаёт старые закэшированные ответы.
- Replay-трассы называются `var/signals/<sha16>__<models_hash>.json.gz`. Трассы разных версий моделей лежат рядом и не путаются.

## 16. Выпуск новой версии и откат

### 16.1. Выпуск

Экспорт и паритет — в режиме `dev`, до тега `jury-v1`. `just perf` — только в режиме `serve`.

1. Обучить (этап C или D) в `.venv-train`.
2. Оценить `.pt`: `09_eval_detect.py` для детектора, `cls_info.json` и val-метрики для классификаторов.
3. Экспортировать в `.venv` в новую `models/<role>/vNNN/` (раздел 14).
4. Пройти проверки паритета (раздел 14.3). Не прошли — версия не выпускается; папку можно удалить.
5. Ветка `s3/promote-<role>-vNNN`: правка `configs/models.yaml` (версия, пути, sha256).
6. На Mac: рестарт воркеров (`just services-restart` в `serve` или перезапуск `just dev`), дождаться `/ready` = 200. Проверить версии в `/api/v1/admin/status`.
7. `just extract-signals` — новые трассы на 40 обучающих роликах. `just sync-signals` — на Windows.
8. `just eval-replay` на новых трассах против `eval/baselines/main.json`. На Mac — `just eval-full` (DEV) и `just perf` (SERVE).
9. Приёмка по гейтам [eval.md](eval.md):
   - recall по видео на LOSO = 1,0;
   - нет новых срабатываний кодов, влияющих на вердикт, на 19 чистых роликах;
   - точность типа и медиана |Δt| не хуже базовой линии в пределах допуска;
   - `just perf` в бюджете, ≥ 3 ГБ свободно на пике.
10. PR S3 → S0 содержит: diff `configs/models.yaml`, числа из `eval_val.md` и проверок паритета, diff `eval-replay`. `eval/baselines/main.json` этот PR не меняет: после мержа его перегенерирует S1 ([roadmap.md](roadmap.md), раздел 4.5). S0 мёржит fast-forward.

Если гейт по сквозной оценке не прошёл, а модель лучше по mAP — это повод для S4/S5 перетюнить пороги на новых трассах (`just loso`). Выпуск модели и перетюн правил идут одним PR или двумя подряд, но baseline обновляется только после прохождения гейтов.

### 16.2. Откат

1. Вернуть `configs/models.yaml` на прошлую версию: `git revert` коммита выпуска.
2. Рестарт воркеров, `/ready` = 200, версии в `/api/v1/admin/status` — прежние.
3. Трассы прошлой версии лежат в `var/signals` под её `models_hash`. Переснимать их не нужно.
4. `eval/baselines/main.json` откатывается тем же revert.

В окно жюри модели не меняются ни в какую сторону: «руки прочь» по [jury_mode.md](jury_mode.md). Если модель ломает сервис в окне, решение принимает человек-интегратор.

### 16.3. VLM

Qwen не обучается и не выпускается версиями. Ревизии закреплены в `configs/models.yaml`. Квантизация выбирается автоматически (`CG_VLM_QUANT=auto`): после загрузки 8 бит и прогрева, если свободно < 3,0 ГБ, воркер переходит на 6 бит, затем на 4 бит. Выбранная квантизация видна в `/ready` и в таблице `workers`. Для окна жюри квант фиксируется по `just perf` в SERVE: 8 бит — только если на пике свободно ≥ 3 ГБ, иначе `CG_VLM_QUANT=6bit` ([architecture.md](architecture.md), раздел 8).

## 17. Что доработать в training/

Расхождения текущих скриптов с архитектурой. Скрипты `training/scripts/00–12` и `run_stage_*.sh` пишет живая сессия обучения; они не переписываются. После P0-S0-01 S3 вносит в `training/` только небольшие аддитивные PR, согласованные с автором сессии обучения (P0-S3-02, P1-S3-05). Всё, что касается выпуска моделей в сервис, закрывает экспортёр сервиса `src/cashguard/pipeline/perception/export.py`.

| # | Где | Сейчас | Как закрываем |
|---|---|---|---|
| 1 | `scripts/10_export_coreml.py` | `nms=True` для detect, без `quantize` (точность выбирает экспорт), один размер входа, `models/<task>/<name>.mlpackage` + общий `models/manifest.json` | скрипт не меняется; экспортёр сервиса (P0-S3-04): `nms=False`, FP16, `batch=1`; detect и pose в 864,480 и 480,864; вывод в `models/<role>/vNNN/` с `manifest.json` на версию; sha256 файлов и каталогов; паритет `yolo val` (≤ 1 mAP50); MLComputePlan |
| 2 | `run_stage_c.sh`, шаги C4–C5 | экспорт интерпретатором `.venv-train` | скрипт не меняется; C4–C5 выполняются как прежде, их выход сервис игнорирует; выпуск — экспортёр сервиса в `.venv`, режим DEV (P2-S3-03) |
| 3 | `requirements.txt` | `ultralytics>=8.4.179`, `torch>=2.7`, `numpy` без версии, `transformers>=5.0` | аддитивный PR P0-S3-02: точные пины, равные `uv.lock`: ultralytics, torch, torchvision, numpy 2.3.5; transformers — точную версию проверить |
| 4 | `download_models.sh` | Qwen 8 и 4 бит без ревизий | аддитивный PR P0-S3-02: `--revision` 8 бит `1704583a`, 6 бит `fbb1092a`, 4 бит `0e7ffd5c`; загрузка 6 бит; манифест sha256; ревизия Grounding DINO — проверить |
| 5 | `configs/train.yaml` → `cls.common` | нет `erasing`, `auto_augment` | аддитивный PR P1-S3-05: `erasing: 0.0`, `auto_augment: null` (`12_train_cls.py` уже передаёт ключи из `DEFAULT_CFG_DICT`) |
| 6 | `11_extract_crops.py` | короткая сторона ≥ 224 px, без letterbox | скрипт не меняется; `pipeline/perception/crops.py` повторяет `crop_spec.json` один к одному (раздел 11) |
| 7 | `13_train_rfdetr.py` | нет | новый скрипт (раздел 13); номер согласовать с сессией обучения |
| 8 | `configs/train.yaml` → `common.workers` | `4` | на MPS не действует; можно поставить `0`, чтобы не вводить в заблуждение |
| 9 | отпечаток набора | нет | sha256 разметки, `dataset.yaml`, `stats.json` в манифест модели |
| 10 | `README.md` | `git clone … && cd datathon` | по согласованию с сессией обучения: клон в `~/cashguard`; выпуск моделей — экспортёр сервиса; ссылка на этот документ |
| 11 | `.claude/settings.json` (S0) | запреты на `data/`, `var/`, `models/`, `*.mov` | P0-S0-02: добавить `Read(./runs/**/*.jpg)` и `Read(**/*.mp4)`; на Mac — пользовательский `~/.claude/settings.json` с абсолютными путями |
| 12 | обёртки запуска | `run_stage_*.sh` не проверяют, работает ли сервис | рецепты `just train-a`, `just train-c` [macos] (P1-S3-05) отказываются стартовать при работающем supervisor |

## 18. Время целиком

| Этап | Кто | Время (оценка) |
|---|---|---|
| 0. Подготовка Mac | человек | 20–40 мин, в основном загрузки |
| A. Кадры, пред-разметка, smoke, pseudo | Mac | ~2 ч |
| B. Ручная правка разметки | люди, параллельно с A6 | 3–5 ч |
| C. Полное обучение, оценка | Mac | 2–3 ч (+2–3 ч на каждую 1 тыс. внешних кадров) |
| D. Классификаторы состояний | люди + Mac | ~1 ч |
| Экспорт, паритет, выпуск, extract-signals | Mac + S3 | проверить замером |
| RF-DETR-M (по желанию) | Mac, ночью | +2–3 ч |

В плане фаз ([roadmap.md](roadmap.md), разделы 3 и 6): этап A — P0 (Ч+1,5 – 3,75), этап B — Ч+2,5 – 6,5, импорт и сборка `reviewed` — Ч+6,5 – 7,25 (DEV), этап C — Ч+7,25 – 10,25 (TRAIN), этап D — Ч+10,25 – 11,25 (TRAIN), экспорт, паритет и выпуск — Ч+11,25 – 12,25 (DEV), трассы v1 — Ч+12,25 – 12,75 (DEV), RF-DETR-M — ночью (Ч+20 – 23, по желанию). Машинное время критического пути — 14–18 ч ([detection_design.md](../analysis/detection_design.md)); календарь команды — около 40 ч.

Критический путь: пред-разметка → правка разметки → обучение → пороги правил на LOSO.

## 19. Открытые вопросы

1. Можно ли агентам смотреть кадры датасета при отладке? По умолчанию — нет (вопрос к организаторам).
2. Дообучать ли финальный детектор на всех пяти сценах после выбора параметров? По умолчанию — нет: в сервис идёт модель без L4, чтобы её оценка на L4 оставалась честной. Решение — команда.
3. Точный пин ultralytics: 8.4.179 или 8.4.178 (проверить на PyPI в P0).
4. Ревизия и лицензия Grounding DINO base — проверить по карточке модели.
5. Путь для своей съёмки (`data/raw/own/` — предложение) и её разметка.
6. Судьба весов, обученных на данных хакатона, после события.
7. Инструмент сравнения mAP50 для YOLO26l и RF-DETR-M.
