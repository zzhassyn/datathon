# Пайплайн анализа видео

> **Назначение:** поэтапная спецификация пайплайна CashGuard: от приёма файла до `jury.json` и `ui_result.json`. По ней агенты реализуют модули `src/cashguard/pipeline/**` и связанные конфиги.
> **Владелец:** S0 (сигнатуры этапов, `pipeline/runner.py`); разделы этапов ведут их владельцы: S1 (ingest, render, stub), S3 (decode, perception, evidence), S4 (scene, правила A), S5 (правила B, verdict, legal), S6 (VLM, сигналы).
> **Статус:** черновик v1, 17.10.2026.

Связанные документы: [architecture.md](architecture.md) (процессы, память, надёжность), [violation_catalog.md](violation_catalog.md) (логика каждого кода), [api.md](api.md) (HTTP-контракт), [training.md](training.md) и [../training/README.md](../training/README.md) (модели), [eval.md](eval.md) (метрики, LOSO), [legal_sources.md](legal_sources.md) (нормы). Исходные исследования: [../analysis/detection_design.md](../analysis/detection_design.md), [../analysis/case_analysis.md](../analysis/case_analysis.md).

Обозначения:
- **t0** — момент начала обработки POST после проверки ключа, до первого байта тела. Все сроки считаются от t0.
- **оценка** — число не измерено; его заменит замер `scripts/bench_mac.py` или подбор LOSO.
- **проверить** — факт не подтверждён источником; до проверки не опираться.
- Пути в коде — от корня репозитория. Пути задачи — от `var/jobs/{id}/`.

---

## Содержание

