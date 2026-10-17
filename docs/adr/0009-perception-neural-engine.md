# ADR-0009. Perception на Neural Engine: флаги экспорта Core ML, варианты ориентации, явный трекер

> Назначение: зафиксировать, как модели YOLO26 экспортируются и исполняются в проде на Mac.
> Владелец: S0 (документ, `configs/models.yaml`); реализует S3 (`pipeline/perception/**`, `pipeline/decode/**`, `configs/perception.yaml`, `configs/trackers/`, `training/scripts/10_export_coreml.py`). Статус: черновик v1, 17.10.2026.

## Статус

Принято. Текущий `training/scripts/10_export_coreml.py` этому решению пока не соответствует (см. «Последствия»); S3 приводит его в соответствие.

## Контекст

- GPU нужен Qwen через MLX ([ADR-0010](0010-qwen-vlm-bounded-verifier.md)). Neural Engine иначе простаивает.
- Кадры датасета 464x848, портрет. Детектор обучается на imgsz 864 ([training/README.md](../../training/README.md)).
- У YOLO26 голова end2end без NMS.
- Compute units `ALL` и `CPU_AND_GPU` падают из-за ошибки MPSGraph. Ultralytics `CoreMLBackend` жёстко задаёт `CPU_AND_NE`.
- Трекер по умолчанию в ultralytics 8.4.x — TrackTrack, а не BoT-SORT.
- FAQ 29: лица распознавать нельзя. FAQ 4: финальное видео снято под другим углом.
- Ключевые объекты мелкие: ключ в замке ≈ 10 px ([case_analysis.md](../../analysis/case_analysis.md), раздел 3).
- coremltools 9.0 проверен примерно с torch 2.7/2.8, а в lock-файле torch 2.14.1.

## Решение

1. **Модели и экспорт:**

   | Роль | Модель | Вход Core ML | Флаги |
   |---|---|---|---|
   | `detector` | YOLO26l detect, 14 классов из `training/configs/classes.yaml`, обучен на 864 | 864x480 (портрет); 480x864 (ландшафт, ленивая загрузка) | `nms=False`, `quantize=16` (FP16), batch 1 |
   | `pose` | YOLO26l-pose, готовая | 864x480 (портрет); 480x864 (ландшафт, ленивая загрузка) | то же |
   | `cls_screen`, `cls_safe_lock`, `cls_door` | YOLO26s-cls | 224; кроп — ровно как `data/crops/crop_spec.json` из обучения (region, pad, upscale, min_side); letterbox — только если в crop_spec есть `letterbox: true` | batch 1 |

   Выпуск моделей для сервиса — экспортёр `src/cashguard/pipeline/perception/export.py` (новый путь) в venv сервиса. `training/scripts/10_export_coreml.py` и `run_stage_c.sh` не меняются; их выход (C4–C5) сервис не читает.

   Точное имя аргумента FP16 в API экспорта ultralytics 8.4.179 — проверить.
2. **Исполнение:** Core ML .mlpackage, `CPU_AND_NE`. Никогда не менять на `ALL`. Инференс YOLO через `device=mps` в проде запрещён.
3. **Вариант по ориентации:** выбирается по размерам после поворота. Ландшафтный вариант загружается при первой надобности.
4. **Трекер:** BoT-SORT через `configs/trackers/botsort_static.yaml` — `with_reid False`, `gmc_method none`, `track_high 0.4`, `track_low 0.1`, `new_track 0.5`, `match 0.8`, `fuse_score True`, `track_buffer 15`. Передаётся **явно** при каждом вызове и сбрасывается на каждой задаче.
5. **Гейтинг:**
   - pose — только для треков в зонах стола, передачи и сейфа, ≤ 2 fps; на уровне `fast` — только в окнах кандидатов; на `min` — нет;
   - crop cls — 1,5 fps плюс триггеры смены состояния; все кропы кадра — один вызов predict; классификаторы обучаются с `erasing=0.0` и `auto_augment=None`.
