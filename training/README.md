# План обучения моделей (MacBook Pro M2 Pro, 16 ГБ)

> **Статус ветки `training-plan`:** готовы установка окружения (`setup_mac.sh`), загрузка моделей (`download_models.sh`) и скрипты этапов A–D (`training/scripts/`, `run_stage_a.sh`, `run_stage_c.sh`). Если окружение на Mac собрано раньше, после `git pull` повторите `bash training/setup_mac.sh`: он не пересоздаёт `.venv-train`, а только обновляет пакеты (в том числе `numpy<=2.3.5` для экспорта в Core ML).

Пайплайн готовит данные и обучает модели из [analysis/detection_design.md](../analysis/detection_design.md):

| Модель | Что делает | Обучаем? |
|---|---|---|
| YOLO26l-detect, 14 классов | люди и предметы (`configs/classes.yaml`) | **да** |
| YOLO26-cls × 3 | экран (рабочий / заблокирован / выключен), ключ в замке, дверь | **да** |
| YOLO26l-pose | запястья | нет, готовые веса |
| Qwen3.5-4B (mlx-vlm) | проверка кандидатов | нет |

**Чего нет до старта.** У организаторов только метки «нарушение / секунда», рамок предметов нет. Рамки создаёт пайплайн: автоматическая предварительная разметка → ручная правка → обучение. Поэтому работа идёт этапами:
- этап A (машина) запускается сразу;
- этап B (люди) идёт параллельно с ним.

**Данные хакатона в git не попадают** (`data/` в `.gitignore`, FAQ п. 6). Видео копируются на Mac вручную.

---

## 0. Подготовка Mac (~20–40 мин, в основном загрузки ~12 ГБ)

```bash
git clone git@github.com:zzhassyn/datathon.git && cd datathon
git checkout training-plan
brew install uv                      # если ещё нет
bash training/setup_mac.sh           # окружение .venv-train: PyTorch (MPS), Ultralytics, transformers
caffeinate -i bash training/download_models.sh   # веса YOLO26, Grounding DINO, Qwen3.5-4B (8 и 4 бит); Mac на зарядке
source .venv-train/bin/activate
```

`download_models.sh` можно перезапускать: уже скачанное пропускается, оборванное докачивается. Если файл не скачался, скрипт печатает строку `!! … не скачан` и завершается с кодом 1 — просто запустите его ещё раз. Флаг `--no-qwen` пропускает модели сервиса (~8 ГБ), если сейчас нужно только обучение.

**Mac не должен засыпать.** Загрузка (~9,5 ГБ) и этапы A и C (по 2–3 ч) встают, когда Mac засыпает: при простое, при закрытой крышке, на батарее — вместе с выключением экрана. `download_models.sh`, `run_stage_a.sh` и `run_stage_c.sh` сами запускаются под `caffeinate` (Mac не уснёт от простоя). Держите Mac **на зарядке и с открытой крышкой**. Отдельные шаги `python training/scripts/…` запускайте так же: `caffeinate -i python training/scripts/08_train_detect.py …`.

Данные: распаковать `open.zip`. Нужна такая структура:

```
data/raw/open/videos/*.mov
data/raw/open/open_labels.xlsx
```

Проверка окружения — MPS, версии, память, диск, наличие данных:

```bash
python training/scripts/00_check_env.py
```

## Этап A — автоматически, можно запускать сразу (~2–2,5 ч машинного времени)

Одной командой: `bash training/run_stage_a.sh`. Или по шагам:

| Шаг | Команда | Что получается | Время (оценка) |
|---|---|---|---|
| A1 | `python training/scripts/01_prepare_raw.py` | `data/manifest.csv`: 40 уникальных роликов, сцены, события; дубли Х-47/Х-48 найдены по sha256 | < 1 мин |
| A2 | `python training/scripts/02_extract_frames.py` | `data/frames/<video>/…jpg` + `index.csv`: 1 кадр/с по всем роликам, 4 кадра/с ±3 с вокруг нарушений, без почти одинаковых кадров; родное разрешение 464×848 | 2–5 мин, ~1–1,3 тыс. кадров |
| A3 | `python training/scripts/03_prelabel.py` | `data/prelabels/…txt` (формат YOLO) + `.json` с уверенностью. Люди, ноутбук, телефон, стакан, сумки — готовая COCO-модель YOLO26x; остальное — Grounding DINO; неподвижные предметы размножаются по всем кадрам видео | 15–30 мин |
| A4 | `python training/scripts/07_build_dataset.py --source pseudo` | `data/yolo/pseudo/` + `dataset.yaml`; валидация — целая сцена L4 | < 1 мин |
| A5 | `python training/scripts/08_train_detect.py --profile smoke` | пробное обучение: проверка MPS и **реальное время эпохи** (`runs/detect/smoke/timing.json`) | 5–10 мин |
| A5b | `python training/scripts/08_train_detect.py --profile calib_full` | замер ровно в настройках `full` (YOLO26l, 864, freeze=10) на 15 % кадров: **прогноз этапа C** и проверка памяти (`runs/detect/calib_full/timing.json`). В `run_stage_a.sh` сбой этого шага этап не останавливает; `CALIB=0` — пропустить | 10–20 мин |
| A6 | `python training/scripts/08_train_detect.py --profile pseudo` | базовый детектор на автоматической разметке: на нём можно отлаживать сервис, пока люди правят разметку | ~1–1,5 ч |