1. [Обзор и инварианты](#1-обзор-и-инварианты)
2. [Режимы stub, replay, full](#2-режимы-stub-replay-full)
3. [Этап 0. Приём, проба, превью](#3-этап-0-приём-проба-превью)
4. [Этап 1. Захват задачи и план восприятия](#4-этап-1-захват-задачи-и-план-восприятия)
5. [Этап 2. Декодирование и хранилище кадров](#5-этап-2-декодирование-и-хранилище-кадров)
6. [Этап 3. Восприятие](#6-этап-3-восприятие)
7. [Этап 4. Сцена](#7-этап-4-сцена)
8. [Этап 5. Правила](#8-этап-5-правила)
9. [Этап 6. Вердикт](#9-этап-6-вердикт)
10. [Этап 7. Кадры-доказательства](#10-этап-7-кадры-доказательства)
11. [Этап 8. Описание и правовая ссылка](#11-этап-8-описание-и-правовая-ссылка)
12. [Этап 9. Рендер и предварительный результат](#12-этап-9-рендер-и-предварительный-результат)
13. [Этап 10. Проверка VLM](#13-этап-10-проверка-vlm)
14. [Финализация и очистка](#14-финализация-и-очистка)
15. [Деградация](#15-деградация)
16. [Сроки и бюджеты по этапам](#16-сроки-и-бюджеты-по-этапам)
17. [Конфигурация: файлы и ключи](#17-конфигурация-файлы-и-ключи)
18. [Тесты по этапам](#18-тесты-по-этапам)
19. [Открытые вопросы и «проверить»](#19-открытые-вопросы-и-проверить)

---

## 1. Обзор и инварианты

### 1.1 Схема

```
 api (FastAPI, без ML)          perception worker (Neural Engine + CPU)            vlm worker (GPU, MLX)
 ─────────────────────          ───────────────────────────────────────            ─────────────────────
 POST /api/v1/analyze           claim(stage=perception)                            claim(stage=vlm)
  ключ → поток → sniff           ├ deep probe → PerceptionPlan                      ├ группы кандидатов (±2 с)
  fsync → light probe            ├ T-decode ─► очередь(16) ─► detect+track          ├ вопросы + кадры из frames/
  dedupe → INSERT → 200          │            ─► gated pose ─► gated cls            ├ stream_generate (JSON-схема)
  (ui) preview.mp4               ├ T-store ─► frames/{pts_ms}.jpg                   ├ keyframe pass (если есть время)
                                 ├ trace.json.gz                                    ├ decide() повторно
                                 ├ scene → rules → verdict(τ)                       ├ evidence/describe для изменённых
                                 ├ evidence → describe → render                     ├ render → finalize (CAS)
                                 │   = ПРЕДВАРИТЕЛЬНЫЙ результат (rules)            └ удалить frames/, mx.clear_cache()
                                 └ handoff: stage=vlm  или  done(result_level=rules)

 sweeper (2 с) и GET (lazy): finalize_overdue, requeue_dead — тот же идемпотентный CAS
```

### 1.2 Этапы

Сигнатуры функций заморожены в P0 в `src/cashguard/pipeline/runner.py` (владелец S0). Владельцы реализуют тела.

| № | Этап | Модуль | Функция (P0) | Процесс | Выход в `var/jobs/{id}/` | Владелец |
|---|---|---|---|---|---|---|
| 0 | Приём, проба, превью | `api/upload.py`, `api/ingest_service.py`, `pipeline/ingest/` | `ingest.probe(path) -> VideoMeta`, `ingest.make_preview(path, meta, out) -> Path\|None` | api | `input.mov`, `meta.json`, `preview.mp4` (UI) | S1 |
| 1 | Захват и план | `workers/perception_worker.py`, `pipeline/perception/plan.py` | `runner.run_perception_stage(job, budget)` | perception | строка `jobs` (running), план | S1, S3 |
| 2 | Декодирование, хранилище кадров | `pipeline/decode/` | `VideoSource.iter_samples`, `FrameStore.put` | perception | `frames/{pts_ms}.jpg`, `frames/index.json` | S3 |
| 3 | Восприятие | `pipeline/perception/` | `perception.run(path, meta, plan, backend, store, progress) -> PerceptionTrace` | perception | `trace.json.gz` | S3 |
| 4 | Сцена | `pipeline/scene/` | `scene.build(trace, cfg) -> SceneModel` | perception | (в памяти; в `analysis.json` — сводка) | S4 |
| 5 | Правила | `pipeline/rules/` | `rules.evaluate_all(ctx) -> tuple[list[Candidate], list[Signal]]` | perception | `candidates.json` | S0 (каркас), S4, S5, S6 |
| 6 | Вердикт | `pipeline/verdict/` | `verdict.decide(cands, review\|None, cfg) -> list[Decision]` | perception, vlm | `analysis.json` | S5 |
| 7 | Доказательства | `pipeline/evidence/` | `evidence.render(decisions, store, out_dir) -> dict[str, list[EvidenceImage]]` | perception, vlm | `evidence/{i}_{event\|zoom\|context}.jpg` | S3 |
| 8 | Описание | `pipeline/legal/` | `legal.describe(decision, scene, catalog) -> tuple[str, list[LegalRef]]` | perception, vlm | (в `analysis.json`) | S5 |
| 9 | Рендер | `pipeline/render/` | `render.jury(result, evidence_dir) -> JuryJobResponse`, `render.ui(result) -> UiJobView` | perception, vlm | `jury.json`, `ui_result.json` | S1 |
| 10 | VLM | `pipeline/vlm/`, `workers/vlm_worker.py` | `vlm.plan_questions(decisions, store, cfg) -> list[VlmQuestion]`, `runner.run_vlm_stage(job, budget)` | vlm | обновлённые `analysis.json`, `jury.json`, `ui_result.json` | S6 |

Коды стадий для интерфейса (`StageState.code`): `queued` → `probe` → `perception` → `rules` → `evidence` → `vlm` → `done`. Этапы 4–6 отчитываются как `rules`, этапы 7–9 — как `evidence`.

### 1.3 Инварианты

1. **Таймкоды только из PTS.** `t_sec = frame.time − start`. Никогда `frame_index / fps`: ролики VFR, с B-кадрами и edit list ([ADR-0011](adr/0011-pts-timecodes-frame-store.md)).
2. **Кадр-доказательство — это сохранённый кадр.** Evidence и VLM читают кадры из `frames/`, поэтому `timestamp_sec` всегда равен реальному PTS.
3. **Предварительный результат всегда есть.** Сразу после восприятия пишется полный валидный `jury.json` по правилам. VLM его только уточняет ([ADR-0005](adr/0005-deadline-tiers-provisional-degradation.md)). Если perception не успевает, на рубеже `perception_hard_stop` (t0 + 235 с) задача закрывается правилами по уже обработанным кадрам.
4. **VLM только понижает балл.** Ответ «нет» снижает балл кандидата. Новый вердикт VLM не создаёт, кроме подтверждённого `OTHER_VISIBLE_IRREGULARITY` выше высокого порога (открытый вопрос 5, [ADR-0010](adr/0010-qwen-vlm-bounded-verifier.md)).
5. **Тип и таймкод выдают правила.** VLM не пишет описание для жюри. Текст строится по шаблону из фактов правила.
6. **Схема жюри заморожена.** `jury.json` валидируется по `src/cashguard/contracts/schemas/jury_job.schema.json` до публикации. Сигналы в ответ жюри не попадают ([ADR-0006](adr/0006-frozen-jury-contract.md)).
7. **Сцена, правила, вердикт, legal, render — чистый Python + numpy.** Без torch, ultralytics, coremltools, mlx, cv2. Проверяет import-linter. Поэтому replay работает на Windows.
8. **Без лиц и без ReID по внешности** (FAQ 29). Роли — по зонам и поведению.
9. **Без облака.** Модели грузятся из `models/` с проверкой sha256, `HF_HUB_OFFLINE=1`, `YOLO_OFFLINE=1`, `YOLO_AUTOINSTALL=false`.
10. **Детерминизм.** Температура 0, отсортированный вывод, версия порогов в `config_hash`, `pipeline_version` в каждом результате.
11. **Файл раньше статуса.** Каждый файл пишется через `core/atomic.py` (tmp → fsync → `os.replace`), и только потом CAS в SQLite.

---

## 2. Режимы stub, replay, full

Режим выбирают переменные окружения (`core/settings.py`, префикс `CG_`).

| Режим | `CG_PIPELINE_MODE` | `CG_VISION_BACKEND` | `CG_VLM_BACKEND` | Что выполняется | Где | Зачем |
|---|---|---|---|---|---|---|
| stub | `stub` | не используется | не используется | `pipeline/stub.py`: детерминированный фиктивный результат с задержками по стадиям. Подсказка по имени файла: нарушение или чистый ролик (правило подсказки задаёт S1) | Windows, Mac, Linux | API, UI, e2e, гейт G0 до появления моделей |
| replay | `replay` | `replay` (для тестов — `stub`, `null`) | `replay` или `null` | Трасса восприятия из `var/signals/` → сцена → правила → вердикт → (VLM из записанных ответов) → evidence → render | Windows | Разработка правил, `just eval-replay`, `just loso` |
| full | `full` | `coreml` (Mac), `torch` (Linux CUDA, Windows для отладки) | `mlx` (Mac), `transformers` (Linux), `null` | Все этапы | Mac (прод), Linux (контур банка) | Прод, `just eval-full`, `just perf` |

Бэкенды восприятия (`pipeline/perception/backends/`, порт `PerceptionBackend`):

| Бэкенд | Что делает | Когда |
|---|---|---|
| `coreml.py` | Ultralytics `YOLO('<file>.mlpackage', task=...)`, compute units CPU_AND_NE (зашито в CoreMLBackend, не патчить на ALL) | Прод на Mac |
| `torch.py` | Те же модели `.pt`, устройство cuda > mps > cpu | Linux CUDA, отладка на Windows (RTX 3050) |
| `replay.py` | Читает `PerceptionTrace` из `var/signals/<sha16>__<models_hash>.json.gz` по sha256 видео. Кадры (если видео есть) всё равно декодируются в хранилище для evidence | Windows, eval-replay |
| `stub.py` | Синтетическая трасса из фикстуры `tests/fixtures/timelines/` или минимальная детерминированная | Unit- и интеграционные тесты сцены и правил |
| `null.py` | Пустая трасса: ни одной детекции → `no_violation` | Тесты API и рендера |

Бэкенды VLM (`pipeline/vlm/backends/`, порт `VlmBackend`): `mlx.py` (Mac), `transformers.py` (Linux), `replay.py` (записанные ответы по ключу видео, хэша моделей и состава группы), `null.py` (VLM пропущен, `vlm_status=skipped`).

Правила для replay:
- `sha16` — первые 16 hex-символов sha256 видео. `models_hash` — хэш манифестов моделей из `configs/models.yaml`.
- Трассы и записанные ответы VLM создаёт только `just extract-signals` на Mac по 40 обучающим роликам. Копирует `just sync-signals` по LAN или Tailscale, никогда через облако.
- Трассы задач жюри и UI в `var/signals/` не копируются никогда.
- Если трассы для sha256 нет: в dev — пустая трасса и пометка в логах; в `just eval-replay` — ошибка.
- В `just eval-replay` видео нет. Evidence и render там не нужны: сравниваются кандидаты и вердикт с `analysis/labels_typed.csv`.

---

## 3. Этап 0. Приём, проба, превью

Модули: `src/cashguard/api/upload.py`, `api/ingest_service.py`, `api/deps.py`, `pipeline/ingest/probe.py`, `pipeline/ingest/preview.py`. Владелец S1. Процесс api (импорт `av` разрешён, ML — нет).

### 3.1 Шаги POST

Один код для `/api/v1/analyze` (principal `jury`, priority 0) и `/api/ui/jobs` (principal `ui`, priority 1).

1. **Ключ до тела.** Зависимость `require_api_key`: `APIKeyHeader('X-API-Key', auto_error=False)`; sha256 ключа сравнивается `hmac.compare_digest` со всеми ключами `CG_API_KEYS` без раннего выхода. Ошибка → 401 JSON, тело не читается. Обработчик принимает только `request: Request`, без `UploadFile` ([ADR-0004](adr/)).
2. **Ранние лимиты.** `Content-Length` > `CG_MAX_UPLOAD_MB` (100) → 413. Свободно на диске < `CG_MIN_FREE_DISK_GB` (2) → 507.
3. **Поток на диск.** `request.stream()` → `StreamingFormDataParser`. Поле `file` пишется в `input.part` с бегущим sha256 и счётчиком байтов. Превышение лимита → 413, `.part` удаляется. Content-Type части любой (curl шлёт `application/octet-stream`), имя файла клиента игнорируется. Нет поля `file` → 422.
4. **Сигнатура контейнера (рекомендательная).** Байты `[4:8]` сверяются со списком {`ftyp`, `moov`, `mdat`, `wide`, `free`, `skip`, `pnot`}. Незнакомый атом (например `uuid`) не отклоняется, а пишется в лог; 415 даёт только шаг 6, если PyAV не открывает контейнер ([api.md](api.md), раздел 3.2).
5. **Надёжность.** fsync в потоке, затем `os.replace` → `input.mov`.
6. **Лёгкая проба** (`ingest.probe`, PyAV, поток, ≤ 0,05 с, оценка): длительность, ширина, высота, кодек, поворот; в режиме `stub` — ещё метатег `comment=cg-stub:<verdict>` (раздел 2). Нечитаемый контейнер → 415.
7. **Дедупликация.**
   - Есть строка `result_cache` по (sha256, pipeline_version, config_hash), только уровень `full` → новая строка `done`, копия `jury.json`, `ui_result.json`, `evidence/` с подменой `job_id`.
   - Тот же sha256 уже в работе → строка с `linked_to` на ту задачу.
   - Иначе `INSERT jobs`: `status=queued`, `stage=perception`, `received_at=t0`, все сроки (раздел 16), VideoMeta-lite. Событие `accepted` в `job_events`.
8. **COMMIT → 200** `{"job_id": "<32-hex uuid4>"}`. Обычно < 0,5 с после последнего байта. С этого момента задача не теряется.

Сроки задачи вычисляются при INSERT из `configs/deadlines.yaml`: `vision_target = t0 + 150`, `vlm_start_cutoff = t0 + 180`, `vlm_stop = t0 + 210`, `finalize_at = t0 + 225`, `perception_hard_stop = t0 + 235` (секунды). Это wall-clock эпохи, общие для процессов. Внутри процесса бюджет считается по monotonic.

### 3.2 `meta.json`

Пишется атомарно до COMMIT. Поля: `sha256`, `size_bytes`, `duration_sec`, `width`, `height`, `rotation_deg`, `codec`, `source` (`jury` или `ui`), `received_at`, сроки, `needs_transcode`. Полный `VideoMeta` (fps, VFR, pix_fmt, start_pts, time_base) дописывает глубокая проба (этап 1).

### 3.3 Превью для интерфейса

Только для UI-задач. Jury-задачи превью не получают.
- После COMMIT API в потоке делает remux `input.mov` → `preview.mp4` без перекодирования, `movflags=+faststart` (≤ 0,3 с, оценка). Причины: moov в конце файла, Chrome не играет часть `.mov`, Safari нужен Range.
- HEVC или ненулевой поворот → `needs_transcode=true`. Перекодирование `h264_videotoolbox` делает perception worker. По умолчанию — после предварительного результата, чтобы не задерживать вердикт (порядок подтверждает S1 по замеру).
- Пока превью нет, `UiJobView.video = null`, а интерфейс играет локальный файл через `URL.createObjectURL`.

---

## 4. Этап 1. Захват задачи и план восприятия

Модули: `workers/base.py`, `workers/perception_worker.py` (S1), `pipeline/perception/plan.py` (S3), `core/budget.py`, `jobs/perfstats.py` (S1).

### 4.1 Захват

Perception worker опрашивает очередь каждые 250 мс:

```sql
BEGIN IMMEDIATE;
UPDATE jobs SET status='running', lease_owner=?, lease_pid=?, alive_at=?, progress_at=?, started_at=?
WHERE id = (SELECT id FROM jobs WHERE stage='perception' AND status='queued'
            ORDER BY priority, received_at LIMIT 1)
RETURNING *;
COMMIT;
```

После захвата строится `Budget` из сроков задачи. Поток T-heartbeat пишет `alive_at` каждые 2 с. Главный цикл пишет `progress_at` на каждом кадре или шаге.

### 4.2 Глубокая проба

`ingest.probe` в полном режиме (PyAV): минимум один видеопоток, длительность 1–900 с, ширина и высота ≤ 4096, первый кадр декодируется. Иначе `status=failed`, `error_code=UNSUPPORTED_VIDEO`, сообщение на русском. Это единственный штатный путь в `failed`; задача не переповторяется.

Результат — полный `VideoMeta{duration_sec, width, height, rotation_deg, fps_avg, is_vfr, codec, pix_fmt, start_pts_sec, time_base, sha256}`, дописывается в `meta.json`.

### 4.3 Уровни восприятия

`PerceptionPlan{level, sample_fps, pose_fps, cls_fps, imgsz_variant}`. Значения — из `configs/perception.yaml`.

| Уровень | Кадров/с | Поза | Классификаторы состояний | Когда |
|---|---|---|---|---|
| `full` | 3 | ≤ 2 кадров/с, только треки в зонах стола, передачи и сейфа | 1,5 кадра/с + триггеры | По умолчанию |
| `fast` | 1,5 | Только в окнах-кандидатах (раздел 6.4) | Только по триггерам | Попытка 2; нехватка времени |
| `min` | 1 | Нет | По триггерам (проверить: в синтезе «detection and cls only» без частоты) | Последний резерв |

Ролики длиннее 300 с получают адаптивную частоту: выборка не больше 600 кадров на ролик, то есть `sample_fps = min(fps_уровня, 600 / duration_sec)`.

Вариант входа детектора и позы выбирается по ориентации кадра после поворота: высота ≥ ширины → `864x480` (портрет, основной), иначе `480x864` (альбом, грузится лениво).

### 4.4 Выбор уровня

Эталонный алгоритм (`plan.py`, S3 может уточнить):

```
cost(level)  = perf_stats.ewma["perception_sec_per_video_sec@" + level] × duration_sec
               (холодный старт: оценка из configs/perf_budget.yaml)
backlog      = сумма cost(full) задач, которые ждут в очереди perception с тем же или более высоким приоритетом
level        = первый из [full, fast, min], для которого now + cost(level) ≤ vision_target
               и задачи из backlog по оценке успевают к своим vision_target
attempt == 2 → не выше fast
ни один не укладывается → min
задача взята из очереди позже t0 + 200 с (min_level_after) → сразу min
```

Уровень ниже `full` пишется в `degradations_json` (`perception_fast` или `perception_min`). Внутри задачи бюджет проверяется на каждом кадре. Если прогноз окончания выходит за `vision_target`, оставшиеся кадры обрабатываются на следующем уровне вниз, и это тоже пишется в деградации.

**Жёсткая остановка.** На рубеже `perception_hard_stop` (t0 + 235 с) воркер прекращает декодирование и прогоняет scene → rules → verdict → evidence → describe → render по уже обработанным кадрам. Предварительный `jury.json` пишется с деградацией `perception_truncated`; `finalize_at` уже прошёл, поэтому тот же воркер сразу выполняет CAS в `done` (`result_level=rules`). После t0 + 250 с ни одной задачи в `processing` ([api.md](api.md), раздел 3.7; [ADR-0005](adr/0005-deadline-tiers-provisional-degradation.md), п. 5).

---

## 5. Этап 2. Декодирование и хранилище кадров

Модули: `pipeline/decode/pyav_source.py`, `frame_store.py`, `window.py`. Владелец S3. Порт `VideoSource`: `probe(path)`, `iter_samples(path, plan)`, `decode_window(path, t0, t1, fps)`. Порт `FrameStore`: `put(frame) -> FrameRef`, `get(t_sec) -> ndarray`, `nearest(t_sec) -> FrameRef`, `index() -> FrameIndex`.

### 5.1 Что известно о входе

Обучающие ролики: H.264 High, 464×848 (портрет), ~29,76–30 кадров/с VFR, B-кадры, GOP ~1 с, edit list, moov в конце, единичная матрица поворота, 11–145 с, ~29 МБ. Финальное видео снято под другим углом и может отличаться ориентацией и кодеком.

### 5.2 PTS-сетка

Поток T-decode (PyAV):
- `codec_context.thread_type = 'AUTO'` (по документации PyAV примерно в 5 раз быстрее SLICE по умолчанию).
- Декодируются все кадры: при GOP 1 с с B-кадрами пропускать нельзя. В BGR переводятся только выбранные кадры (примерно каждый десятый при 3 кадрах/с).
- `frame.rotation` применяется вручную (`np.rot90`). PyAV сам не поворачивает, OpenCV и ffmpeg CLI поворачивают — поэтому результаты инструментов расходятся.
- VideoToolbox (`hwaccel`) — за флагом `decode.hwaccel` в `configs/perception.yaml`. Включается, если замер на Mac показал выигрыш.

```
start  = время первого кадра потока (stream.start_time × time_base)
step   = 1 / plan.sample_fps
next_t = 0.0
for frame in container.decode(video_stream):
    t = frame.time − start
    if t < 0: continue                      # кадры до начала по edit list
    if t + eps < next_t: continue           # eps — половина среднего интервала кадров
    img = rotate(frame.to_ndarray(format="bgr24"), frame.rotation)
    queue.put(SampledFrame(idx, t_sec=t, pts=frame.pts, image=img))   # блокирующая, maxsize 16
    while next_t <= t: next_t += step
```

- Очередь ограничена 16 кадрами (~20 МБ). Все кадры ролика в памяти не держатся: 435 кадров × 1,18 МБ ≈ 0,5 ГБ на ролик 145 с.
- Если профилирование покажет конкуренцию за GIL больше 20 %, декодирование уходит в отдельный spawn-процесс с кольцом `shared_memory`.
- Тест: синтетический VFR-ролик (`tests/fixtures/synthetic_video.py`) — таймкоды совпадают с PTS; повёрнутый ролик — поворот применён.

### 5.3 Хранилище кадров

- Поток T-store пишет каждый выбранный кадр в `frames/{pts_ms}.jpg` (JPEG q85, cv2 отпускает GIL), где `pts_ms = round(t_sec × 1000)`. Индекс `frames/index.json` = `FrameIndex{frames: [{idx, t_sec, pts, path}]}`.
- Стоимость: ~1 с CPU на ролик 145 с, ~30 МБ диска (оценка).
- `nearest(t)` возвращает ближайший сохранённый кадр по `t_sec`. `get(t)` — изображение этого кадра.
- Кадры сохраняются уже повёрнутыми, координаты `BBox` нормированы к ним.
- Хранилище удаляется после итогового результата. `evidence/` остаётся до общей очистки через 24 ч.

### 5.4 Плотное окно (P3, за флагом)

`decode_window(path, t0, t1, fps)` повторно декодирует окно `t ± 1 с` с частотой 10 кадров/с. Используется в необязательном уточнении таймкода (раздел 9.5) для ≤ 5 кандидатов-переходов. `input.mov` для этого должен существовать: jury-файл удаляется только после этапа уточнения.

---

## 6. Этап 3. Восприятие

Модули: `pipeline/perception/orchestrator.py`, `plan.py`, `gating.py`, `crops.py`, `trace_io.py`, `backends/*`. Владелец S3. Конфиги: `configs/perception.yaml`, `configs/trackers/botsort_static.yaml`, `configs/models.yaml`.

Главный цикл на каждый `SampledFrame`: детектор + BoT-SORT → поза (по гейту) → классификаторы фрагментов (по гейту, все фрагменты кадра одним вызовом) → качество кадра → `FrameObservation`. Прогресс в SQLite — не чаще 2 раз в секунду.

### 6.1 Модели

| Роль (`configs/models.yaml`) | Модель | Вход | Экспорт Core ML | Выход |
|---|---|---|---|---|
| `detector` | YOLO26l detect, 14 классов, обучен при imgsz 864 | 864×480 или 480×864 | `nms=False` (NMS-free end2end), `quantize=16`, `batch=1` | `Detection{cls, conf, box, track_id}` |
| `pose` | YOLO26l-pose, готовые веса | 864×480 или 480×864 | те же флаги | `PoseKeypoints{track_id, box, kpts[17]}` (порядок COCO) |
| `cls_screen` | YOLO26s-cls | 224 | `imgsz=224` | `active` / `locked` / `off` |
| `cls_safe_lock` | YOLO26s-cls | 224 | `imgsz=224` | `key` / `no_key` |
| `cls_door` | YOLO26s-cls | 224 | `imgsz=224` | `closed` / `open` |

Классы детектора (`DetClass`, порядок `training/configs/classes.yaml`, менять нельзя): `person`, `cash`, `collection_bag`, `safe_closed`, `safe_open`, `laptop`, `door`, `counting_machine`, `auth_detector`, `id_document`, `phone`, `cup`, `personal_bag`, `outerwear`. Имена ASCII; русские названия — только в UI.

Требования:
- Каждая модель прогревается одним холостым вызовом до `state=idle`. Первая компиляция под Neural Engine может занять десятки секунд (оценка; замер — `bench_mac.py`); обновление macOS сбрасывает кэш. Альбомные варианты (`det_480x864`, `pose_480x864`) грузятся лениво; `/ready` от этого не падает (проверяет вариант `rot90` прогона robustness, [eval.md](eval.md), раздел 7.1).
- sha256 файлов сверяется с `configs/models.yaml` при старте. Несовпадение — воркер не стартует.
- Модель принимается, только если `yolo val` для `.mlpackage` на отложенной сцене отстаёт от `.pt` не больше чем на 1 mAP50, и `MLComputePlan` подтверждает размещение на Neural Engine.
- Рисовать боксы через `Results.plot()` нельзя: Ultralytics скачивает шрифт. Только cv2 (раздел 10).

### 6.2 Детектор

- Вызов: `model.track(frame, persist=True, tracker="configs/trackers/botsort_static.yaml", conf=detector.conf_min, verbose=False)`. Трекер передаётся явно всегда: в Ultralytics 8.4.x трекер по умолчанию — TrackTrack.
- `detector.conf_min` не выше `track_low_thresh` (0.1): BoT-SORT использует низкоуверенные детекции во втором проходе. Пороги по классам применяет сцена, а не восприятие. В трассу пишутся все детекции не ниже `conf_min`.
- Боксы нормируются к 0..1 в координатах повёрнутого кадра.
- Трекаются все классы. `track_id` есть и у неподвижных предметов — это помогает сцене.

### 6.3 Трекинг BoT-SORT

`configs/trackers/botsort_static.yaml` (владелец S3):

```yaml
tracker_type: botsort
track_high_thresh: 0.4
track_low_thresh: 0.1
new_track_thresh: 0.5
track_buffer: 15
match_thresh: 0.8
fuse_score: true
gmc_method: none        # камера неподвижна, sparseOptFlow тратит CPU впустую
with_reid: false        # без ReID по внешности (FAQ 29), без скрытой загрузки модели
# остальные обязательные ключи — как в botsort.yaml пинованной версии ultralytics (проверить)
```

- Состояние трекера сбрасывается в начале каждой задачи (`predictor.trackers` = новый объект). Иначе при `persist=True` треки переходят между задачами.
- `track_buffer` считается в обновлениях трекера, а не в секундах: 15 обновлений = 5 с при 3 кадрах/с, 10 с при 1,5 кадра/с, 15 с при 1 кадре/с. Сшивку разорванных треков делает сцена (раздел 7.2).
- Тест: трекер передан явно, файл конфигурации существует.

### 6.4 Гейтинг позы и классификаторов

`gating.py` решает, запускать ли позу и классификаторы на текущем кадре.

**Онлайн-раскладка.** Зоны нужны уже во время восприятия. `gating.py` вызывает ту же функцию калибровки, что и сцена (`scene/zones.py`, чистая функция), по детекциям неподвижных классов за окно калибровки. Второго алгоритма зон нет. До окончания калибровки гейт разрешающий: поза на всех кадрах с людьми с частотой `pose_fps`, классификаторы с базовой частотой.

**Поза.** YOLO26l-pose работает по всему кадру, а не по треку. Поэтому гейт решает про кадр целиком:
- `full`: поза запускается, если на кадре есть хотя бы один трек `person`, чья нижняя точка бокса лежит в зонах `desk`, `cashier_side`, `client_side` или `safe` (зона «передачи» — это `desk` между сторонами), и с прошлого запуска прошло ≥ `1 / pose_fps` (≤ 2 кадров/с).
- `fast`: только в окнах-кандидатах. Окно открывает триггер и держится `pose.trigger_hold_sec` (подобрать) после него. Триггеры: бокс `cash` пересекается с боксом `person` в зоне `desk`; `cup` или `phone` пересекается с верхней третью бокса `person`; появился `collection_bag` или `id_document`.
- `min`: поза не запускается.
- Скелеты сопоставляются с треками по IoU с боксами `person` (`pose.match_iou`, подобрать). Несопоставленные скелеты отбрасываются.
- Ожидаемая экономия вызовов позы — 30–60 % (оценка).

**Классификаторы состояний.**
- Базовая частота: `cls_fps` (1,5 кадра/с на `full`) по каждой цели.
- Триггеры (на всех уровнях): впервые появился исходный бокс (`laptop`, `safe_closed`, `door`); смена `safe_open` ↔ `safe_closed`; трек `person` вошёл в зону `door` или вышел из неё; трек кассира покинул `desk` или `cashier_side` (кандидат на уход); резкое изменение яркости кадра (свет выключили, как в Х-11). После триггера классификаторы идут на `cls.trigger_hold_frames` (подобрать) кадрах подряд.
- Все фрагменты кадра идут в один вызов `predict` списком.

### 6.5 Фрагменты для классификаторов

`crops.py` обязан вырезать фрагменты ровно так же, как обучающий скрипт `training/scripts/11_extract_crops.py`: region, pad, upscale и min_side — из `data/crops/crop_spec.json` (копия — в манифесте модели). Контрактный тест сравнивает выход `crops.py` с crop_spec на синтетической рамке (P1-S3-02).

| Цель | Исходный класс | Область бокса (x1, y1, x2, y2), доли | Поле | Увеличение | Классы |
|---|---|---|---|---|---|
| `screen` | `laptop` | 0, 0, 1, 1 | 0,05 | ×1 | `active`, `locked`, `off` |
| `safe_lock` | `safe_closed` | 0,55, 0,25, 1,0, 0,75 (замок справа посередине) | 0,15 | ×4 | `key`, `no_key` |
| `door` | `door` | 0, 0, 1, 1 | 0,05 | ×1 | `closed`, `open` |

- Берётся самый уверенный бокс исходного класса на кадре. Короткая сторона фрагмента — не меньше 224 px.
- Letterbox до квадрата делается, только если в `crop_spec.json` есть `letterbox: true`. Сейчас `11_extract_crops.py` letterbox не делает, и классификаторы обучены и проверены на val с предобработкой Ultralytics (Resize по короткой стороне + CenterCrop) на неквадратных фрагментах. Поэтому без этого поля фрагмент идёт в Ultralytics как есть, как на val. Если сервис дополнит до квадрата, а обучение — нет, вход классификаторов `screen` и `safe_lock` исказится, а ключ в замке ~10 px. Поле `letterbox: true` может появиться только из правки на стороне обучения (по согласованию с сессией обучения).
- `safe_lock` вырезается только при `safe_closed`. При открытом сейфе действует `SAFE_OPEN`.
- Выход: `CropState{target, source_box, label, conf}`.

### 6.6 Качество кадра

На каждом выбранном кадре по уменьшенному серому изображению: `brightness` (среднее) и `blur` (дисперсия лапласиана). Это дёшево и нужно для сигнала `SIG_CAMERA_OBSTRUCTED`, для учёта выключенного света (Х-11) и для отладки. Пишется в `FrameObservation.quality`.

### 6.7 Трасса восприятия

`PerceptionTrace{schema_version, video: VideoMeta, models: {role: {version, sha256}}, plan, frames: list[FrameObservation]}` → `trace.json.gz` (`trace_io.py`). Это единица replay: её читают S4, S5, S6.

- `FrameObservation{idx, t_sec, detections, poses | None, crops, quality | None}`.
- Трасса задачи жюри или UI никогда не копируется в `var/signals/`.
- Отказ бэкенда на кадре (исключение) не роняет задачу: кадр пишется без детекций, ошибка — в лог; больше `perception.max_failed_frames` (подобрать) подряд → исключение и requeue.

### 6.8 Расхождения с текущим `training/` (S3)

Код обучения не совпадает с требованиями сервиса. Скрипты `training/scripts/00–12` и `run_stage_*.sh` не переписываются: модели для сервиса выпускает экспортёр `src/cashguard/pipeline/perception/export.py` (новый путь, S3), а в `training/` идут только аддитивные правки по согласованию с сессией обучения ([roadmap.md](roadmap.md), P0-S3-04, P1-S3-05).

| Что | Сейчас в `training/` | Как получает сервис |
|---|---|---|
| Экспорт детектора (`10_export_coreml.py`, C4) | `nms=True` для detect, без `quantize` (точность выбирает экспорт), один размер входа | экспортёр сервиса: `nms=False`, FP16 (имя аргумента — проверить), `batch=1`, imgsz 864×480 и 480×864 |
| Экспорт позы (C5) | imgsz по умолчанию 640 (квадрат) | экспортёр сервиса: 864×480 и 480×864 |
| Где лежат модели | `models/<task>/<name>.mlpackage`, общий `models/manifest.json` (сервис их не читает) | `models/<role>/vNNN/{best.pt, *.mlpackage, manifest.json}` |
| Где запускать экспорт | C4–C5 — `.venv-train` (остаются как есть) | экспортёр — venv сервиса (те же ultralytics, coremltools, numpy 2.3.5), режим DEV |
| Фрагменты cls (`11_extract_crops.py`) | без letterbox, `min_side` 224 | `crops.py` повторяет `crop_spec.json` один к одному (раздел 6.5) |
| Аугментации cls (`12_train_cls.py`) | по умолчанию | аддитивный PR: `erasing: 0.0`, `auto_augment: null` в `cls.common` `train.yaml` |

---

## 7. Этап 4. Сцена

Модули: `pipeline/scene/zones.py`, `roles.py`, `sessions.py`, `states.py`, `events.py`, `presence.py`, `build.py`. Владелец S4. Конфиг `configs/scene.yaml`. Чистый Python + numpy, детерминированно. Выход — `SceneModel{layout, roles, sessions, states, events}`.

### 7.1 Зоны (`zones.py`)

Зоны строятся по предметам, а не по пикселям. Поэтому они переносятся на новый ракурс.

- **Калибровка.** Медианные боксы неподвижных классов (`safe_closed`/`safe_open`, `laptop`, `door`, `counting_machine`, `auth_detector`) за первые `calibration.window_sec` секунд (подобрать). Нужно не меньше `calibration.min_frames` кадров с классом.
- **Зоны** (`ZoneKind`):

| Зона | Как строится |
|---|---|
| `desk` | Выпуклая оболочка боксов `laptop`, `counting_machine`, `auth_detector` (и устойчивых `cash` за окно), расширенная на `zones.desk_margin` |
| `cashier_side` | Полуплоскость у стола со стороны, где дольше всех находится трек, работающий с ноутбуком, или где сейф |
| `client_side` | Противоположная сторона стола |
| `safe` | Бокс сейфа + `zones.safe_margin` |
| `door` | Бокс двери + `zones.door_margin` |
| `counting_machine`, `auth_detector` | Бокс + поле |
| `room` | Весь кадр |

- **Если предмета нет.** Нет двери → зона входа — край кадра, где чаще всего рождаются и исчезают треки людей. Нет ноутбука → стол по машинке и детектору. Каждая зона несёт `conf`; правила, которым нужна зона с низкой уверенностью, понижают свою уверенность.
- **Перекалибровка.** Если медианный бокс неподвижного предмета сместился (IoU с калибровкой ниже `calibration.recalib_iou`), зоны пересчитываются с этого момента.
- `SceneLayout{zones, orientation, calibrated_from_sec, conf}`.

### 7.2 Роли (`roles.py`)

Без лиц и без внешности. `Role = cashier | client | collector | outsider | unknown`.

| Роль | Признак |
|---|---|
| `cashier` | Трек, который дольше всех на `cashier_side`, работает с ноутбуком или открывает сейф |
| `client` | Вошёл через дверь и сел (или встал) на `client_side` в течение `roles.client_grace_sec` |
| `collector` | Вошёл с `collection_bag`: бокс сумки рядом с треком в течение `roles.collector_link_sec` после входа |
| `outsider` | Вошёл и не стал клиентом или инкассатором за `roles.outsider_grace_sec`; или прошёл на `cashier_side`; или вошёл, когда кассира нет |
| `unknown` | Остальное |

- **Сшивка треков.** Новый трек, который родился не в зоне двери в пределах `tracks.stitch_gap_sec` и `tracks.stitch_dist` от места, где потерян другой трек, наследует его роль. Повторный вход кассира: трек вошёл через дверь после ухода кассира и прошёл на `cashier_side` → `cashier`.
- `TrackRole{track_id, role, conf, first_t, last_t, entered_via_door, exited_via_door}`.
- Назначение кассира оператором (PDF, раздел «Контроль присутствия») в v1 не делается — см. [roadmap.md](roadmap.md).

### 7.3 Сеансы (`sessions.py`)

- `client`: от посадки клиента (или прихода на `client_side`) до его выхода.
- `collection`: от входа инкассатора до его выхода.
- `id_shown_t` — первое событие `id_shown` в окне [вход субъекта; конец сеанса].
- `ServiceSession{id, kind, subject_track_id, start_t, end_t, id_shown_t | None}`.

### 7.4 Состояния (`states.py`)

Каждое состояние — интервалы `StateInterval{subject, value, start_t, end_t, conf, zone | None, track_id | None}` с гистерезисом: значение меняется, только если новое держится ≥ `states.<subject>.min_on_sec`, а старое не держалось ≥ `min_off_sec`. Сырые значения ниже порога уверенности → `unknown`.

| `subject` | Значения | Источник |
|---|---|---|
| `screen` | `active`, `locked`, `off`, `unknown` | C `cls_screen` по `laptop`; нет ноутбука → `unknown` |
| `safe` | `open`, `closed` | D `safe_open` / `safe_closed` |
| `safe_key` | `key`, `no_key`, `unknown` | C `cls_safe_lock`, только при `safe=closed` |
| `door` | `open`, `closed`, `unknown` | C `cls_door` |
| `cash_on_desk` | `present`, `absent` | D `cash` в зоне `desk` (`track_id` — трек кластера) |
| `bag_on_desk` | `present`, `absent` | D `collection_bag` в зоне `desk` |
| `personal_item_on_desk` | `present`, `absent` | D `personal_bag` или `outerwear` в `desk` или `cashier_side` |
| `cup` | `on_desk`, `at_mouth` | D `cup` + P (запястье и стакан у лица) |
| `phone` | `in_cashier_hand`, `in_other_hand`, `on_desk` | D `phone` + P + роли |
| `cashier_present` | `present`, `absent`, `unknown` | T + `presence.py` |

Словарь значений фиксирует S4. Если правилам понадобится поле `data` в `StateInterval` (например, принадлежность кластера денег сеансу), это отдельный PR в `contracts/` через S0. До этого принадлежность считают помощники `rules/context.py`.

### 7.5 События (`events.py`)

`SceneEvent{kind, t, track_ids, conf, box | None, data}`.

| `kind` | Определение | Сигналы |
|---|---|---|
| `enter` | Трек родился в зоне `door` или пересёк её внутрь | T |
| `exit` | Трек пересёк зону `door` наружу или ушёл за край кадра у двери | T |
| `sit`, `stand` | Бокс трека меняет высоту и останавливается у стола; уточняется позой | T, P |
| `handoff_cash` | Бокс `cash` рядом с запястьями одного трека, затем рядом с запястьями другого; `data.direction` = `client_to_cashier`, `cashier_to_client`, `other` | D, P, T |
| `cash_to_machine` | Бокс `cash` входит в зону `counting_machine` | D |
| `cash_to_detector` | Бокс `cash` входит в зону `auth_detector` | D |
| `cash_to_safe` | Бокс `cash` входит в зону `safe` при `safe=open` и исчезает | D |
| `cash_from_bag` | `cash` появляется рядом с открытой `collection_bag` | D |
| `id_shown` | `id_document` в зоне `desk` или у запястий субъекта сеанса | D, P |
| `bag_on_desk` | Начало `bag_on_desk=present` | D |
| `cup_to_mouth` | Запястье со стаканом у ключевых точек лица | D, P |
| `phone_in_hand` | `phone` у запястья трека; `data.role` | D, P |
| `state_change` | Смена значения любого `StateInterval`; `data.subject`, `data.from`, `data.to` | — |

### 7.6 Присутствие (`presence.py`)

Модуль присутствия из списка команды (PDF, стр. 13–14):
- Уход подтверждается только пересечением зоны двери или границы рабочего места наружу.
- **Потеря трека из-за перекрытия или темноты — не уход.** `cashier_present = unknown`, а не `absent`.
- Подход к сейфу или машинке внутри помещения — не отсутствие.
- Интервалы `cashier_present` и список других людей в помещении, пока кассира нет, — вход для `UNATTENDED_PRESENCE`, `VALUABLES_LEFT`, `SAFE_OPEN`, `PC_UNLOCKED`, `SAFE_KEY_LEFT`, `LEFT_DURING_SERVICE`.

---

## 8. Этап 5. Правила

Каркас: `pipeline/rules/base.py`, `registry.py`, `context.py` (S0 в P0, затем S4). Правила — по одному файлу на код. Логика каждого кода — в [violation_catalog.md](violation_catalog.md).

### 8.1 Раскладка файлов

| Группа | Папка | Владелец | Коды |
|---|---|---|---|
| Уход, присутствие | `rules/leave/` | S4 | `PC_UNLOCKED`, `SAFE_KEY_LEFT`, `VALUABLES_LEFT`, `SAFE_OPEN`, `LEFT_DURING_SERVICE`, `UNATTENDED_PRESENCE` |
| Доступ | `rules/access/` | S4 | `DOOR_NOT_CLOSED`, `OUTSIDER_IN_CASH_ROOM`, `THIRD_PERSON_DURING_SERVICE`, `STORAGE_UNESCORTED` |
| Кассовые операции | `rules/cashops/` | S5 | `PREV_CASH_ON_DESK`, `MIXED_CLIENT_CASH`, `NO_CLIENT_ID`, `RECOUNT_WITHOUT_CLIENT`, `CASH_WITHHELD`, `NO_AUTH_CHECK`, `PAYOUT_WITHOUT_RECOUNT`, `CHANGE_BEFORE_PREPARED`, `CASH_CONCEALED`, `CASH_TRANSFER_OUTSIDE_OP` |
| Инкассация | `rules/collection/` | S5 | `COLLECTOR_NOT_VERIFIED`, `BAG_CASH_NO_RECOUNT` |
| Дисциплина | `rules/discipline/` | S5 | `PERSONAL_ITEMS`, `FOOD_DRINK`, `PHONE_CASHIER` |
| Сигналы | `rules/signals/` | S6 | 13 кодов `SIG_*` |

Имя файла — код в нижнем регистре: `rules/leave/pc_unlocked.py` ↔ `PC_UNLOCKED`, `rules/signals/sig_camera_obstructed.py` ↔ `SIG_CAMERA_OBSTRUCTED`. У каждого кода: `configs/rules/<CODE>.yaml`, `configs/catalog/<CODE>.yaml`, `tests/unit/rules/<CODE>_test.py`, фикстуры `tests/fixtures/timelines/<CODE>_{pos,neg}_*.json`.

`OTHER_VISIBLE_IRREGULARITY` файла правила не имеет: его кандидатов создаёт `verdict/decide.py` из находок VLM (раздел 13.6). Контрактный тест полноты каталога учитывает это исключение.

### 8.2 Регистрация и обнаружение

```python
# src/cashguard/pipeline/rules/leave/pc_unlocked.py
from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import Candidate
from cashguard.pipeline.rules.base import register_rule
from cashguard.pipeline.rules.context import RuleContext


@register_rule
class PcUnlocked:
    code = ViolationCode.PC_UNLOCKED
    version = "1"
    verdict_impact = True

    def evaluate(self, ctx: RuleContext) -> list[Candidate]:
        ...
```

- `@register_rule` кладёт класс в реестр по `code`. Повтор кода → ошибка при старте.
- `registry.discover()` обходит подпакеты `cashguard.pipeline.rules.*` через `pkgutil.walk_packages` и импортирует модули. Центрального списка нет.
- Сигнальные правила — тот же декоратор, протокол `SignalRule{code: SignalCode; evaluate(ctx) -> list[Signal]}`.
- `rules.evaluate_all(ctx)` вызывает каждое включённое правило со своим `cfg`. Исключение в правиле: лог с `job_id` и кодом, задача продолжается без этого правила. В pytest и `eval-replay` исключение правила — провал.
- Подпакеты `rules.*` не импортируют друг друга. Общие помощники — только в `context.py` и `base.py`.

### 8.3 Контекст правила

`RuleContext{video: VideoMeta, trace: PerceptionTrace, scene: SceneModel, cfg: RuleConfig}`. Помощники в `context.py` (список задаёт S0 в P0, расширяет S4):

| Помощник | Что возвращает |
|---|---|
| `ctx.states(subject, value=None)` | Интервалы состояния, отсортированные по времени |
| `ctx.state_at(subject, t)` | Значение и уверенность в момент `t` |
| `ctx.events(kind, t0=None, t1=None)` | События вида в окне |
| `ctx.tracks(role)` | Треки роли |
| `ctx.cashier_absent()` | Подтверждённые интервалы отсутствия кассира (без `unknown`) |
| `ctx.sessions(kind=None)`, `ctx.session_at(t)` | Сеансы обслуживания |
| `ctx.cash_clusters(t)` | Кластеры `cash` на столе с принадлежностью сеансу по времени появления |
| `ctx.zone(kind)` | Зона и её уверенность |
| `ctx.det_quality(cls, t0, t1)` | Средняя уверенность детекций класса в окне |
| `ctx.anchor(t)` | Таймкод, привязанный к ближайшему сохранённому кадру (из индекса трассы) |

### 8.4 Кандидат

`Candidate{id, code, t_sec, start_sec, end_sec, confidence, facts, evidence, vlm_group, rule_version, verdict_impact}`:
- `t_sec` — по политике таймкода кода (`timecode_policy` в каталоге: `event`, `exit`, `state_start`, `omission_due`, см. [violation_catalog.md](violation_catalog.md#03-политики-таймкода)). Всегда в [0; длительность].
- `confidence` 0..1: произведение качества детекций (средняя уверенность ключевых классов в окне) и уверенности сцены (зоны, роли). Длительность учитывает вердикт, а не правило.
- `facts` — только значения для шаблона описания: числа и короткие строки. Имена полей совпадают с `required_facts` каталога.
- `evidence` — `EvidenceRequest{kind: event|zoom|context, t_sec, box, label}`. Правило задаёт хотя бы `event`; `zoom` — с боксом главного предмета (экран, замок, деньги, дверь).
- `vlm_group` — необязательная подсказка для группировки (по умолчанию группирует VLM по времени).
- Правило выдаёт не больше `max_candidates` на код (стартовое значение 3 — предложение, уточняет владелец кода).

### 8.5 Параметры правила

`configs/rules/<CODE>.yaml` → `RuleConfig`. Общие ключи (все численные значения подбираются LOSO, до этого — `null` и правило выключено):

```yaml
code: PC_UNLOCKED
version: 1
enabled: true            # B1-коды: false, пока не пройдена проверка на 19 чистых роликах
tau: null                # порог по правилам (раздел 9), LOSO на recall 1.0
tau_final: null          # порог после VLM, eval-full при LOSO recall 1.0
ref_duration_sec: null   # длительность, при которой duration_factor = 1
min_duration_sec: null   # минимальная длительность состояния-нарушения
anchor: exit             # точка таймкода, если код допускает варианты
suppresses: []           # [{code: OUTSIDER_IN_CASH_ROOM, same_track: true, window_sec: ...}]
max_candidates: 3        # предложение
params:                  # параметры конкретного правила
  screen_active_min_sec: 2.0   # из detection_design: экран active ≥ 2 с после ухода
  check_window_sec: null
tuned:                   # заполняет just loso
  method: loso
  folds: [L1, L2, L3, L4, L5]
  date: null
  recall: null
  clean_firings: null
```

### 8.6 Включение кода

Код влияет на вердикт, только если:
1. проходят фикстуры `pos` и `neg`;
2. в каталоге есть название, шаблон и норма, и цитата нормы найдена дословно в `analysis/normative/*.txt` (контрактный тест);
3. `just eval-replay`: LOSO recall по видео остаётся 1.0, нет новых срабатываний кодов вердикта на 19 чистых роликах, точность и медиана |Δt| в допуске базовой линии `eval/baselines/main.json`.

Коды B1 без обучающих примеров стартуют с `enabled: false` и включаются после проверки на чистых роликах и своей съёмке (FAQ 7).

---

## 9. Этап 6. Вердикт

Модули: `pipeline/verdict/scoring.py`, `aggregate.py`, `decide.py`. Владелец S5. Функция `verdict.decide(cands, review | None, cfg) -> list[Decision]`, `Decision{candidate, score, vlm_status}`.

### 9.1 Балл

```
duration_factor = clip((end_sec − start_sec) / ref_duration_sec, 0, 1)   # для событий без длительности = 1
score           = candidate.confidence × duration_factor
```

### 9.2 Слияние и подавление

1. **Один код.** Кандидаты одного кода ближе 10 с сливаются: остаётся кандидат с большим баллом, интервал — объединение.
2. **Подавление между кодами** по `suppresses` из `configs/rules/<CODE>.yaml`. Стартовые пары (уточняют S4 и S5 по eval):

| Код-победитель | Подавляет | Условие |
|---|---|---|
| `THIRD_PERSON_DURING_SERVICE` | `OUTSIDER_IN_CASH_ROOM` | Тот же трек, окно подобрать |
| `THIRD_PERSON_DURING_SERVICE` | `CASH_TRANSFER_OUTSIDE_OP` | Тот же трек (Х-18: второй человек передаёт предмет) |
| `CASH_WITHHELD` | `CASH_CONCEALED` | Тот же кластер денег в сеансе выдачи |
| `UNATTENDED_PRESENCE` | `STORAGE_UNESCORTED` | Кассир отсутствует |
| `SAFE_OPEN` | `SAFE_KEY_LEFT` | Сейф открыт (ключ проверяется только при закрытом) |

Разные события в одном ролике не подавляются: Х-4/Х-47 — это `PREV_CASH_ON_DESK` (57 с) и `MIXED_CLIENT_CASH` (68 с), оба должны остаться.

### 9.3 Порог τ и отбор

- Порог на код: `tau` для результата по правилам, `tau_final` для итога после VLM. Подбор `tau` — `just loso` по сценам L1–L5 так, чтобы recall по видео был 1.0 (FAQ 16: пропуск недопустим). `tau_final` подбирается только по `eval-full` (P3-S5-01), не по replay. Ложные срабатывания гасит VLM.
- Кандидат проходит, если `score ≥ τ` и `verdict_impact = true`. Сигналы и кандидаты ниже порога идут только в UI.
- Не больше 5 нарушений: если прошло больше, берутся 5 с наибольшим баллом. Затем сортировка по `timecode_sec`.
- `verdict = violation` ⇔ прошло хотя бы одно нарушение.
- FAQ 11 (отсылает к ТЗ §4): если хоть один код вердикта выше τ — `verdict=violation`; неразборчивые фрагменты дают только сигнал `SIG_CAMERA_OBSTRUCTED`; третьего вердикта нет (ТЗ §4–5, FAQ 15).

### 9.4 Учёт VLM

| Ответ VLM | Действие | `vlm_status` |
|---|---|---|
| `yes` | Балл не меняется | `confirmed` |
| `no` с `c ≥ vlm.scoring.no_conf_min` | `score × vlm.scoring.down_factor` | `downweighted` |
| `no` с низкой `c`, `unclear`, ошибка, нет ответа | Не меняется | `unverified` |
| VLM не запускался | Не меняется | `skipped` |

- VLM никогда не повышает балл. Пониженный кандидат остаётся видимым в UI с пометкой «снижено VLM».
- Если VLM пропущен, результат по правилам — надмножество, безопасное по recall.
- `down_factor`, `no_conf_min` и `tau_final` подбираются вместе на `eval-full` при условии LOSO recall 1.0.

### 9.5 Уточнение таймкода (P3, за флагом)

Для ≤ 5 кандидатов-переходов (уход, дверь, открытие сейфа) — повторное декодирование `t ± 1 с` при 10 кадрах/с и запуск только нужной модели. Первый кадр, где состояние держится, даёт более точный таймкод. Кадр добавляется в хранилище, чтобы evidence взял именно его. Флаг `perception.refine.enabled`.

---

## 10. Этап 7. Кадры-доказательства

Модули: `pipeline/evidence/select.py`, `draw.py`, `jpeg.py`. Владелец S3. Функция `evidence.render(decisions, store, out_dir) -> dict[str, list[EvidenceImage]]`.

### 10.1 Выбор кадров

На каждое нарушение до трёх кадров, в этом порядке:

| № | `kind` | Кадр | Содержимое |
|---|---|---|---|
| 1 | `event` | `store.nearest(t_sec)` | Весь кадр, рамки cv2 вокруг ключевых предметов и людей |
| 2 | `zoom` | Тот же кадр | Фрагмент ×3–4 вокруг бокса `EvidenceRequest(zoom)`: экран, замок, деньги, дверь |
| 3 | `context` | `store.nearest(t_sec − 1.5)`, отличный от `event` | Весь кадр с рамками: что было до события |

- `timecode_sec` нарушения приравнивается к `t_sec` выбранного кадра `event` (округление до 0,01). Так выполняется инвариант `evidence_frames[0].timestamp_sec == timecode_sec`.
- У `zoom` метка времени та же, что у `event`.
- Нет бокса для `zoom` → кадра 2 нет. `t_sec − 1,5 < 0` → контекст берётся из `t_sec + 1,5`, если это другой кадр; иначе кадра 3 нет. Минимум — один кадр `event`.
- Для кода с политикой `exit` контекстный кадр показывает уход; для `omission_due` — момент, когда шаг должен был случиться, и кадр до него.
- Сигналы получают один кадр `event` (UI).

### 10.2 Рисование и кодирование

- Рамки и подписи — только `cv2.rectangle` и `cv2.putText` с ASCII (имена классов и состояний: `laptop: screen=active`). Кириллицу cv2 не рисует; `Results.plot()` запрещён.
- Боксы из нормированных координат переводятся в пиксели сохранённого (повёрнутого) кадра.
- Зум: вырезать бокс с полем, увеличить ×3–4 (короткая сторона не меньше 224 px), `cv2.INTER_CUBIC`.
- JPEG q85 через `cv2.imencode`. Файлы: `evidence/{i}_{event|zoom|context}.jpg`, где `i` — номер нарушения 0–4. Имя должно совпадать с `^\d{1,2}_(event|zoom|context)\.jpg$` (маршрут UI). Сигналы — номера 10–29 (предложение, S1 подтверждает).
- `EvidenceImage{kind, timestamp_sec, file, width, height}`. Кодирование ~5 мс на кадр (оценка).
- После VLM перерисовываются только изменённые или новые находки.

---

## 11. Этап 8. Описание и правовая ссылка

Модули: `pipeline/legal/catalog.py` (загрузка и проверка каталога и актов), `describe.py`. Владелец S5. Функция `legal.describe(decision, scene, catalog) -> tuple[str, list[LegalRef]]`.

### 11.1 Текст наблюдения

- Шаблон `template_ru` из `configs/catalog/<CODE>.yaml` заполняется `str.format_map` по `facts` кандидата. jinja2 не используется.
- Перед подстановкой `describe.py` форматирует значения: секунды — с одним знаком и запятой (`14,0`), длительности — целые или с одним знаком.
- Не хватает поля из `required_facts` → общий шаблон «В {t} с наблюдается: {title_ru}.» и ошибка в лог. Тест каталога ловит это заранее.
- Только наблюдаемое: кто, что, где, когда. Без «хищения» и без умысла (ТЗ, раздел 7). Свободный текст VLM в описание жюри не попадает; `VlmAnswer.note` — только UI и отладка.

### 11.2 Правовое предложение

Добавляется к тексту по силе основной (первой) записи `legal`. Шаблоны — дословно из [legal_sources.md](legal_sources.md), раздел 6.2, и [ADR-0006](adr/0006-frozen-jury-contract.md), п. 5:

| Сила | Формулировка |
|---|---|
| `direct` | `Основание: {clause} {act_cite}.` |
| `indirect` | `Может свидетельствовать о несоблюдении {clause} {act_cite}.` |
| `none`, ближайший пункт есть | `Прямой нормы нет; ближайшая — {clause} {act_cite}.` |
| `none`, пункт не определён (FAQ 31) | `Прямой нормы нет; пункт {act_cite} не определён (FAQ 31); требуется проверка человеком.` |

- `{clause}` хранится в каталоге уже с префиксом: `п. 73 пп. 6)`, `п. 20 ч. 1`. Шаблон префикс не добавляет.
- `{act_cite}` — поле `cite_ru` записи акта в `configs/legal/acts.yaml`, например: «Правил осуществления кассовых операций…, утв. постановлением Правления НБРК от 29.11.2019 № 231» ([case_analysis.md, раздел 7](../analysis/case_analysis.md#7-нормативная-база-рк)).
- Источник норм — только раздел 7 `case_analysis.md`; цитата `quote_ru` — дословно из `analysis/normative/*.txt` (контрактный тест). Утратившие силу акты (НБРК № 58 от 2001 г., № 250 от 2012 г., № 48 от 2018 г., № 254 от 2012 г.) не цитируются.
- Общее предложение «Результат требует проверки человеком.» к описаниям не добавляется ([legal_sources.md](legal_sources.md), раздел 6.4). Фраза о проверке человеком есть только в шаблоне без пункта.
- Сигналы используют формулировки «не наблюдается…» из `configs/catalog/signals.yaml`; норм у сигналов в v1 нет.

Пример (`PC_UNLOCKED`, Х-28):

```
В 7,0 с кассир покидает рабочее место. Экран ноутбука остаётся включённым и не заблокированным
ещё 4,0 с после ухода. Может свидетельствовать о несоблюдении п. 73 пп. 6) Требований к обеспечению
информационной безопасности банков…, утв. постановлением Правления АРРФР от 03.04.2026 № 53.
```

(«4,0 с» — иллюстрация формата, не измерение. Основная норма `PC_UNLOCKED` — ТИБ п. 73 пп. 6) «недопущение работы в информационных системах под чужими учетными записями»; п. 71 пп. 9) об автоблокировке через 30 минут — требование к системе, а не к кассиру, и показывается только в интерфейсе, [legal_sources.md](legal_sources.md), раздел 5.1. S5 может выбрать силу `none` и форму «Прямой нормы нет; ближайшая — п. 73 пп. 6) …».)

---

## 12. Этап 9. Рендер и предварительный результат

Модули: `pipeline/render/jury.py`, `ui.py`. Владелец S1.

### 12.1 `analysis.json`

`AnalysisResult{schema_version, job_id, verdict, result_level, perception_level, violations: list[ViolationFinding], signals: list[SignalFinding], video, timings, degradations, versions: {git_sha, models_hash, config_hash}}`. Полный внутренний результат; из него строятся оба ответа.

### 12.2 `jury.json`

`render.jury(result, evidence_dir) -> JuryJobResponse`:
1. `type` = `title_ru` из каталога; `description` = текст + правовое предложение.
2. `timecode_sec` — Python float, округление до 0,01, в пределах [0; длительность].
3. `evidence_frames`: 1–3 кадра в порядке event, zoom, context; `image_base64` — сырой base64 JPEG, начинается с `/9j/`, без префикса `data:`.
4. Не больше 5 нарушений, по времени. `verdict = violation` ⇔ список не пуст.
5. Проверка: `jsonschema` по `contracts/schemas/jury_job.schema.json` + инварианты (`evidence_frames[0].timestamp_sec == timecode_sec`).
6. **Запасной рендер** при любой ошибке проверки: заново кодирует кадры, зажимает таймкоды в [0; длительность], обрезает кадры до 3, заново применяет инвариант вердикта, проверяет снова.
7. **Последний рубеж** (предложение, S1): если и это не прошло, а вердикт `violation`, нарушение сохраняется с заранее закодированным серым JPEG в качестве кадра. Вердикт не теряется ради формы. Ошибка в лог.
8. Запись атомарно: tmp → fsync → `os.replace`. Размер 0,3–1,5 МБ.

Ответы `processing` и `failed` файлами не пишутся: их формирует API фиксированной формой.

### 12.3 `ui_result.json`

`render.ui(result) -> UiJobView` (фрагмент): URL вместо base64 (`/api/ui/jobs/{id}/frames/{name}`, `/api/ui/jobs/{id}/video.mp4`), структурированные `legal` с цитатой и ссылкой adilet, `signals`, `vlm_status`, `provisional`, `stages`, `degradations` с `reason_ru`, `timings`.

### 12.4 Предварительный результат и передача в VLM

После рендера по правилам (`result_level=rules`):
1. Все файлы записаны атомарно. Затем CAS `set_provisional(job_id, paths, next_stage, result_level, degradations)` ставит `provisional_at`. С этого момента UI показывает карточки с пометкой «Предварительно», а sweeper может финализировать задачу по сроку.
2. Задача уходит в VLM (`stage=vlm`, `status=queued`), если одновременно:
   - `CG_VLM_ENABLED`;
   - `now + est_vlm + 15 с < vlm_start_cutoff`, где `est_vlm` — EWMA `vlm_group_call_sec` × число групп + `keyframe_pass_sec`;
   - свободно ≥ `CG_MIN_FREE_MEM_GB` (3,0 ГБ; формула — [architecture.md](architecture.md), раздел 8);
   - очередь VLM-воркера успевает по оценке;
   - есть кандидаты (включая ниже τ) или включён keyframe pass.
3. Иначе `status=done`, `result_level=rules` с причиной деградации (раздел 15).
4. Для jury-задач `input.mov` удаляется здесь (после уточнения таймкода, если оно включено). Хранилище кадров живёт до итогового результата.

---

## 13. Этап 10. Проверка VLM

Модули: `pipeline/vlm/questions.py`, `grouping.py`, `schema.py`, `keyframes.py`, `quant.py`, `prompts/*.yaml`, `backends/{mlx,transformers,replay,null}.py`; воркер `workers/vlm_worker.py`. Владелец S6. Конфиг `configs/vlm.yaml`.

### 13.1 Модель и память

- Qwen3.5-4B из mlx-community: 8-bit rev `1704583a` (5,16 ГБ), запасные 6-bit rev `fbb1092a` (4,11 ГБ) и 4-bit rev `0e7ffd5c` (3,06 ГБ). Загрузка один раз (`lazy=False`) из `models/hf/<repo>` при `HF_HUB_OFFLINE=1`.
- `CG_VLM_QUANT=auto`: после загрузки 8-bit и прогрева максимального размера, если psutil available < 3,0 ГБ — перезагрузка в 6-bit; если всё ещё мало — 4-bit. Выбранная квантизация — в `/ready` и в таблице `workers`.
- `mx.set_cache_limit(1 ГБ)`, `mx.set_memory_limit(7 ГБ)`, `mx.clear_cache()` после каждой задачи.
- Модель вызывается только из главного потока воркера: mlx-vlm держит поток генерации thread-local.
- Шлюз памяти при захвате задачи: свободно ≥ `CG_MIN_FREE_MEM_GB` (3,0 ГБ; формула — [architecture.md](architecture.md), раздел 8); `mx.get_active_memory()` пишется в лог (psutil занижает wired-память MLX). Не прошло → задача финализируется по правилам с `vlm_memory`.
- Квант для окна жюри фиксируется по `just perf` в SERVE: 8-bit — только если на пике свободно ≥ 3 ГБ, иначе `CG_VLM_QUANT=6bit`.

### 13.2 Группировка

`grouping.py`:
- Кандидаты (прошедшие τ и близкие к нему) с якорями ближе ±2 с объединяются в одну группу и один вызов с несколькими вопросами. Пример: уход кассира проверяет экран, ключ и ценности одним вызовом.
- Группы упорядочены по влиянию на вердикт: |score − τ| по возрастанию — сначала те, что могут перевернуть вердикт.
- Не больше `grouping.max_groups` (4 по бюджету) групп.

### 13.3 Вопросы и кадры

`questions.py` → `VlmQuestion{group_id, candidate_ids, questions: [{qid, code, text_ru}], frames: list[FrameRef], max_tokens}`:
- Текст вопроса — `vlm_question_ru` из каталога, подстановка времени и предмета. Вопрос формулируется так, что `yes` = наблюдаемый факт нарушения есть.
- Кадры: 4–8 сохранённых кадров равномерно в окне группы, масштаб ~0,75 (≈ 230 визуальных токенов вместо ≈ 405 на полный кадр 464×848, оценка).
- Плюс одна мозаика из фрагментов `zoom` всех кандидатов группы. Мелкие фрагменты по отдельности увеличились бы до минимума 65 536 пикселей и потратили бы токены.
- Метки времени кадров пишутся в тексте запроса: «Кадр 1 — 12,3 с, …».
- Системный промпт и шаблоны — `pipeline/vlm/prompts/*.yaml` (по группам каталога и отдельный для keyframe pass).

### 13.4 Ответ по схеме

`schema.py` строит JSON-схемы один раз при старте; llguidance ограничивает генерацию схемой:

```json
{
  "type": "object", "additionalProperties": false, "required": ["answers"],
  "properties": {
    "answers": {"type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["qid", "a", "c"],
      "properties": {
        "qid": {"type": "string"},
        "a":   {"enum": ["yes", "no", "unclear"]},
        "c":   {"type": "number", "minimum": 0, "maximum": 1}}}},
    "note": {"type": "string", "maxLength": 200}
  }
}
```

- `stream_generate`: `enable_thinking=False`, `temperature=0`, `max_tokens=96` (verify) или 160 (keyframe pass). Ответ короткий (~15–40 токенов, оценка), основное время — prefill.
- `VlmAnswer{group_id, answers, note, tokens_in, tokens_out, latency_ms, model_rev}`. Ответ без нужного `qid` → `unclear`.

### 13.5 Сроки внутри VLM

- Перед группой: `now + est_group ≤ vlm_stop`, иначе оставшиеся группы — `unverified`, `result_level=partial`.
- На каждом потоковом фрагменте: `now > vlm_stop` → прервать генерацию; неполный ответ → `unverified`.
- Другого способа прервать генерацию MLX нет; зависание ловит супервизор (`progress_at` старше 60 с у занятого воркера → SIGKILL), задача финализируется по правилам с `vlm_crash`.

### 13.6 Keyframe pass (общий проход)

- Если после групп осталось время: 12–16 кадров в половинном разрешении в моменты входа, выхода и смены состояний.
- Схема ответа: `findings: [{t_sec, code, c, summary_ru}]`, где `code` — код каталога или `NONE`.
- `KeyframeFinding{t_sec, suggested_code, confidence, summary_ru}`.
- По умолчанию находки — **только сигналы** в UI («Наблюдения»). Находка с известным кодом не создаёт нарушение этого кода: тип выдают правила.
- `OTHER_VISIBLE_IRREGULARITY` влияет на вердикт только при подтверждении треками или состояниями сцены в окне ±2 с и уверенности ≥ `keyframes.tau_high`. Политику утверждает grill-me (открытый вопрос 5). `summary_ru` в описание жюри не идёт.

### 13.7 После VLM

`decide()` повторно с `VlmReview{answers, findings, skipped_reason}` → evidence и describe только для изменённых и новых находок → рендер и проверка `jury.json` и `ui_result.json` → финализация (раздел 14). `result_level`: `full` — все запланированные группы и keyframe pass выполнены; `partial` — часть пропущена.

### 13.8 Replay и null

- `replay.py` возвращает записанные ответы по ключу (`sha16`, `models_hash`, подпись группы = отсортированные коды + округлённый якорь). Нет записи → `unclear`.
- `null.py` → `VlmReview(skipped_reason="vlm_disabled")`.

---

## 14. Финализация и очистка

Финализация — один идемпотентный CAS, вызываемый из трёх мест:

| Кто | Когда |
|---|---|
| VLM-воркер (или perception, если VLM не нужен) | Успех |
| Sweeper в API (каждые 2 с) | Просрочен `finalize_at`, есть предварительный результат → `done`, `result_level=rules`, `deadline_finalize` |
| `GET /api/v1/jobs/{id}` | То же, лениво, если sweeper умер |

```sql
UPDATE jobs SET status='done', stage='final', result_level=?, verdict=?, n_violations=?, finished_at=?
WHERE id=? AND status!='done' AND (lease_owner=? OR ?)
```

`rowcount == 1` — этот вызов победил.

После финализации:
- связанные задачи (`linked_to`) материализуются;
- `result_cache` получает строку только при `result_level=full`;
- `frames/` удаляется, `evidence/` остаётся; `mx.clear_cache()`;
- через `CG_RETENTION_HOURS` (24 ч) вся папка задачи удаляется (hourly retention); `0` — очистка выключена (окно жюри).

Повтор и сбои (sweeper):
- аренда мертва (`psutil.pid_exists` = False или `alive_at` старше 20 с) → requeue, `attempts + 1`;
- perception: 2 попытки, вторая на уровне `fast`;
- VLM: 1 попытка, затем финализация предварительного результата;
- попытки исчерпаны: `done` с предварительным результатом, если он есть, иначе `failed` с `PIPELINE_CRASH`.

---

## 15. Деградация

### 15.1 Уровни

| Измерение | Значения | Смысл |
|---|---|---|
| `perception_level` | `full` → `fast` → `min` | Плотность восприятия (раздел 4.3) |
| `result_level` | `full`, `partial`, `rules` | Сколько проверки VLM вошло в итог |

### 15.2 Причины

`Degradation{stage, reason}`. Тексты `reason_ru` — предложение для UI (S1, S2 правят):

| `reason` | Триггер | Что получает жюри | `reason_ru` (предложение) |
|---|---|---|---|
| `perception_fast` | Попытка 2 или прогноз не укладывается в `vision_target` | Тот же формат, ниже плотность | «Ускоренный анализ: 1,5 кадра/с» |
| `perception_min` | Не укладывается и `fast`; задача взята из очереди позже t0 + 200 с | То же, без позы | «Минимальный анализ: 1 кадр/с, без позы» |
| `perception_truncated` | Наступил `perception_hard_stop` (t0 + 235 с) до конца восприятия | Результат по правилам по обработанным кадрам | «Анализ остановлен по сроку: проверена часть видео» |
| `vlm_deadline` | Не прошёл `vlm_start_cutoff` или прервано на `vlm_stop` | Результат по правилам или частично проверенный | «Проверка VLM не успела к сроку» |
| `vlm_memory` | Свободной памяти < 3,0 ГБ (`CG_MIN_FREE_MEM_GB`) | Результат по правилам | «Проверка VLM пропущена: мало памяти» |
| `vlm_crash` | VLM-воркер упал или завис | Результат по правилам | «Проверка VLM прервана сбоем» |
| `vlm_backlog` | Очередь VLM не успевает | Результат по правилам | «Проверка VLM пропущена из-за очереди» |
| `vlm_disabled` | `CG_VLM_ENABLED=false` или бэкенд `null` | Результат по правилам | «Проверка VLM отключена» |
| `deadline_finalize` | Sweeper или GET финализировал по `finalize_at` | Последний записанный результат | «Результат зафиксирован по сроку» |

### 15.3 Лестница при пачке роликов

5 роликов по 145 с подряд: восприятие ~25 с на ролик (готово к ~25, 50, 75, 100, 125 с после загрузки), VLM отстаёт на ~30 с (готово к ~55, 85, 115, 145, 175 с). Последний — к ~175–190 с. Если оценки не сойдутся, задачи 4–5 пропустят VLM из-за `vlm_start_cutoff` (180 с). Все цифры — оценка до `just perf` и `tests/mac/test_overlap_throughput.py`.

`failed` возможен только при `UNSUPPORTED_VIDEO` и при `PIPELINE_CRASH` без предварительного результата.

---

## 16. Сроки и бюджеты по этапам

### 16.1 Сроки от t0 (`configs/deadlines.yaml`)

| Срок | Время |
|---|---|
| `vision_target` | t0 + 150 с |
| `vlm_start_cutoff` | t0 + 180 с |
| `vlm_stop` | t0 + 210 с |
| `finalize_at` | t0 + 225 с |
| `perception_hard_stop` | t0 + 235 с |
| Ожидаемый худший случай | ≤ t0 + 240 с |
| Аварийный предел (ни одной задачи в `processing`) | ≤ t0 + 250 с |
| Лимит жюри | 300 с (FAQ 14) |

Задача, взятая из очереди позже t0 + 200 с (`min_level_after`, имя — предложение S1), сразу идёт на уровне `min` с тем же рубежом остановки.

### 16.2 Бюджет по этапам (`configs/perf_budget.yaml`)

Все значения — оценка. `scripts/bench_mac.py` заменяет их замерами: предварительно в Ч+3,75 – 4,75, окончательно в SERVE Ч+15 – 16 ([roadmap.md](roadmap.md), раздел 6.2). `just perf` [macos] работает только в режиме SERVE и падает, если p95 этапа выше бюджета больше чем на 20 %.

| Этап | Х-14 (≈ 142 с; бюджет на 145 с) | Типичный (20 с) | Примечание |
|---|---|---|---|
| Запись, sha256, fsync | ≤ 0,5 с | ≤ 0,5 с | Без времени передачи через туннель |
| Лёгкая проба (API) | ≤ 0,05 с | ≤ 0,05 с | |
| Remux превью | ≤ 0,3 с | ≤ 0,3 с | Только UI, без перекодирования |
| Очередь → захват | ≤ 0,25 с | ≤ 0,25 с | Опрос 250 мс |
| Глубокая проба | ≤ 0,2 с | ≤ 0,2 с | |
| Декодирование 3 кадра/с | ≤ 6 с | ≤ 1 с | Декодируются все кадры, в BGR — ~1 из 10 |
| Детектор + трекинг | ≤ 15 с | ≤ 3 с | Core ML, ANE, batch 1, 864×480 (480×864 для альбомных) |
| Поза | ≤ 8 с | ≤ 1,5 с | С гейтом |
| Классификаторы | ≤ 2 с | ≤ 0,5 с | С гейтом, один вызов на кадр |
| Хранилище кадров | ~1 с, параллельно | | Поток JPEG |
| Сцена + правила + вердикт | ≤ 1 с | ≤ 0,2 с | ~435 наблюдений на 145 с |
| Уточнение (P3) | ≤ 1 с | ≤ 0,5 с | ≤ 5 кандидатов |
| Evidence + describe + render + проверка | ≤ 1,5 с | ≤ 0,5 с | |
| **Предварительный итог** | **≤ 28 с** | **≤ 7 с** | |
| VLM | ≤ 30 с (макс. 35) | ≤ 20 с | ≤ 4 группы × 3–5 с (≤ 3 тыс. токенов prompt, ≤ 96 на выход) + keyframe pass 6–10 с |
| Итоговый рендер | ≤ 0,5 с | ≤ 0,5 с | |
| **Итог** | **≤ 60–65 с** | **≤ 30 с** | |
| Только правила (деградация) | ≤ 25 с | | |

Замер: каждая задача пишет `time.perf_counter` по этапам в `jobs.timings_json` (ключи: `upload`, `probe`, `decode`, `detect_track`, `pose`, `cls`, `scene`, `rules`, `verdict`, `evidence`, `describe`, `render`, `vlm_groups`, `vlm_keyframes`, `final_render` — предложение, S1 фиксирует), пиковый RSS и пик MLX. Эти данные обновляют `perf_stats` (EWMA) и видны в UI и `/api/v1/admin/status`.

---

## 17. Конфигурация: файлы и ключи

Все YAML читаются `yaml.safe_load` и проверяются Pydantic-моделями при старте (Catalog, Acts, RuleParams[CODE], SceneConfig, PerceptionConfig, VlmConfig, Deadlines, PerfBudget, ModelsRegistry). Ошибка → процесс не стартует. `config_hash` = sha256 нормализованного YAML; входит в ключ кэша результатов. Значения «подобрать» — LOSO или замер; до подбора они `null`, и зависящая логика выключена или использует оценку из `perf_budget.yaml`.

### 17.1 `configs/perception.yaml` (S3)

| Ключ | Значение | Источник |
|---|---|---|
| `levels.full` | `{sample_fps: 3, pose_fps: 2, pose_policy: zones, cls_fps: 1.5, cls_triggers: true}` | синтез |
| `levels.fast` | `{sample_fps: 1.5, pose_policy: candidate_windows, cls_fps: 0, cls_triggers: true}` | синтез |
| `levels.min` | `{sample_fps: 1, pose_policy: off, cls_triggers: true}` | синтез; cls — проверить |
| `long_video` | `{threshold_sec: 300, max_sampled_frames: 600}` | синтез |
| `decode` | `{thread_type: AUTO, queue_size: 16, hwaccel: none, frame_store_jpeg_quality: 85}` | синтез; `hwaccel` — по замеру |
| `imgsz_variants` | `{portrait: "864x480", landscape: "480x864"}` | синтез |
| `detector` | `{conf_min: 0.1, max_failed_frames: null}` | `conf_min` = `track_low_thresh` (предложение, раздел 6.2); остальное подобрать |
| `pose` | `{zones: [desk, cashier_side, client_side, safe], trigger_hold_sec: null, match_iou: null, kpt_conf_min: null}` | подобрать |
| `cls` | `{imgsz: 224, trigger_hold_frames: null, tasks: {screen: ..., safe_lock: ..., door: ...}}` | геометрия `tasks` и признак `letterbox` — только из `crop_spec.json` (раздел 6.5), не задаются здесь |
| `quality` | `{enabled: true, downscale_px: null, brightness_jump: null}` | подобрать |
| `progress_hz` | 2 | синтез |
| `refine` | `{enabled: false, window_sec: 1, fps: 10, max_candidates: 5}` | синтез (P3) |

### 17.2 `configs/trackers/botsort_static.yaml` (S3)

Раздел 6.3.

### 17.3 `configs/scene.yaml` (S4)

| Ключ | Значение |
|---|---|
| `calibration` | `{window_sec, min_frames, static_classes: [safe_closed, safe_open, laptop, door, counting_machine, auth_detector], recalib_iou}` |
| `zones` | `{desk_from: [laptop, counting_machine, auth_detector], desk_margin, safe_margin, door_margin, device_margin}` |
| `class_conf` | Порог уверенности по каждому из 14 классов для состояний и событий |
| `roles` | `{cashier_min_dwell_frac, client_grace_sec, collector_link_sec, outsider_grace_sec}` |
| `tracks` | `{stitch_gap_sec, stitch_dist}` |
| `exit` | `{require_door_or_edge: true, edge_margin_frac, lost_track_is_unknown: true}` |
| `states.<subject>` | `{min_on_sec, min_off_sec, conf_min}` для каждого `StateSubject` |
| `events` | `{handoff_wrist_dist, cup_mouth_dist, phone_wrist_dist, zone_enter_frac}` |

Все численные значения — подобрать (LOSO).

### 17.4 `configs/rules/<CODE>.yaml` (владелец кода)

Раздел 8.5. Конкретные `params` каждого кода — в [violation_catalog.md](violation_catalog.md). Для `OTHER_VISIBLE_IRREGULARITY` здесь же `tau_high`.

### 17.5 `configs/catalog/<CODE>.yaml` (S0 v0, затем владелец кода)

```yaml
code: PC_UNLOCKED
title_ru: "Уход с рабочего места без блокировки компьютера"   # строка type для жюри, стабильная
group: leave            # leave | access | cashops | collection | discipline | other
verdict_impact: true
timecode_policy: exit   # event | exit | state_start | omission_due
template_ru: "В {t_exit} с кассир покидает рабочее место. Экран ноутбука остаётся включённым и не заблокированным ещё {active_sec} с после ухода."
required_facts: [t_exit, active_sec]
vlm_question_ru: "На кадрах после ухода сотрудника экран ноутбука показывает рабочее окно, а не экран блокировки и не выключен?"
legal:                  # первая запись — основная, её пункт идёт в описание жюри
  - act_code: TIB
    act_title_ru: "Требования к обеспечению информационной безопасности банков, филиалов банков-нерезидентов Республики Казахстан и организаций, осуществляющих отдельные виды банковских операций"
    clause: "п. 73 пп. 6)"
    quote_ru: "недопущение работы в информационных системах под чужими учетными записями"
    url: "https://adilet.zan.kz/rus/docs/V2600038336"
    strength: indirect
  - act_code: TIB        # только интерфейс: автоблокировка через 30 мин — требование к системе
    act_title_ru: "Требования к обеспечению информационной безопасности банков, филиалов банков-нерезидентов Республики Казахстан и организаций, осуществляющих отдельные виды банковских операций"
    clause: "п. 71 пп. 9)"
    quote_ru: "при неактивности пользователя более 30 (тридцати) минут информационная система автоматически завершает сеанс работы пользователя либо блокирует рабочую станцию или ноутбук"
    url: "https://adilet.zan.kz/rus/docs/V2600038336"
    strength: indirect
owner: S4
```

Поля `legal` — как `LegalRef` ([architecture.md](architecture.md), раздел 5.7): при `extra='forbid'` без `act_title_ru` и `url` запись не пройдёт проверку. Значения для всех кодов — [violation_catalog.md](violation_catalog.md), нормы и сила — [legal_sources.md](legal_sources.md), раздел 5. `docs/violation_types.md` генерируется из этих файлов (`scripts/render_docs.py`).

### 17.6 `configs/catalog/signals.yaml` (S0, затем S6)

Список `{code, title_ru, template_ru, required_facts, pdf_items}` (модель `SignalCatalog{signals: list[SignalEntry]}`) для 13 кодов `SIG_*`; формулировки «не наблюдается…». Поля `legal` нет: в v1 у сигналов норм нет (`UiSignal` без `legal`; [legal_sources.md](legal_sources.md), раздел 4).

### 17.7 `configs/legal/acts.yaml` (S0, затем S5)

Корневая модель `ActsFile{acts: list[ActEntry], do_not_cite: list[DoNotCite{title_ru, reason_ru}]}` (`contracts/catalog.py`); `status` — перечисление `in_force | repealed`. На акт (`ActEntry`): `act_code` (`PKO`, `POP`, `POO`, `TIB`), `title_ru`, `cite_ru` (краткая форма для `{act_cite}`), `issuer`, `number`, `date`, `registration_no`, `adilet_id`, `url`, `status`. `do_not_cite` — утратившие силу акты (НБРК № 58, № 250, № 48, № 254). Пример — [legal_sources.md](legal_sources.md), раздел 2.

### 17.8 `configs/vlm.yaml` (S6)

| Ключ | Значение | Источник |
|---|---|---|
| `model` | `{quant: auto}` | репозитории, ревизии (`1704583a`, `fbb1092a`, `0e7ffd5c`) и sha256 хранятся только в `configs/models.yaml` → `vlm.variants` ([training.md](training.md), раздел 15.3) |
| `memory` | `{cache_limit_gb: 1, memory_limit_gb: 7, min_free_after_warmup_gb: 3.0, gate_free_gb: 3.0}` | синтез; `gate_free_gb` = `CG_MIN_FREE_MEM_GB` ([architecture.md](architecture.md), раздел 8) |
| `generation` | `{enable_thinking: false, temperature: 0, max_tokens_verify: 96, max_tokens_keyframe: 160}` | синтез |
| `grouping` | `{anchor_window_sec: 2, max_groups: 4, order: impact}` | синтез |
| `frames` | `{per_group_min: 4, per_group_max: 8, scale: 0.75, zoom_mosaic: true}` | синтез |
| `keyframes` | `{enabled: true, n_min: 12, n_max: 16, scale: 0.5, policy: signal_only, tau_high: null}` | синтез; `policy` — открытый вопрос 5 |
| `scoring` | `{no_conf_min: null, down_factor: null}` | подобрать на eval-full |
| `handoff_margin_sec` | 15 | синтез |
| `estimates` | `{group_call_sec: 3–5, keyframe_pass_sec: 6–10}` | оценка, заменяет EWMA |

### 17.9 `configs/deadlines.yaml` (S1)

`vision_target_sec: 150`, `vlm_start_cutoff_sec: 180`, `vlm_stop_sec: 210`, `finalize_at_sec: 225`, `perception_hard_stop_sec: 235`, `min_level_after_sec: 200` (имя — предложение S1), `emergency_done_sec: 250`, `expected_done_sec: 240`, `jury_limit_sec: 300`, `sweeper_tick_sec: 2`, `alive_stale_sec: 20`, `progress_stale_busy_sec: 60`, `progress_stale_warming_sec: 300`, `attempts: {perception: 2, vlm: 1}`, `retention_hours: 24` (`0` — очистка выключена, окно жюри).

### 17.10 `configs/perf_budget.yaml` (S1)

Бюджеты раздела 16.2 по классам роликов (`x14_145s`, `typical_20s`), `tolerance_pct: 20`, стартовые EWMA для холодного старта.

### 17.11 `configs/models.yaml` (S0, правит S3)

На роль (`detector`, `pose`, `cls_screen`, `cls_safe_lock`, `cls_door`, `vlm`): `version`, артефакты по вариантам `{path, sha256, imgsz}`, бэкенд по платформе с ключами как в `[tool.uv] environments` (`darwin-arm64: coreml`, `linux-x86_64: torch`, `win32-AMD64: torch | replay`), для cls — геометрия фрагмента; для VLM — `vlm.variants`: репозиторий, ревизия и sha256 по квантизации (единственное место ревизий VLM). Пример — [training.md](training.md), раздел 15.3. Продвижение модели = правка этого файла и перезапуск воркеров.

### 17.12 Переменные окружения `CG_*`

| Переменная | Значения |
|---|---|
| `CG_PIPELINE_MODE` | `stub`, `replay`, `full` |
| `CG_VISION_BACKEND` | `coreml`, `torch`, `replay`, `stub`, `null` |
| `CG_VLM_BACKEND` | `mlx`, `transformers`, `replay`, `null` |
| `CG_VLM_ENABLED` | bool |
| `CG_VLM_QUANT` | `auto`, `8bit`, `6bit`, `4bit` |
| `CG_MAX_UPLOAD_MB` | 100 |
| `CG_MIN_FREE_MEM_GB` | 3.0 |
| `CG_MIN_FREE_DISK_GB` | 2 |
| `CG_DATA_DIR`, `CG_MODELS_DIR`, `CG_WEB_DIST` | `var`, `models`, `web/dist` |
| `CG_RETENTION_HOURS` | 24; `0` — очистка выключена (окно жюри) |

Ключи API, лимиты UI и прочее — [api.md](api.md) и [architecture.md](architecture.md).

---

## 18. Тесты по этапам

| Этап | Тесты | Где | Владелец |
|---|---|---|---|
| Приём | Ключ до тела (итератор тела не тронут при 401), 401/404/413/415/422/429/507, слэш на конце, дедупликация, 5 параллельных загрузок | `tests/api/` | S1 |
| Декодирование | VFR-фикстура: таймкоды = PTS; повёрнутый ролик: поворот применён; HEVC: needs_transcode | `tests/unit/`, `tests/fixtures/synthetic_video.py` | S3 |
| Восприятие | Трекер передан явно; паритет Core ML и `.pt` ≤ 1 mAP50; размещение на ANE; ≥ 3 ГБ свободно на 145 с | `tests/mac/` (marker `mac`) | S3 |
| Сцена | Зоны на синтетических трассах, в том числе отражённых и повёрнутых; потеря трека ≠ уход | `tests/unit/scene/` | S4 |
| Правила | `pos` и `neg` фикстуры на каждый код | `tests/unit/rules/<CODE>_test.py` | владелец кода |
| Вердикт | Слияние 10 с, подавление, ≤ 5, VLM только понижает | `tests/unit/verdict/` | S5 |
| Описание | Снимки syrupy по каждому шаблону; цитаты дословно в `analysis/normative` | `tests/unit/legal/`, `tests/contract/` | S5, S0 |
| Evidence | Инвариант `timestamp_sec`, имена файлов, JPEG `/9j/` | `tests/unit/evidence/` | S3 |
| Рендер | Замороженная схема, запасной рендер на испорченных входах | `tests/unit/render/`, `tests/contract/` | S1 |
| VLM | Схема ответа, прерывание по сроку, replay | `tests/unit/vlm/`, `tests/mac/` | S6 |
| Целиком | `kill -9` воркера посреди задачи → всё равно `done`; падение VLM → уходит предварительный результат; perception искусственно замедлена → `done` ≤ 250 с, `perception_truncated` | `tests/workers/` | S1 |
| Качество | `just eval-replay` против `eval/baselines/main.json`; LOSO recall 1.0 | [eval.md](eval.md) | S1 |

---

## 19. Открытые вопросы и «проверить»

1. **Политика keyframe pass** (открытый вопрос 5): только сигналы или `OTHER_VISIBLE_IRREGULARITY` с высоким τ. По умолчанию — подтверждённые треками и выше `tau_high`.
2. **Экспорт моделей.** Закрыт: `training/` не меняется, выпуск — экспортёр сервиса `pipeline/perception/export.py` (раздел 6.8).
3. **Геометрия фрагментов классификаторов.** Закрыт: `crops.py` повторяет `crop_spec.json`, letterbox — только при `letterbox: true` (раздел 6.5). Аугментации — аддитивный PR к `train.yaml` до этапа D (P1-S3-05).
4. **Классификаторы на уровне `min`**: частота не задана синтезом — проверить замером.
5. **Порядок перекодирования превью** для HEVC и повёрнутых UI-роликов — решить по замеру.
6. **Номера файлов evidence для сигналов** (10–29) — подтверждает S1 вместе с регулярным выражением маршрута.
7. **Последний рубеж рендера** (серый JPEG при повторной ошибке схемы) — решение S1.
8. **Поле `data` у `StateInterval`** для принадлежности денег сеансу — если понадобится, отдельный PR в `contracts/` (S0).
9. **Обязательные ключи botsort.yaml** пинованной версии ultralytics (`proximity_thresh`, `appearance_thresh` и др.) — проверить по исходникам версии из `uv.lock`.
10. **Все числа скорости и памяти** — оценка до `scripts/bench_mac.py` и `just perf`.