6. **Прогрев:** по одному пустому инференсу на модель до `state=idle`; `/ready` ждёт прогрева.
7. **Приёмка модели:**
   - экспорт и проверка — в venv сервиса ([ADR-0001](0001-repository-shape.md));
   - паритет `yolo val` .pt против .mlpackage на отложенной сцене: разница ≤ 1 mAP50;
   - проверка размещения операций на ANE через MLComputePlan;
   - артефакты в `models/<role>/vNNN/{best.pt,*.mlpackage,manifest.json}`; манифест хранит sha256, версии, метрики, лицензию;
   - продвижение — правка `configs/models.yaml` (PR к S0) и рестарт воркеров;
   - воркер сверяет sha256 при старте и отказывается работать при несовпадении.
8. **Запасной путь экспорта**, если конверсия падает: изолированное окружение `uv run --isolated --with torch==2.8.* --with numpy==2.3.5 --with coremltools==9.0 --with ultralytics==<pin>`.
9. **Linux:** тот же порт `PerceptionBackend`, бэкенд `torch` на CUDA с весами `.pt` той же версии ([ADR-0012](0012-contracts-first-ports-replay.md)).

## Рассмотренные альтернативы

| Альтернатива | Почему отклонена |
|---|---|
| PyTorch-инференс на `device=mps` | Занимает GPU, нужный Qwen; нет перекрытия ANE и GPU |
| Динамический батчинг | Экспорт фиксирует статический batch 1. Кадры и так идут потоком по одному; батчинг добавил бы задержку и сложность без замеренного выигрыша |
| `ComputeUnit.ALL` | Падает из-за ошибки MPSGraph |
| Квадратный экспорт 864x864 | Для кадров 464x848 прямоугольный вход ≈ на 44 % дешевле |
| Трекер по умолчанию | В 8.4.x это TrackTrack; поведение неявное |
| RF-DETR на GPU в проде | Конкурирует с MLX за GPU. Остаётся конкурентом детектора в обучении: продвигается, только если обходит YOLO26l на ≥ 3 mAP50 на отложенной сцене |

## Последствия

Плюсы:
- ANE и GPU работают одновременно: perception задачи N+1 идёт параллельно с VLM задачи N.
- Голова без NMS — нет NMS на стороне хоста.
- Две ориентации и зоны по объектам работают на критерий «Устойчивость» (10 баллов).

Минусы и цена:
- Экспорт хрупкий: numpy строго 2.3.5, расхождение версий torch и coremltools, возможный уход операций YOLO26 с ANE на CPU.
- Первая компиляция под ANE — десятки секунд; обновление macOS сбрасывает кэш. В окно жюри автообновления выключены.
- Две ориентации — два пакета на модель.
- Текущий `training/scripts/10_export_coreml.py` использует `nms=True` для detect, без `quantize` (точность выбирает экспорт), один размер входа, pose по умолчанию 640, общий `models/manifest.json` и запускается из `.venv-train` через `run_stage_c.sh`. Скрипт не переписывается, а его выход в прод не принимается: модели для сервиса выпускает экспортёр `src/cashguard/pipeline/perception/export.py` ([architecture.md, раздел 15](../architecture.md#15-расхождения-с-текущим-кодом-training)).

Что проверяет решение:
- `tests/mac`: паритет Core ML и .pt; размещение на ANE.
- Тест, что yaml трекера передаётся явно.
- `just perf`: detect + track ≤ 15 с, pose ≤ 8 с, crop cls ≤ 2 с на ролик 145 с (оценка, бюджет в `configs/perf_budget.yaml`).
- `just robustness`: hflip, поворот ±15°, crop-zoom, перспектива, гамма, 25 fps.

Связанные решения: [ADR-0001](0001-repository-shape.md), [ADR-0002](0002-three-process-runtime-supervisor.md), [ADR-0011](0011-pts-timecodes-frame-store.md), [ADR-0012](0012-contracts-first-ports-replay.md). Подробности: [training.md](../training.md), [detection_design.md](../../analysis/detection_design.md).