После A3 проверьте сейф: в `data/prelabels/X-20/*.json` (с 6-й секунды сейф открыт) поле `safe_open_score_raw` должно быть заметно выше, чем в `data/prelabels/X-1/*.json` (сейф закрыт). Рамка `safe_open` ставится, только если оценка ≥ `prelabel.safe_open_threshold` (0.45, `configs/pipeline.yaml`). Если открытый сейф в X-20 не размечен, а у X-1 оценки ниже, поставьте порог между ними и переразметьте сейфовые ролики: `python training/scripts/03_prelabel.py --videos X-20 X-35 X-39 --force` (или `--safe-open-threshold`).

После A5 смотрите `timing.json`: там секунды на эпоху (и сколько из них ушло на загрузку кадров) и прогноз для профилей `pseudo`, `full`, `full_m`. Прогноз по `smoke` (маленькая модель, 640) для `full` грубый; точный — по `calib_full`. Если в `calib_full` не хватило памяти, Ultralytics сам уменьшит batch, а скрипт напишет, какой batch поставить для `full`.

## Этап B — ручная правка разметки (люди, 3–5 ч, параллельно с A6)

Label Studio живёт в отдельном окружении `.venv-ls` (он тянет много зависимостей), а скрипты пайплайна — в `.venv-train`. Поэтому нужны **два терминала**.

Терминал 1 — обучающее окружение, экспорт задач:

```bash
source .venv-train/bin/activate
python training/scripts/04_labelstudio_export.py     # задачи с предварительной разметкой; печатает переменные для Label Studio
```

Терминал 2 — Label Studio (работает, пока открыт терминал):

```bash
[ -x .venv-ls/bin/python ] || uv venv --python 3.12 .venv-ls
source .venv-ls/bin/activate && uv pip install label-studio
export LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true
export LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT="<путь, который напечатал 04>"
label-studio start                                   # данные остаются локально
```

Терминал 1 — после экспорта из Label Studio (Export → JSON):

```bash
source .venv-train/bin/activate                      # не .venv-ls: импорту нужен PyYAML из .venv-train
python training/scripts/05_labelstudio_import.py --export <файл экспорта Label Studio JSON>
```

Как сэкономить время:
1. **Неподвижные предметы** — сейф, ноутбук, дверь, машинка, детектор. Достаточно поправить рамку на первом кадре видео; при импорте она размножится на все кадры видео. Ложный предмет (например, «детектор», которого нет) достаточно удалить на первом кадре — при импорте он уйдёт из всех кадров ролика, где рамки не правились; кадры, где вы сами правили или удаляли рамки этого класса, импорт не трогает. Отдельно размечаются только кадры, где сейф открыт.
2. **Начать с кадров вокруг нарушений** (`--only-event-frames`): они важнее всего для правил.
3. **По каждому классу смотреть подсказку** из `configs/classes.yaml` → `review`. Самые шумные классы после автоматической разметки: `cash`, `id_document`, `auth_detector`, `safe_open`.
4. **Хватит ~600–800 проверенных кадров.** Остальные кадры в обучение не идут.

## Этап C — полное обучение и экспорт (~2–3 ч машинного времени)

`bash training/run_stage_c.sh` или по шагам:

```bash
python training/scripts/07_build_dataset.py --source reviewed --loso
python training/scripts/08_train_detect.py --profile full          # YOLO26l, 864, 50 эпох, freeze=10
python training/scripts/09_eval_detect.py --weights runs/detect/full/weights/best.pt
python training/scripts/10_export_coreml.py --weights runs/detect/full/weights/best.pt
python training/scripts/10_export_coreml.py --weights yolo26l-pose.pt   # готовая поза → Core ML
```

`09_eval_detect.py` даёт точность по каждому классу на сцене, которой не было в обучении. Это главный показатель устойчивости к новому ракурсу. Модели и `models/manifest.json` (sha256, классы, размер входа) забирает сервис.

**Память.** Скрипты ставят `PYTORCH_MPS_HIGH_WATERMARK_RATIO=1.0`: при нехватке памяти MPS сообщает «out of memory», и Ultralytics в первой эпохе сам уменьшает batch вдвое (8 → 4), а не уходит в swap. Во время первой эпохи откройте «Мониторинг системы» → «Память»: если «Использовано подкачки» растёт на гигабайты, остановите обучение (Ctrl+C) и в `configs/train.yaml` для `full` поставьте `batch: 4` или возьмите профиль `full_m` (`PROFILE=full_m bash training/run_stage_c.sh`).

## Этап D — классификаторы состояний (~30 мин людей + минуты обучения)

```bash
python training/scripts/11_extract_crops.py --weights runs/detect/full/weights/best.pt
# разложить фрагменты из data/crops/<задача>/_unsorted по папкам классов
# (известные по каталогу случаи скрипт раскладывает сам)
python training/scripts/12_train_cls.py --task screen
python training/scripts/12_train_cls.py --task safe_lock
python training/scripts/12_train_cls.py --task door
```

---

## Внешние датасеты

Подключение:
1. Скопировать `configs/external_datasets.example.yaml` → `configs/external_datasets.yaml` и описать датасет: формат YOLO/COCO, путь, лицензия, соответствие классов, `max_images`.
2. Запустить `python training/scripts/06_merge_external.py`.
3. Собрать датасет: `07_build_dataset.py --source reviewed --external <имя1>,<имя2>`.

Правила:
- **Внешние кадры идут только в train.** Оценка — только на наших роликах. Если внешний датасет ухудшил точность на сцене L4, его отключают.
- **Лицензия** должна разрешать коммерческое использование: права на решение получает банк (FAQ п. 34).
- **Время обучения растёт пропорционально числу кадров:** каждая добавленная 1 тыс. кадров ≈ +2–3 ч на профиле `full`. Поэтому `max_images` по умолчанию ограничивает вклад датасета. Если внешних данных много:
  1. сначала ночное обучение только на внешних данных (`--source external`);
  2. затем дообучение на наших: `08_train_detect.py --profile full --model runs/detect/<внешний>/weights/best.pt`.

## Устойчивость: другой ракурс, другие люди, другое окружение

| Что меняется | Что уже заложено | Что нужно от команды |
|---|---|---|
| **Другие люди** | Детектор учит класс «человек», не внешность; роли (кассир / клиент / инкассатор) определяются по зонам и поведению, не по одежде; лица не используются | Ничего обязательного. Внешние датасеты с людьми и своя съёмка с другими актёрами повышают надёжность |
| **Другой ракурс** (финальное видео) | Аугментации: поворот, перспектива, масштаб, отражение. Валидация на целой сцене, которой не было в обучении. Зоны сцены строятся по найденным предметам, а не по пикселям | **Своя съёмка с 2–3 других ракурсов** — самый сильный ход (FAQ п. 7 разрешает). Даже 10–15 коротких роликов |
| **Другое окружение и предметы** (другой сейф или машинка, окно-ячейка со стеклом и лотком, другой стол) | Детектор узнаёт только то, что видел. Похожие предметы обычно находит, совсем другие — нет | Внешние датасеты или своя съёмка с такими предметами. Новые типы зон (окно, лоток) — новые классы в `classes.yaml` плюс правила |

## Время целиком

| Этап | Кто | Время |
|---|---|---|
| 0. Подготовка Mac | человек | 15–20 мин |
| A. Кадры, предварительная разметка, пробное обучение, замер для этапа C, базовое обучение | Mac | ~2–2,5 ч |
| B. Ручная правка разметки | люди, параллельно с A6 | 3–5 ч |
| C. Полное обучение, оценка, Core ML | Mac | 2–3 ч (+2–3 ч на каждую 1 тыс. внешних кадров) |
| D. Классификаторы состояний | люди + Mac | ~1 ч |

**Ограничение по памяти:** обучение и сервис на Mac одновременно не запускать (16 ГБ общей памяти).
