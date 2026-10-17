# Архитектура CashGuard

> Назначение: единое описание архитектуры сервиса CashGuard: процессы, поток данных, контракты, стек, бюджеты памяти и времени, надёжность. По этому документу агенты реализуют продукт.
> Владелец: S0 (интегратор). Статус: черновик v1, 17.10.2026.
> Источник истины — синтез архитектуры от 17.10.2026. Решения с обоснованием — в [adr/](adr/README.md). При расхождении документа и ADR прав ADR.

---

## Содержание

1. [Что делает система](#1-что-делает-система)
2. [Обзор системы](#2-обзор-системы)
3. [Процессная модель](#3-процессная-модель)
4. [Поток данных](#4-поток-данных)
5. [Внутренние контракты](#5-внутренние-контракты)
6. [API (кратко)](#6-api-кратко)
7. [Стек](#7-стек)
8. [Бюджет памяти Mac](#8-бюджет-памяти-mac)
9. [Бюджет времени и деградация](#9-бюджет-времени-и-деградация)
10. [Надёжность](#10-надёжность)
11. [Безопасность и гигиена данных](#11-безопасность-и-гигиена-данных)
12. [Локальность и перенос на Linux](#12-локальность-и-перенос-на-linux)
13. [Что сознательно не используем](#13-что-сознательно-не-используем)
14. [Структура репозитория](#14-структура-репозитория)
15. [Расхождения с текущим кодом training/](#15-расхождения-с-текущим-кодом-training)
16. [Связь с критериями оценки ТЗ](#16-связь-с-критериями-оценки-тз)
17. [Риски и открытые вопросы](#17-риски-и-открытые-вопросы)
18. [Допущения](#18-допущения)
19. [Связанные документы](#19-связанные-документы)

---

## 1. Что делает система

CashGuard — локальный AI-сервис. Он принимает видео кассы (.mov, около 29 МБ, ракурс сверху) и возвращает:
- вердикт `violation` или `no_violation` на всё видео;
- для каждого нарушения: тип, таймкод, описание, 1–3 кадра-доказательства и ссылку на норму РК.

Два входа:
- **API жюри** `/api/v1` — ровно по разделу 10 ТЗ, ключ в `X-API-Key`, автоматическая проверка.
- **Веб-интерфейс** — загрузка видео, статус, результат с плеером, кадрами и нормами.

Жёсткие рамки из ТЗ и FAQ:

| Рамка | Источник | Что это значит для архитектуры |
|---|---|---|
| Ответ за 5 минут, иначе видео не засчитано | ТЗ §10, FAQ 14 | Дедлайны от прихода запроса, промежуточный результат всегда готов заранее ([ADR-0005](adr/0005-deadline-tiers-provisional-degradation.md)) |
| Автоматическая проверка по единой JSON-схеме | FAQ 18 | Замороженная схема ответа, ни одного лишнего поля ([ADR-0006](adr/0006-frozen-jury-contract.md)) |
| Пропуск нарушения недопустим, ложных срабатываний — минимум | FAQ 16 | Порог правил под recall 1.0, VLM может только понизить балл ([ADR-0010](adr/0010-qwen-vlm-bounded-verifier.md)) |
| Всё локально, без облачных AI | ТЗ §7, FAQ 21, 35 | Все модели на Mac, офлайн-окружение, проверка холодного старта с выключенной сетью. FAQ 35 (дисквалификация за облачные решения по API и готовые решения): облачных API и готовых решений нет; только open-source модели и библиотеки с лицензией, разрешающей коммерческое использование (FAQ 24; оговорка AGPL — раздел 11) |
| Перенос во внутренний контур банка | ТЗ §7 | Порты и бэкенды, Docker-образ для Linux + CUDA ([ADR-0012](adr/0012-contracts-first-ports-replay.md)) |
| Финальное видео снято под другим углом | FAQ 4 | Зоны по объектам, а не по пикселям; экспорт в двух ориентациях ([ADR-0009](adr/0009-perception-neural-engine.md)) |
| Лица не распознавать | FAQ 29 | Трекер без ReID по внешности, роли по зонам и поведению |
| Публичный URL обязателен | FAQ 23 | Именованный Cloudflare Tunnel + резерв Tailscale Funnel ([ADR-0013](adr/0013-public-ingress-tunnel.md)) |
| Нормы только РК, без выдуманных пунктов | ТЗ §6, FAQ 30 | Каталог норм из проверенного раздела 7 [case_analysis.md](../analysis/case_analysis.md), цитаты сверяются тестом |
| Нельзя утверждать то, что не видно на видео | ТЗ §7 | Описание по шаблону из фактов, а не свободной генерацией; невидимое — только сигналы в UI |

Производственный хост — MacBook Pro M2 Pro, 16 ГБ. Он же машина обучения, но не одновременно. Разработка идёт на Windows-ПК агентами (Claude Code и Codex) в параллельных worktree.

---

## 2. Обзор системы

```
   Жюри: curl, X-API-Key                        Проверяющий: браузер
          │ HTTPS                                      │ HTTPS
          ▼                                            ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Cloudflare edge: именованный туннель, лимит тела 100 МБ,             │
 │ таймаут origin 100 с, /api не кэшируется, Bot Fight Mode выключен    │
 └──────────────────────────────┬───────────────────────────────────────┘
                                │ http2            резерв: Tailscale Funnel
 ═══ MacBook Pro M2 Pro, 16 ГБ ═╪═════════════════ (тот же origin, второй URL)
                                ▼
  launchd (gui/<uid>)     cloudflared ──────► http://127.0.0.1:8000
   ├ kz.cashguard.tunnel ───┘
   ├ kz.cashguard.caffeinate    (/usr/bin/caffeinate -ims)
   └ kz.cashguard.supervisor ─► python -m cashguard.cli up --profile prod
                                  │ spawn свежих интерпретаторов (без fork),
                                  │ рестарт с backoff, SIGKILL зависших
        ┌─────────────────────────┼──────────────────────────────┐
        ▼                         ▼                              ▼
 ┌────────────────────┐ ┌───────────────────────────┐ ┌───────────────────────────┐
 │ api       [CPU]    │ │ perception worker         │ │ vlm worker        [GPU]   │
 │ uvicorn, 1 worker  │ │ [ANE] Core ML, CPU_AND_NE │ │ MLX: Qwen3.5-4B 8-bit     │
 │ FastAPI, без ML    │ │  YOLO26l det 864x480      │ │  (авто 6/4-bit)           │
 │ /api/v1  /api/ui   │ │  YOLO26l-pose 864x480     │ │ llguidance JSON-схема     │
 │ /health  /ready    │ │  YOLO26s-cls x3 (224)     │ │ проверка групп            │
 │ sweeper каждые 2 с │ │ [CPU] PyAV decode,        │ │ кандидатов, keyframe pass │
 │ web/dist (SPA)     │ │  BoT-SORT, scene, rules,  │ │ decide(), финальный       │
 │ PyAV: probe, remux │ │  verdict, evidence, render│ │ render                    │
 └───┬──────────┬─────┘ └────────┬──────────────────┘ └───────────┬───────────────┘
     │ SQL      │ файлы          │ claim / lease / CAS            │ claim / lease / CAS
     ▼          ▼                ▼                                ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ var/db/cashguard.sqlite3 (WAL): jobs, job_events, workers, result_cache,     │
 │ perf_stats. Единственная очередь и единственное состояние                    │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ var/jobs/<id>/: input.mov, preview.mp4, meta.json, frames/, trace.json.gz,   │
 │ candidates.json, analysis.json, evidence/, jury.json, ui_result.json         │
 └──────────────────────────────────────────────────────────────────────────────┘
 models/ (только чтение, sha256 сверяется с configs/models.yaml при старте):
   detector|pose|cls_screen|cls_safe_lock|cls_door/vNNN/*.mlpackage
   hf/Qwen3.5-4B-{8,6,4}bit/
```

Ключевые свойства:
- **Три вычислительных блока работают параллельно.** Neural Engine считает YOLO, GPU — Qwen, CPU — декодирование, трекинг и правила. Пока VLM проверяет задачу N, perception уже обрабатывает задачу N+1.
- **API не держит моделей.** Его перезапуск занимает меньше 2 с и не перезагружает Qwen.
- **Промежуточный результат по правилам пишется всегда** сразу после perception. VLM только уточняет его.
- **Связь процессов — только через SQLite и файлы задачи.** Нет брокера, нет сокетов между процессами.

---

## 3. Процессная модель

### 3.1. Хост и режимы

- Репозиторий на Mac: `~/cashguard`. Не `~/Desktop`, `~/Documents` или `~/Downloads`: TCC молча блокирует там launchd-задачи, а синхронизация iCloud выгрузит видео и испортит WAL SQLite.
- Окружение: нативный uv venv, без Docker (в контейнерах на macOS нет Metal и ANE). macOS ≥ 14, рекомендуется 15.
- Состояние: `~/cashguard/var`. Секреты: `~/.config/cashguard/.env` (chmod 600).
- Три режима взаимоисключающие. Переключение: `just mode serve|train|dev`. `mode train|dev` = `launchctl bootout` + `launchctl disable` агента supervisor, поэтому после перезагрузки сервис сам не стартует; `mode serve` = `launchctl enable` + `bootstrap` ([runbook_mac.md](runbook_mac.md), раздел 6).
- Обучение запускается только через `just train-a` / `just train-c` [macos]: обёртки отказываются стартовать, если `pgrep -f 'cashguard.cli up'` что-то находит, затем `caffeinate -i bash training/run_stage_{a,c}.sh`. Сами `run_stage_*.sh` не меняются.

| Режим | Что запущено | Когда |
|---|---|---|
| `serve` | supervisor → api, perception, vlm; cloudflared; caffeinate | Демонстрация и окно проверки жюри |
| `train` | supervisor выгружен. Обучение в `.venv-train` под `caffeinate -i`, по одной задаче. Label Studio в `.venv-ls` | Этапы A–D из [training/README.md](../training/README.md). Туннель может остаться и отдаёт 502, поэтому в окно жюри режим `train` запрещён |
| `dev` | тот же supervisor с `--profile dev` | Отладка на Windows-ПК и на Mac |

Режим `dev`:
- `uv run just dev` запускает supervisor с `--profile dev`.
- api с `--reload` на порту worktree: `scripts/port.py` хэширует путь в диапазон 8100–8199.
- Один worker с `--role all`. Переключатели: `CG_PIPELINE_MODE=stub|replay`, `CG_VISION_BACKEND=stub|replay|torch`, `CG_VLM_BACKEND=null|replay`.
- На Mac в dev допустимы `CG_PIPELINE_MODE=full`, `CG_VISION_BACKEND=coreml`, `CG_VLM_BACKEND=mlx` — только для Mac-части G2 и `eval-full`. Замеры скорости и памяти (`just perf`) в dev не делаются: один worker `--role all` не даёт перекрытия ANE и GPU и не повторяет RSS трёх процессов. `just perf` — только в serve ([eval.md](eval.md), раздел 8.1).
- Vite dev server на :5173 проксирует `/api` на этот порт, поэтому CORS не нужен.
- У каждого worktree свой `var/` (SQLite, загрузки, логи).

### 3.2. LaunchAgents (режим serve)

Пользовательские LaunchAgents в `gui/<uid>`. `just services-install` рендерит их из `deploy/macos/launchd/*.plist.tmpl`.

| Агент | Команда | Ключевые настройки |
|---|---|---|
| `kz.cashguard.supervisor` | `~/cashguard/.venv/bin/python -m cashguard.cli up --profile prod` | Абсолютные пути; никогда `uv run` (может синхронизировать или выйти в сеть при загрузке). RunAtLoad, KeepAlive, ProcessType=Interactive, ThrottleInterval 5, ExitTimeOut 30, SoftResourceLimits NumberOfFiles 8192. Логи падений: `~/Library/Logs/cashguard/supervisor.{out,err}.log` |
| `kz.cashguard.tunnel` | `cloudflared tunnel --config ~/.cloudflared/config.yml run` | Именованный туннель, protocol http2, ingress `http://127.0.0.1:8000` (не `localhost`), metrics `127.0.0.1:20241`, KeepAlive |
| `kz.cashguard.caffeinate` | `/usr/bin/caffeinate -ims` | KeepAlive. Нужны питание от сети и открытая крышка |

Окружение supervisor:
- `PATH=/opt/homebrew/bin:/usr/bin:/bin`
- `PYTHONUNBUFFERED=1`, `PYTHONUTF8=1`
- `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`
- `YOLO_OFFLINE=1`, `YOLO_AUTOINSTALL=false`, `YOLO_CONFIG_DIR=~/cashguard/var/yolo` (заранее заполнен; `yolo settings sync=False`)
- `CG_ENV_FILE=~/.config/cashguard/.env`

Резервный адрес: приложение Tailscale выполняет `tailscale funnel --bg 8000` и отдаёт тот же origin со второго URL.

### 3.3. Supervisor

Файл: `src/cashguard/workers/supervisor.py`. Около 150 строк на stdlib + psutil. Не импортирует веб- и ML-библиотеки. Один и тот же код работает на macOS, Windows и Linux.

- **Старт.** Применяет офлайн-окружение. Запускает детей как свежие интерпретаторы: `subprocess.Popen([sys.executable, '-m', ...])`. Никогда не делает fork: Metal и ObjC runtime не fork-safe. Каждая точка входа также вызывает `multiprocessing.set_start_method('spawn')`. Порядок: api, perception, vlm.
- **Рестарт.** Упавший ребёнок перезапускается с backoff 1, 2, 4 … 30 с.
- **Жёсткое убийство** — единственный способ остановить зависший вызов Metal или Core ML:
  - `alive_at` воркера старше 20 с (поток heartbeat мёртв или GIL захвачен);
  - `progress_at` старше 60 с при `busy` или старше 300 с при `warming`;
  - перед SIGKILL сверяется cmdline процесса по pid.
- **API.** Проба `http://127.0.0.1:8000/health` каждые 5 с, рестарт после 3 неудач подряд.
- **cloudflared.** Проба metrics `/ready` каждые 15 с. При сбое — запись в лог и локальное уведомление через osascript. Падение cloudflared перезапускает launchd.
- **Остановка.** Пересылает SIGTERM детям и ждёт 30 с.

### 3.4. Процессы и что они держат в памяти

| Процесс | Команда | Движок | RSS (оценка) | Что держит |
|---|---|---|---|---|
| api | `uvicorn cashguard.api.main:app --host 127.0.0.1 --port 8000 --workers 1 --timeout-keep-alive 100 --timeout-graceful-shutdown 30 --limit-concurrency 64 --proxy-headers --forwarded-allow-ips 127.0.0.1` | CPU | 0,15–0,25 ГБ | Settings; YAML каталога и норм (несколько КБ); thread-local соединения SQLite; per-IP token bucket (только UI); небольшой LRU `ui_result.json` по mtime; пул из 2 потоков для лёгкого probe и remux превью (только PyAV); asyncio-задача sweeper (тик 2 с) |
| perception | `python -m cashguard.workers --role perception` | ANE (Core ML, CPU_AND_NE) + CPU | 1,5–2,0 ГБ (замер в bench_mac) | Импорты torch и ultralytics; `det_864x480` (`det_480x864` загружается лениво), `pose_864x480`, `cls_screen`, `cls_safe_lock`, `cls_door`; состояние BoT-SORT, сбрасывается на каждой задаче |
| vlm | `python -m cashguard.workers --role vlm` | GPU (MLX) | ≈ 6,0–6,5 ГБ (8-bit), ≈ 5,0 (6-bit), ≈ 4,0 (4-bit) | Qwen3.5-4B, загружен один раз (`lazy=False`) из `models/hf/<repo>` на закреплённой ревизии; processor; заранее собранные llguidance-процессоры JSON-схем; Metal-ядра, скомпилированные одной пустой генерацией |
| cloudflared | см. 3.2 | CPU | ≈ 40–50 МБ | Соединения туннеля |
| caffeinate | см. 3.2 | — | ≈ 0 | — |

Правила для api:
- Импортирует `av`, но **никогда** `torch`, `ultralytics`, `coremltools`, `mlx`, `mlx_vlm`, `cv2`. Это проверяют контракт import-linter и тест.
- Рестарт быстрее 2 с, модели не перезагружаются.

Правила для perception worker:
- Потоки:
  - `T-decode`: PyAV, `thread_type='AUTO'`, сетка PTS, поворот; кладёт кадры в ограниченную очередь на 16 кадров (≈ 20 МБ).
  - main: инференс, затем scene, rules, evidence, render.
  - `T-store`: пишет JPEG q85 в хранилище кадров (cv2 отпускает GIL).
  - `T-heartbeat`: пишет `alive_at` каждые 2 с. Основной цикл пишет `progress_at` на каждом кадре или шаге.
- Прогрев: по одному пустому инференсу на модель до `state=idle`. Первая компиляция под ANE может занять десятки секунд; обновление macOS сбрасывает этот кэш.
- Одна задача за раз, `ORDER BY priority` (jury 0, ui 1), затем `received_at`.
- Самоперезапуск между задачами: при RSS > 3 ГБ или после 200 задач воркер дренируется и выходит с кодом 0, supervisor поднимает новый.

Правила для vlm worker:
- Модель используется **только из главного потока**: mlx-vlm держит thread-local stream.
- Память: `mx.set_cache_limit(1 ГБ)`, `mx.set_memory_limit(7 ГБ)`, `mx.clear_cache()` после каждой задачи.
- `CG_VLM_QUANT=auto`: после загрузки 8-bit и прогрева максимального размера, если psutil показывает меньше 3,0 ГБ свободной памяти, — перезагрузка в 6-bit; если всё ещё мало — 4-bit. Выбранная квантизация видна в `/ready` и в таблице `workers`. Это проверка в покое; квант для окна жюри фиксируется по замеру на пике (раздел 8).
- Одна задача за раз.

### 3.5. Почему три процесса

- ANE, GPU и CPU — разные движки. Perception задачи N+1 идёт одновременно с VLM-этапом задачи N.
- Metal OOM, abort или зависание MLX убивает только vlm worker. Промежуточный результат по правилам к этому моменту уже на диске.
- Перезапуск API никогда не перезагружает Qwen (10–30 с).

Подробнее: [ADR-0002](adr/0002-three-process-runtime-supervisor.md).

### 3.6. Linux / контур банка

- `deploy/linux/compose.yaml` поднимает три сервиса из одного образа: `api`, `perception` (`gpus: all`, `CG_VISION_BACKEND=torch` на CUDA), `vlm` (`CG_VLM_BACKEND=transformers`).
- Общий `var/` через локальный bind mount (WAL SQLite работает между контейнерами на локальной ФС одного хоста). `/models` монтируется только для чтения.
- Цикл рестартов supervisor заменяет restart policy Docker. Есть эквивалентные systemd-юниты в `deploy/linux/systemd/`.

---

## 4. Поток данных

### 4.1. Дедлайны

`t0` — момент старта обработчика загрузки: после проверки ключа, до первого байта тела. Все дедлайны отсчитываются от `t0`, поэтому время приёма тела входит в бюджет. Значения в `configs/deadlines.yaml`; до замеров bench_mac это оценки.

| Уровень | Время | Что происходит |
|---|---|---|
| `vision_target` | t0 + 150 с | Цель для perception; по ней выбирается уровень full / fast / min |
| `vlm_start_cutoff` | t0 + 180 с | Позже VLM-этап не начинается |
| `vlm_stop` | t0 + 210 с | VLM прерывается (проверка на каждом потоковом чанке) |
| `finalize_at` | t0 + 225 с | Sweeper или ленивый GET финализирует промежуточный результат |
| `perception_hard_stop` | t0 + 235 с | Perception прекращает декодирование и закрывает задачу по уже обработанным кадрам (`perception_truncated`, [ADR-0005](adr/0005-deadline-tiers-provisional-degradation.md), п. 5) |
| ожидаемый худший случай | ≤ t0 + 240 с | Цель для всех задач жюри |
| аварийный предел | ≤ t0 + 250 с | После него ни одна задача не остаётся в `processing` (кроме зависшего воркера, его убивает supervisor) |
| лимит жюри | 300 с | ТЗ §10, FAQ 14 |

### 4.2. Путь жюри: от загрузки до JSON

Здесь — сквозной поток и точки синхронизации процессов. Логика каждого этапа (параметры, алгоритмы, файлы) — в [pipeline.md](pipeline.md).

```
t0 ── POST /api/v1/analyze ─ ключ проверен ДО чтения тела
 │     поток тела → input.part (sha256, лимит 100 МБ, проверка диска, сигнатура ISO-BMFF)
 │     → fsync → os.replace(input.mov) → probe → INSERT jobs → COMMIT
 ├──► 200 {"job_id"}                                  задача долговечна
 │
 │   perception worker (опрос очереди каждые 250 мс)
 │     claim → plan(level) → deep probe
 │     → decode ‖ detect+track ‖ pose ‖ cls → frame store
 │     → scene → rules → verdict (rules-only) → evidence → describe → render
 ├──► промежуточный результат: analysis.json, jury.json, ui_result.json (валидны)
 │     handoff: stage=vlm, если успеваем до vlm_start_cutoff и хватает памяти
 │
 │   vlm worker
 │     claim → группы кандидатов (±2 с) → stream_generate (JSON-схема, ≤ 96 токенов)
 │     → keyframe pass (если остался бюджет) → decide() → evidence/describe для изменённых
 │     → render + валидация → CAS finalize
 ├──► done (result_level full | partial)
 │
 t0+225 с  finalize_at: sweeper или ленивый GET отдаёт промежуточный результат (rules)
 t0+235 с  perception_hard_stop: perception без результата останавливается → правила по обработанным кадрам → done
 t0+300 с  лимит жюри
```

Шаги подробно:

1. **Вход.** Клиент жюри → Cloudflare edge (лимит тела 100 МБ, таймаут origin 100 с, `/api` не кэшируется) → cloudflared (http2) → uvicorn на `127.0.0.1:8000`.
2. **`POST /api/v1/analyze`.** Тот же обработчик зарегистрирован на `/api/v1/analyze/` и скрыт из OpenAPI, поэтому слэш в конце не даёт 307.
   1. Зависимость роутера `require_api_key`: `APIKeyHeader('X-API-Key', auto_error=False)`; sha256 присланного ключа сравнивается через `hmac.compare_digest` со **всеми** настроенными ключами, без раннего выхода. Ошибка — 401 JSON до чтения тела: обработчик принимает только `request: Request`.
   2. `Content-Length` > `CG_MAX_UPLOAD_MB` (100) → 413. Свободно на диске < 2 ГБ → 507.
   3. Поток: `request.stream()` → `StreamingFormDataParser`. Поле `file` пишется в `var/jobs/{id}/input.part` с бегущим sha256 и счётчиком байт. Превышение лимита — 413 и удаление `.part`. Content-Type части любой (curl шлёт `application/octet-stream`), имя файла клиента игнорируется. Нет поля `file` → 422.
   4. Сигнатура: байты `[4:8]` должны быть одним из `ftyp`, `moov`, `mdat`, `wide`, `free`, `skip`, `pnot`, иначе 415.
   5. `fsync` в потоке, затем `os.replace` в `input.mov`.
   6. Лёгкий probe (PyAV, поток, ≤ 50 мс): длительность, w/h, кодек, поворот. Нечитаемый контейнер → 415.
   7. Дедупликация:
      - попадание в `result_cache` по (sha256, pipeline_version, config_hash), только уровень full: вставить строку `done` и скопировать `jury.json`, `ui_result.json`, `evidence/` с подстановкой нового `job_id`;
      - тот же sha256 уже в работе: вставить строку с `linked_to` на ту задачу;
      - иначе `INSERT jobs`: `status=queued`, `stage=perception`, priority 0, source jury, `received_at=t0`, все уровни дедлайнов, VideoMeta-lite. Событие `accepted` в `job_events`.
   8. `COMMIT`, затем 200 `{"job_id":"<32-hex uuid4>"}`. Обычно < 0,5 с после последнего байта. С этого момента задача не теряется.
3. **Claim perception** (опрос каждые 250 мс): `BEGIN IMMEDIATE; UPDATE jobs SET status='running', lease_owner, lease_pid, alive_at, progress_at WHERE id=(SELECT … stage='perception' AND status='queued' ORDER BY priority, received_at LIMIT 1) RETURNING *; COMMIT`. Затем из уровней дедлайнов строится `Budget`.
4. **План.** `PerceptionLevel` выбирается по EWMA стоимости секунды видео из `perf_stats`, длительности и очереди впереди относительно `vision_target` (см. 9.3).
5. **Deep probe** (PyAV): хотя бы один видеопоток, длительность 1–900 с, w и h ≤ 4096, первый кадр декодируется. Иначе `status=failed`, `error_code=UNSUPPORTED_VIDEO` и сообщение на русском. Это единственный штатный путь в `failed`.
6. **Декодирование и perception, с перекрытием.**
   - `T-decode` декодирует каждый кадр (GOP 1 с с B-кадрами), выбирает кадры на сетке PTS, конвертирует в BGR только их, применяет поворот, кладёт в очередь.
   - Основной цикл на каждый `SampledFrame`: Core ML detect + BoT-SORT (вариант по ориентации); gated pose; gated crop-cls (все фрагменты кадра одним батчем); на выходе `FrameObservation`.
   - `T-store` пишет каждый выбранный кадр JPEG q85 в `frames/{pts_ms}.jpg` и `frames/index.json` (≈ 1 с CPU на 145-секундное видео, оценка).
   - Прогресс в SQLite — не чаще 2 раз в секунду.
   - Выход: `PerceptionTrace` → `trace.json.gz` в папке задачи. **Никогда** не копируется в `var/signals` для задач жюри и UI.
7. **Scene** (чистый Python + numpy):
   - `SceneLayout`: зоны калибруются по детекциям в первые секунды и перепроверяются, если неподвижные предметы сдвинулись. Строятся по объектам, а не по пикселям, поэтому работают на новом ракурсе.
   - `TrackRole`: роль по зонам и поведению, никогда по лицам.
   - `ServiceSession`, `StateInterval` с гистерезисом, `SceneEvent`.
   - Результат: `SceneModel`.
8. **Rules.** `registry.discover()` импортирует все классы с `@register_rule`. `evaluate(RuleContext)` возвращает `Candidate[]` и `Signal[]` → `candidates.json`.
9. **Вердикт по правилам:**
   - score = уверенность правила × коэффициент длительности;
   - кандидаты одного кода в пределах 10 с сливаются;
   - порог τ по коду из `configs/rules/<CODE>.yaml`, подобран по LOSO под recall 1.0;
   - не больше 5 нарушений, сортировка по таймкоду;
   - `verdict = 'violation'`, если остался хотя бы один кандидат.
10. **Уточнение (опционально, P3, флаг в конфиге).** Для ≤ 5 кандидатов-переходов (выход, дверь, открытие сейфа) повторно декодируется окно t ± 1 с на 10 fps, запускается только нужная модель. Первый кадр, где состояние держится, даёт более точный таймкод.
11. **Evidence** — из хранилища кадров, поэтому метки времени — ровно выбранные PTS:
    - (1) кадр события на `timecode_sec`, целиком, с рамками cv2;
    - (2) увеличенный фрагмент ×3–4 вокруг бокса `EvidenceRequest`;
    - (3) кадр-контекст на t − 1,5 с.
    - Файлы: JPEG q85 `evidence/{i}_{event|zoom|context}.jpg`.
12. **Describe.** Шаблон `template_ru` из каталога заполняется `str.format_map` фактами кандидата. Затем добавляется фраза о норме по первой (основной) записи `legal` ([legal_sources.md](legal_sources.md), раздел 6.2; `{clause}` хранится уже с «п.», `{act_cite}` — поле `cite_ru` акта):
    - прямая норма: `Основание: {clause} {act_cite}.`;
    - косвенная: `Может свидетельствовать о несоблюдении {clause} {act_cite}.`;
    - нормы нет, есть ближайший пункт: `Прямой нормы нет; ближайшая — {clause} {act_cite}.`;
    - пункт не определён (FAQ 31): `Прямой нормы нет; пункт {act_cite} не определён (FAQ 31); требуется проверка человеком.`
13. **Промежуточный render:**
    - `AnalysisResult(result_level=rules)` → `analysis.json`;
    - `render.jury` → `jury.json`: сырой base64 JPEG, начинается с `/9j/`; 1–3 кадра; таймкоды float; инвариант вердикта соблюдён. Валидация по замороженной схеме. При любой ошибке — безопасный fallback: перекодировать evidence, зажать таймкоды в [0, duration], оставить ≤ 3 кадров, заново применить инвариант, снова валидировать;
    - `render.ui` → `ui_result.json` (URL вместо base64);
    - каждый файл пишется tmp → fsync → `os.replace`. **Только потом** CAS-обновление ставит `provisional_at` и пути.
14. **Передача в VLM.** Задача уходит на VLM, если одновременно:
    - `CG_VLM_ENABLED`;
    - `now + est_vlm + 15 < vlm_start_cutoff`;
    - свободно ≥ `CG_MIN_FREE_MEM_GB` (3,0 ГБ; формула свободной памяти — раздел 8);
    - очередь vlm worker помещается;
    - есть кандидаты или включён keyframe pass.

    Тогда `stage=vlm`, `status=queued`. Иначе `status=done`, `result_level=rules` и причина деградации. Для задач жюри `input.mov` удаляется сейчас; хранилище кадров живёт до финального результата.
15. **VLM worker:**
    - claim `stage=vlm` так же, как perception;
    - кандидаты с якорями в пределах ±2 с объединяются в один вызов с несколькими вопросами (например, при выходе кассира — ПК, ключ и ценности вместе);
    - группы упорядочены по влиянию на вердикт: `|score − τ|` по возрастанию;
    - на группу: 4–8 кадров из хранилища в масштабе ≈ 0,75 и одна мозаика увеличенных фрагментов; вопросы из каталога с метками времени кадров в тексте; `stream_generate` с JSON-схемой, `max_tokens` 96; `vlm_stop` проверяется на каждом чанке;
    - затем keyframe pass, если остался бюджет: 12–16 кадров половинного разрешения на входах, выходах и сменах состояний → `KeyframeFinding`. По умолчанию это только сигналы. `OTHER_VISIBLE_IRREGULARITY` идёт в вердикт, только если подтверждено треками и выше высокого τ (открытый вопрос, см. раздел 17).
16. **Повторный `decide()`:**
    - ответ VLM «нет» понижает score и никогда не повышает. Пониженный кандидат остаётся в UI с пометкой «снижено VLM»;
    - `τ_final` подбирается на eval-full с сохранением LOSO recall 1.0;
    - если VLM пропущен, результат по правилам — надмножество, безопасное для recall;
    - evidence и describe пересобираются только для изменённых или новых находок;
    - `jury.json` и `ui_result.json` переписываются атомарно и валидируются;
    - CAS finalize: `status=done`, `result_level` full или partial, `verdict`, `n_violations`. Связанные задачи материализуются. `result_cache` пополняется только для уровня full;
    - `frames/` удаляется (`evidence/` остаётся), затем `mx.clear_cache()`.
17. **Sweeper** (lifespan API, каждые 2 с, идемпотентный CAS):
    - (a) прошёл `finalize_at`, есть промежуточный результат, задача не done → `status=done`, `result_level=rules`, деградация `deadline_finalize`;
    - (b) `running`, а pid аренды мёртв (`psutil.pid_exists` False) или `alive_at` старше 20 с → вернуть в очередь, `attempts+1`. Вторая попытка perception — уровень fast. Сбой VLM финализирует промежуточный результат. Попытки исчерпаны: done с промежуточным результатом, если он есть, иначе `failed` с `PIPELINE_CRASH`;
    - (c) раз в час: удалить папки задач старше `CG_RETENTION_HOURS` (24 ч; `0` — очистка выключена, так в окне жюри).
18. **`GET /api/v1/jobs/{job_id}`:**
    - проверка ключа; id должен совпадать с `^[0-9a-f]{32}$`, иначе 404 JSON;
    - один SELECT по индексу, с переходом по `linked_to`;
    - ленивая финализация: если `finalize_at` прошёл и есть промежуточный результат, выполняется тот же CAS, что у sweeper;
    - ответы, все 200 с `Cache-Control: no-store`:
      - queued или running: `{"job_id","status":"processing","verdict":null,"violations":[]}`;
      - done: `FileResponse(jury.json, application/json)`;
      - failed: `{"job_id","status":"failed","verdict":null,"violations":[]}`.

### 4.3. Жизненный цикл задачи

```
                    POST: файл зафиксирован, COMMIT
                                  │
                                  ▼
      ┌──────────────── queued / perception ◄────────────── requeue: attempts+1,
      │                           │ claim                    попытка 2 на уровне fast
      │                           ▼                                  ▲
      │                  running / perception ───────────────────────┘
      │                     │            │                  pid мёртв или alive_at > 20 с
      │                     │            └── deep probe не прошёл ──► failed (UNSUPPORTED_VIDEO)
      │                     │ промежуточный результат записан (provisional_at)
      │                     ├── VLM не нужен / не успеваем / мало памяти ──► done (rules)
      │                     ▼
      │              queued / vlm ── claim ──► running / vlm ──► done (full | partial)
      │                     │                        │
      │                     │                        └── сбой, зависание ──► done (rules, vlm_crash)
      │                     └── прошёл finalize_at ─────────────────────► done (rules, deadline_finalize)
      │
      └── попытки исчерпаны, промежуточного результата нет ─────────────► failed (PIPELINE_CRASH)

 Ответ жюри: queued и running → "processing"; done → jury.json; failed → "failed".
```

Уровни результата (`ResultLevel`):

| Уровень | Когда |
|---|---|
| `full` | VLM проверил все группы кандидатов. Только этот уровень попадает в `result_cache` |
| `partial` | VLM-этап начался, но проверил не все группы (например, остановлен по `vlm_stop`); непроверенные кандидаты остаются с `vlm_status=unverified` |
| `rules` | VLM не запускался или упал; результат — промежуточный по правилам |

### 4.4. Путь UI

1. **Загрузка страницы.** `index.html` с `no-cache`; `/assets/*` с хэшем и `immutable` (≈ 80 КБ gzip JS, лайтбокс — ленивый чанк). Один раз `GET /api/ui/catalog` (ETag = `config_hash`).
2. **Загрузка видео:**
   - на клиенте: .mov или .mp4, ≤ 100 МБ, ошибки на русском;
   - XHR `POST /api/ui/jobs` с процентом, МБ/с и ETA, отменой и защитой `beforeunload`;
   - мгновенное локальное превью через `URL.createObjectURL(file)`; при ошибке `<video>` — переход на серверное превью;
   - на сервере тот же `ingest_upload` с principal `ui`. IP клиента из `CF-Connecting-IP`, только если пир — `127.0.0.1`. Приоритет 1. Лимиты: 5 загрузок за 10 мин с IP, 2 активные задачи с IP, общий лимит очереди UI — 10; в окне жюри — профиль «окно жюри»: 30, 5 и 20 (жюри может загружать и через UI, FAQ 13; [api.md](api.md), раздел 6.1); сверх лимита 429 с `Retry-After`. Задачи UI идут по тем же рубежам дедлайнов от t0, что и задачи API;
   - после COMMIT API делает remux `preview.mp4` в потоке (без перекодирования, ≤ 0,3 с). HEVC или повёрнутое видео ставит `needs_transcode`; тогда perception worker перекодирует через `h264_videotoolbox`.
3. **Опрос статуса:**
   - `history.pushState('/?job=<id>')`;
   - `useJob` опрашивает `GET /api/ui/jobs/{id}` каждую 1 с, пока задача queued или running;
   - `UiJobView` ≈ 0,5 КБ: этапы, позиция в очереди, `eta_sec`, уровни и деградации;
   - как только появился `provisional_at`, ответ несёт нарушения с `provisional=true` и бейджем «Предварительно». Это прогрессивный результат без SSE.
4. **Готово:**
   - полный `UiJobView` из кэша `ui_result.json`; опрос останавливается, `staleTime` = Infinity;
   - видео: `/api/ui/jobs/{id}/video.mp4` с HTTP Range (Safari этого требует);
   - кадры: `/api/ui/jobs/{id}/frames/{name}` (private, immutable);
   - на экране: маркеры `EventTimeline`, карточки нарушений с нормами и лайтбоксом, аккордеон сигналов «Наблюдения (не влияют на вердикт)»;
   - последние 10 job id хранятся в localStorage (в try/catch).

Подробнее: [ui.md](ui.md), [ADR-0007](adr/0007-ui-polling-sse-deferred.md), [ADR-0008](adr/0008-separate-ui-api.md).

### 4.5. Путь обучения и replay (между машинами ходят только числа)

1. **Mac, `.venv-train`, существующий план** ([training/README.md](../training/README.md)):
   `data/raw/open/videos/*.mov` → `01_prepare_raw` (манифест, нормализация X/Х, дубли Х-47/Х-48) → `02_extract_frames` → `03_prelabel` (YOLO26x COCO + Grounding DINO) → `07_build_dataset --source pseudo` → `08_train_detect` smoke, затем pseudo (псевдо-детектор для раннего replay).
2. **Люди правят разметку в Label Studio** (`.venv-ls`), затем `05_labelstudio_import` → `07_build_dataset --source reviewed --loso` (валидационная сцена L4) → `08_train_detect` full (yolo26l, 864) → `09_eval_detect` → `11_extract_crops` → `12_train_cls` для screen, safe_lock, door.
3. **Экспорт:** экспортёр сервиса `src/cashguard/pipeline/perception/export.py` (новый путь, S3) запускается **интерпретатором сервиса**: `.venv/bin/python -m cashguard.pipeline.perception.export --role <role> --weights <best.pt> --version vNNN`. Скрипты `training/` не переписываются: `run_stage_c.sh` по-прежнему выполняет C4–C5 (`10_export_coreml.py` в `.venv-train`), а их выход (`models/<task>/<name>.mlpackage`, `models/manifest.json`) сервис не читает. Экспортёр делает:
   - `det_864x480`, `det_480x864`, `pose_864x480`, `pose_480x864` и `cls_*` .mlpackage;
   - паритет `yolo val` .pt против .mlpackage на отложенной сцене (≤ 1 mAP50);
   - проверка размещения на ANE через MLComputePlan;
   - sha256 каждого файла в `models/<role>/vNNN/manifest.json` (версии, метрики, лицензия);
   - продвижение модели = правка `configs/models.yaml` и рестарт воркеров.
4. **`just extract-signals`** [macos]: боевой perception на 40 обучающих видео → `var/signals/<sha16>__<models_hash>.json.gz`. Ответы VLM записываются для replay-бэкенда.
5. **`just sync-signals`**: scp по LAN или Tailscale в `var/signals` на Windows. Никогда через облачные хранилища.
6. **Windows:**
   - `just eval-replay`: scene → rules → verdict по 40 трассам за секунды; сравнение с [labels_typed.csv](../analysis/labels_typed.csv). Метрики: recall/precision по видео, медиана и p90 |Δt|, hit@1s/2s, точность типа, матрица ошибок против `eval/baselines/main.json`;
   - `just loso`: подбор `configs/rules/*.yaml` на фолдах L1–L5.
7. **Mac:** `just eval-full` (DEV), `just perf` (только SERVE), `just robustness` (hflip, поворот ±15°, rot90 — альбомная модель, crop-zoom, перспектива, гамма, 25 fps).

Видео жюри и UI никогда не попадают в `var/signals`, `data/`, обучение или оценку.

### 4.6. Файлы задачи

`var/jobs/{id}/`. Пишутся через `core.atomic` (tmp + fsync + `os.replace`) и **всегда до** смены статуса в БД.

| Файл | Кто пишет | Срок жизни |
|---|---|---|
| `input.mov` | api | Удаляется после perception (шаг 14 в 4.2) |
| `preview.mp4` | api (remux) или perception (transcode) | Только задачи UI |
| `meta.json` | api | До очистки |
| `frames/{pts_ms}.jpg`, `frames/index.json` | perception (`T-store`) | Удаляются после финального результата |
| `trace.json.gz`, `candidates.json`, `analysis.json` | perception, vlm | До очистки |
| `evidence/{i}_{event\|zoom\|context}.jpg` | perception, vlm | До очистки |
| `jury.json`, `ui_result.json` | perception (промежуточный), vlm (финальный) | До очистки |

Вся папка удаляется через 24 ч (`CG_RETENTION_HOURS`). В окне жюри очистка выключена (`CG_RETENTION_HOURS=0` или ≥ 168), папки удаляются вручную после окна ([jury_mode.md](jury_mode.md), раздел 6).

---

## 5. Внутренние контракты

Все контракты лежат в `src/cashguard/contracts`, владелец S0.
- Pydantic v2, frozen, `extra='forbid'`. Поле `schema_version` — только у сохраняемых артефактов (`PerceptionTrace`, `AnalysisResult`, базовые линии eval). У `AnalyzeAccepted`, `JuryJobResponse`, `JuryViolation`, `JuryEvidenceFrame` (и у `UiJobView`) его нет: форма jury JSON — ровно ТЗ §10, лишний ключ отклоняет замороженная схема.
- Экспорт в JSON Schema и OpenAPI: `python -m cashguard.contracts.export [--check]`. Дрейф проверяется в хуках и в `just check`.
- Изменение контракта — отдельный маленький PR, вливается первым; зависимые ветки делают rebase.

### 5.1. `jury.py` — ЗАМОРОЖЕН

Оракул — `contracts/schemas/jury_job.schema.json`, написан вручную по ТЗ §10, `additionalProperties: false`.

```
AnalyzeAccepted   {job_id: str}
JuryEvidenceFrame {timestamp_sec: float, image_base64: str}      # сырой JPEG '/9j/…', без 'data:'
JuryViolation     {type: str (= CatalogEntry.title_ru), timecode_sec: float,
                   description: str, evidence_frames: list[JuryEvidenceFrame] (1..3)}
JuryJobResponse   {job_id: str, status: Literal['processing','done','failed'],
                   verdict: Literal['violation','no_violation'] | None,
                   violations: list[JuryViolation]}
```

Инварианты:
- done ⇒ `verdict` не None, и `verdict == 'violation'` ⇔ `len(violations) ≥ 1`;
- processing или failed ⇒ `verdict` None и `violations` = [];
- `timecode_sec = round(pts_event, 2)`; все `timestamp_sec = round(pts, 2)` той же функцией; поэтому `evidence_frames[0].timestamp_sec == timecode_sec` точно;
- `0 ≤ timecode_sec ≤ duration`;
- float — Python float;
- не больше 5 нарушений, сортировка по времени;
- никаких лишних ключей. Сигналы сюда не попадают никогда.

Подробнее: [ADR-0006](adr/0006-frozen-jury-contract.md).

### 5.2. `jobs.py` и SQL (`jobs/db.py`)

```
JobStatus       = queued | running | done | failed
JobStage        = perception | vlm | final
JobSource       = jury | ui
PerceptionLevel = full | fast | min
ResultLevel     = full | partial | rules
ErrorCode       = UNSUPPORTED_VIDEO | PIPELINE_CRASH | INTERNAL

DeadlineTiers {t0, vision_target, vlm_start_cutoff, vlm_stop, finalize_at, perception_hard_stop}
    # wall-clock epoch, общие для процессов; внутри процесса — monotonic
Budget        {tiers; now(); remaining(tier) -> float;
               allows(est_sec, tier='vlm_start_cutoff', margin_sec=15) -> bool}
Degradation   {stage, reason: vlm_deadline | vlm_memory | vlm_crash | vlm_backlog |
               vlm_disabled | perception_fast | perception_min | perception_truncated |
               deadline_finalize}
StageState    {code: queued|probe|perception|rules|evidence|vlm|done,
               status: pending|running|done|skipped|failed,
               progress: float, elapsed_sec: float|None, note_ru: str|None}
```

Таблица `jobs`:

| Группа | Колонки |
|---|---|
| Идентичность | `id` TEXT PK (uuid4 hex), `source`, `priority` INT, `client_ip` |
| Состояние | `status`, `stage`, `perception_level`, `result_level`, `progress` REAL, `stage_code`, `stages_json` |
| Дедлайны | `received_at`, `vision_target`, `vlm_start_cutoff`, `vlm_stop`, `finalize_at`, `perception_hard_stop` |
| Отметки времени | `started_at`, `provisional_at`, `finished_at` |
| Аренда | `attempts_perception`, `attempts_vlm`, `lease_owner`, `lease_pid`, `alive_at`, `progress_at` |
| Видео | `sha256`, `size_bytes`, `duration_sec`, `width`, `height`, `rotation`, `codec`, `needs_transcode` |
| Результат | `verdict`, `n_violations`, `linked_to` (REFERENCES jobs(id)) |
| Ошибки и метаданные | `error_code`, `error_msg`, `timings_json`, `degradations_json`, `pipeline_version`, `config_hash` |

Индексы: `(stage, status, priority, received_at)`, `(sha256)`, `(status, finalize_at)`.

Другие таблицы:
- `job_events(id INTEGER PK AUTOINCREMENT, job_id, at, kind, data_json)` — только добавление, не больше 2 записей в секунду на задачу;
- `workers(name PK, role, pid, state warming|idle|busy|draining, started_at, alive_at, progress_at, job_id, rss_mb, mlx_active_mb, models_json, quant)`;
- `result_cache(sha256, pipeline_version, config_hash, job_id)`, PK на первые три;
- `perf_stats(key PK, ewma REAL, n INT)`. Ключи, например: `perception_sec_per_video_sec@full|fast|min`, `vlm_group_call_sec`, `keyframe_pass_sec`.

PRAGMA: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`; `isolation_level=None`; миграции через `PRAGMA user_version`.

Репозиторий `jobs/repo.py`:

```
create_job, link_job
claim_next(stage, owner, pid) -> JobRow | None
beat_alive(owner), beat_progress(job_id, owner)
report_progress(job_id, stage_code, frac, msg)
set_provisional(job_id, paths, next_stage | None, result_level, degradations)
finalize(job_id, result_level, verdict, n, paths)
requeue_dead(now), finalize_overdue(now)
fail(job_id, code, msg)
queue_position(job_id), queue_depth(stage)
```

Каждый переход — compare-and-set, например `UPDATE … WHERE id=? AND status!='done' AND (lease_owner=? OR ?)`. `rowcount == 1` значит, что победил этот вызов. Поэтому один и тот же вызов безопасен из воркера, sweeper и ленивого GET.

### 5.3. `video.py`

```
VideoMeta    {duration_sec, width, height, rotation_deg, fps_avg, is_vfr, codec, pix_fmt,
              start_pts_sec, time_base, sha256}
SampledFrame {idx, t_sec (из PTS), pts, image: ndarray BGR}   # dataclass, не сериализуется
FrameRef     {t_sec, path, box: BBox | None, scale: float = 1.0}
FrameIndex   {frames: list[{idx, t_sec, pts, path}]}
```

### 5.4. `perception.py` — единица replay (пишет S3, читают S4, S5, S6)

```
DetClass  = 14 ASCII-имён в порядке training/configs/classes.yaml:
            person, cash, collection_bag, safe_closed, safe_open, laptop, door,
            counting_machine, auth_detector, id_document, phone, cup, personal_bag, outerwear
BBox      {x1, y1, x2, y2}            # нормированы 0..1 после поворота
Detection {cls: DetClass, conf, box, track_id: int | None}
PoseKeypoints {track_id, box, kpts: list[tuple[x, y, conf]] (17, порядок COCO)}
CropTarget = screen | safe_lock | door
CropState  {target, source_box, label: active|locked|off|key|no_key|closed|open, conf}
FrameObservation {idx, t_sec, detections, poses: list | None, crops: list[CropState],
                  quality: {brightness, blur} | None}
PerceptionPlan  {level, sample_fps, pose_fps, cls_fps, imgsz_variant: '864x480' | '480x864'}
PerceptionTrace {schema_version, video: VideoMeta, models: dict[role, {version, sha256}],
                 plan, frames: list[FrameObservation]}          # хранится как json.gz
```

### 5.5. `scene.py` — пишет S4, читают правила

```
ZoneKind  = desk | cashier_side | client_side | safe | door | counting_machine | auth_detector | room
Zone      {kind, polygon: list[(x, y)], conf}
SceneLayout {zones, orientation, calibrated_from_sec, conf}
Role      = cashier | client | collector | outsider | unknown
TrackRole {track_id, role, conf, first_t, last_t, entered_via_door, exited_via_door}
ServiceSession {id, kind: client | collection, subject_track_id, start_t, end_t, id_shown_t | None}
StateSubject = screen | safe | safe_key | door | cash_on_desk | bag_on_desk |
               personal_item_on_desk | cup | phone | cashier_present
StateInterval {subject, value, start_t, end_t, conf, zone | None, track_id | None}
SceneEventKind = enter | exit | sit | stand | handoff_cash | cash_to_machine | cash_to_detector |
                 cash_to_safe | cash_from_bag | id_shown | bag_on_desk | cup_to_mouth |
                 phone_in_hand | state_change
SceneEvent {kind, t, track_ids, conf, box | None, data}
SceneModel {layout, roles, sessions, states: list[StateInterval], events: list[SceneEvent]}
```

### 5.6. `findings.py`

```
RuleConfig      # из configs/rules/<CODE>.yaml
RuleContext     {video: VideoMeta, trace: PerceptionTrace, scene: SceneModel, cfg: RuleConfig}
EvidenceRequest {kind: event | zoom | context, t_sec, box: BBox | None, label}
Candidate {id, code: ViolationCode, t_sec, start_sec, end_sec, confidence 0..1,
           facts: dict[str, str | int | float],      # переменные шаблона
           evidence: list[EvidenceRequest], vlm_group: str | None, rule_version, verdict_impact: bool}
Signal    {id, code: SignalCode, t_sec, start_sec, end_sec, confidence, facts}
VlmQuestion {group_id, candidate_ids, questions: list[{qid, code, text_ru}],
             frames: list[FrameRef], max_tokens}
VlmAnswer   {group_id, answers: list[{qid, a: yes | no | unclear, c: float}],
             note: str | None,     # ≤ 200 символов, только UI/отладка, никогда в описание жюри
             tokens_in, tokens_out, latency_ms, model_rev}
KeyframeFinding {t_sec, suggested_code: ViolationCode | None, confidence, summary_ru}
VlmReview       {answers, findings, skipped_reason: str | None}
Decision        {candidate, score, vlm_status: confirmed | downweighted | unverified | skipped}
EvidenceImage   {kind, timestamp_sec, file, width, height}
ViolationFinding {id, code, title_ru, timecode_sec, start_sec, end_sec, confidence, vlm_status,
                  provisional: bool, description_ru, legal: list[LegalRef],
                  evidence: list[EvidenceImage] (1..3)}
SignalFinding   {id, code, title_ru, t_sec, description_ru, evidence}
AnalysisResult  {schema_version, job_id, verdict, result_level, perception_level, violations,
                 signals, video, timings: dict[str, float], degradations,
                 versions: {git_sha, models_hash, config_hash}}
```

### 5.7. `catalog.py`

```
ViolationCode (StrEnum):
  17 кодов из датасета: CASH_WITHHELD, NO_CLIENT_ID, PREV_CASH_ON_DESK, MIXED_CLIENT_CASH,
    RECOUNT_WITHOUT_CLIENT, NO_AUTH_CHECK, VALUABLES_LEFT, SAFE_OPEN, SAFE_KEY_LEFT, PC_UNLOCKED,
    DOOR_NOT_CLOSED, OUTSIDER_IN_CASH_ROOM, THIRD_PERSON_DURING_SERVICE, COLLECTOR_NOT_VERIFIED,
    BAG_CASH_NO_RECOUNT, PERSONAL_ITEMS, FOOD_DRINK
  8 кодов группы B1: PHONE_CASHIER, LEFT_DURING_SERVICE, PAYOUT_WITHOUT_RECOUNT,
    CHANGE_BEFORE_PREPARED, STORAGE_UNESCORTED, CASH_CONCEALED, CASH_TRANSFER_OUTSIDE_OP,
    UNATTENDED_PRESENCE
  + OTHER_VISIBLE_IRREGULARITY
SignalCode (StrEnum, 13): SIG_SEAL_NOT_INSPECTED, SIG_BAG_OPENED_WITHOUT_COLLECTOR,
  SIG_RECOUNT_WITHOUT_SECOND_EMPLOYEE, SIG_STAFF_CASH_TRANSFER, SIG_DOC_NOT_HANDED,
  SIG_REJECT_TRAY_TO_CLIENT, SIG_NO_SIGNATURE_SEEN, SIG_NO_STAMP_SEEN, SIG_MULTI_DOC_SINGLE_RECOUNT,
  SIG_SAFE_CLOSED_WITHOUT_COUNT, SIG_SAFE_WITHOUT_PC, SIG_CASH_TO_STAFF, SIG_CAMERA_OBSTRUCTED
LegalStrength  = direct | indirect | none
LegalRef       {act_code: PKO | POP | POO | TIB, act_title_ru,
                clause: str | None (с префиксом: «п. 73 пп. 6)»; None только для ссылки на уровне акта, FAQ 31),
                quote_ru (дословно из analysis/normative), url, strength}
                # первая запись legal — основная, её пункт идёт в описание жюри
TimecodePolicy = event | exit | state_start | omission_due
CatalogEntry   {code, title_ru (стабильная строка type для жюри),
                group: leave | access | cashops | collection | discipline | other,   # модуль присутствия — в leave
                verdict_impact, timecode_policy, template_ru (поля для str.format_map),
                required_facts, vlm_question_ru, legal: list[LegalRef], owner}
ActEntry       {act_code, title_ru, cite_ru (краткая ссылка для {act_cite}), issuer, number, date,
                registration_no, adilet_id, url, status}
Acts           {acts: list[ActEntry], do_not_cite: list[str]}   # корень configs/legal/acts.yaml
```

Откуда коды: группы A, B1 и B2 из раздела «Покрытие списка команды» в [detection_design.md](../analysis/detection_design.md). Нормы — только из раздела 7 [case_analysis.md](../analysis/case_analysis.md). Логика каждого кода — [violation_catalog.md](violation_catalog.md); разбор норм — [legal_sources.md](legal_sources.md). Перечень типов для жюри `docs/violation_types.md` генерируется из `configs/catalog/` (руками не правится).

### 5.8. `ui.py` (→ OpenAPI → `web/src/shared/api/schema.d.ts`)

```
UiJobView {job_id, status: queued | running | done | failed, created_at, elapsed_sec,
           queue_position, eta_sec, perception_level, result_level | None,
           stages: list[StageState], degradations: list[{stage, reason_ru}], provisional: bool,
           video: UiVideo{url, duration_sec, width, height} | None, verdict | None,
           violations: list[UiViolation], signals: list[UiSignal], timings: dict | None,
           error: UiError{code, message_ru} | None}
UiViolation {id, code, title_ru, timecode_sec, start_sec, end_sec, confidence, vlm_status,
             provisional, description_ru,
             legal: list[UiLegalRef{act_code, act_title_ru, clause, quote_ru, url, strength}],
             evidence: list[UiEvidence{timestamp_sec, url, kind, width, height}]}
UiSignal  {id, code, title_ru, t_sec, description_ru, evidence}
UiCatalog {config_hash, types: list[{code, title_ru, group, verdict_impact, legal}],
           signals: list[{code, title_ru}], acts: list[ActEntry]}
```

### 5.9. `health.py`

```
LiveReport  {status: 'ok'}
ReadyReport {ready: bool, checks: {worker_perception, worker_vlm, models_loaded, warmup_done,
             free_mem_ok, disk_ok, queue_ok}, vlm_quant, version}
AdminStatus {versions, models: dict[role, {version, sha256}], queue: {perception, vlm},
             workers: list, recent_jobs: list[{id, source, status, perception_level,
             result_level, verdict, n, timings}],
             memory: {available_gb, mlx_active_gb, mlx_peak_gb}, disk_free_gb, tunnel_ready}
```

### 5.10. `ports.py` (typing.Protocol)

| Порт | Методы | Реализации |
|---|---|---|
| `VideoSource` | `probe(path) -> VideoMeta`; `iter_samples(path, plan) -> Iterator[SampledFrame]`; `decode_window(path, t0, t1, fps) -> list[SampledFrame]` | `pipeline/decode/pyav_source.py` |
| `PerceptionBackend` | `name`; `warmup()`; `info() -> dict`; `run(samples, plan, progress) -> PerceptionTrace` | coreml, torch, replay, stub, null |
| `FrameStore` | `put(frame) -> FrameRef`; `get(t_sec) -> ndarray`; `nearest(t_sec) -> FrameRef`; `index() -> FrameIndex` | `pipeline/decode/frame_store.py` |
| `Rule` | `code`; `version`; `verdict_impact`; `evaluate(ctx) -> list[Candidate]` | по файлу на код в `rules/<group>/` |
| `SignalRule` | `code`; `evaluate(ctx) -> list[Signal]` | `rules/signals/` (13) |
| `VlmBackend` | `name`; `load(path, quant)`; `warmup()`; `ask(q, images, deadline_ts) -> VlmAnswer`; `scan_keyframes(images, times, deadline_ts) -> list[KeyframeFinding]` | mlx, transformers, replay, null |
| `ProgressFn` | `Callable[[stage_code, frac, msg], None]` | — |
| `Clock` | `now()`; `monotonic()` | — |

Правила и сигнальные правила регистрируются через `@register_rule` и находятся через pkgutil. Центрального списка нет.

### 5.11. Функции этапов

Сигнатуры заморожены на P0 в `pipeline/runner.py`; владельцы реализуют.

| Функция | Владелец |
|---|---|
| `ingest.probe(path) -> VideoMeta` | S1 |
| `ingest.make_preview(path, meta, out) -> Path \| None` | S1 |
| `perception.run(path, meta, plan, backend, store, progress) -> PerceptionTrace` | S3 |
| `scene.build(trace, cfg) -> SceneModel` | S4 |
| `rules.evaluate_all(ctx) -> tuple[list[Candidate], list[Signal]]` | S0 (registry), S4/S5/S6 (правила) |
| `verdict.decide(cands, review \| None, cfg) -> list[Decision]` | S5 |
| `evidence.render(decisions, store, out_dir) -> dict[str, list[EvidenceImage]]` | S3 |
| `legal.describe(decision, scene, catalog) -> tuple[str, list[LegalRef]]` | S5 |
| `vlm.plan_questions(decisions, store, cfg) -> list[VlmQuestion]` | S6 |
| `render.jury(result, evidence_dir) -> JuryJobResponse` (валидация + fallback) | S1 |
| `render.ui(result) -> UiJobView` (фрагмент) | S1 |
| `runner.run_perception_stage(job, budget)` | S0 |
| `runner.run_vlm_stage(job, budget)` | S0 |

### 5.12. Настройки и конфиги

`pydantic-settings`, префикс `CG_`, файл `~/.config/cashguard/.env`:

| Настройка | Значения / по умолчанию |
|---|---|
| `API_KEYS` | `'jury:<tok>,team:<tok>'`, SecretStr |
| `DATA_DIR` / `MODELS_DIR` / `WEB_DIST` | `var` / `models` / `web/dist` |
| `HOST`, `PORT` | — |
| `PIPELINE_MODE` | `stub`, `replay`, `full` |
| `VISION_BACKEND` | `coreml`, `torch`, `replay`, `stub`, `null` |
| `VLM_BACKEND` | `mlx`, `transformers`, `replay`, `null` |
| `VLM_ENABLED` | — |
| `VLM_QUANT` | `auto`, `8bit`, `6bit`, `4bit` |
| `MAX_UPLOAD_MB` | 100 |
| `MIN_FREE_MEM_GB` | 3.0 (свободно перед VLM-этапом; раздел 8) |
| `MIN_FREE_DISK_GB` | 2 |
| `UI_ACCESS_CODE` | необязателен; по умолчанию выключен |
| `UI_RATE_*` | лимиты на IP: обычно 5 загрузок за 10 мин, 2 активных, очередь UI 10; в окне жюри — 30, 5 и 20 ([api.md](api.md), раздел 6.1) |
| `DOCS_ENABLED` | — |
| `RETENTION_HOURS` | 24; `0` — очистка выключена (окно жюри) |
| `UI_SSE` | зарезервирован для P3, в v1 не используется |

YAML (`yaml.safe_load`, валидация в Pydantic при старте): `Catalog`, `Acts`, `RuleParams[CODE]`, `SceneConfig`, `PerceptionConfig`, `VlmConfig`, `Deadlines`, `PerfBudget`, `ModelsRegistry`.

| Файл | Владелец | Что внутри |
|---|---|---|
| `configs/catalog/<CODE>.yaml` | S0 v0 → владелец кода | `title_ru`, группа, `verdict_impact`, `timecode_policy`, `template_ru`, `required_facts`, `vlm_question_ru`, нормы |
| `configs/catalog/signals.yaml` | S0 v0 → S6 | 13 кодов SIG_*, формулировки «не наблюдается…»; без норм в v1 |
| `configs/legal/acts.yaml` | S0 | `Acts`: ПКО №231, ПОП №14 (+№88), ПОО №49, ТИБ АРРФР №53 с `cite_ru`; `do_not_cite` — утратившие силу (№ 58, 250, 48, 254) |
| `configs/rules/<CODE>.yaml` | владелец кода | τ, длительности, гистерезис (подбор LOSO) |
| `configs/scene.yaml`, `perception.yaml`, `vlm.yaml` | S4 / S3 / S6 | Параметры этапов |
| `configs/deadlines.yaml`, `perf_budget.yaml` | S1 | Уровни дедлайнов, бюджеты этапов |
| `configs/models.yaml` | S0, правит S3 | роль → версия, путь, sha256, imgsz, бэкенд по платформе, ревизия и квантизация VLM |
| `configs/trackers/botsort_static.yaml` | S3 | Параметры BoT-SORT |

- `config_hash` = sha256 по нормализованному YAML. Хранится в каждой задаче.
- `pipeline_version` = git sha + хэш манифеста моделей.

### 5.13. Границы импортов (import-linter, в `just check`)

- Порядок слоёв: `api → jobs → core → contracts` и `workers → pipeline → contracts`.
- `api`, `jobs`, `core`, `contracts` не импортируют `torch`, `ultralytics`, `coremltools`, `mlx`, `mlx_vlm`, `cv2`. `av` в `api` разрешён.
- `pipeline.scene`, `rules`, `verdict`, `legal`, `render` — чистый Python + numpy, работают в replay на Windows.
- Подпакеты `rules.*` не зависят друг от друга.
- Никто не импортирует `training/` и `kassa_train`.

### 5.14. Генерируемые файлы (руками не править)

- `src/cashguard/contracts/schemas/generated/*.json`
- `web/src/shared/api/openapi.json`, `web/src/shared/api/schema.d.ts`
- `docs/violation_types.md`
- `uv.lock`, `pnpm-lock.yaml`: перегенерировать, никогда не сливать руками.

---

## 6. API (кратко)

Все маршруты обслуживает один uvicorn на `127.0.0.1:8000`. Единственный внешний вход — туннель. Всё на одном origin, CORS нет. Ошибки всегда JSON (`{"detail": ...}`), неизвестный маршрут — 404 JSON. `GZipMiddleware(minimum_size=1024, exclude_content_types=['video/mp4','image/jpeg'])`. Полное описание — [api.md](api.md).

| Метод и путь | Доступ | Ответ | Ошибки |
|---|---|---|---|
| `POST /api/v1/analyze` (+ `/analyze/`, скрыт) | `X-API-Key` (jury, team) | 200 `{"job_id"}` | 401, 413, 415, 422, 507; 503 только при превышении `--limit-concurrency 64` |
| `GET /api/v1/jobs/{job_id}` (+ слэш) | `X-API-Key` | 200 processing / done (`jury.json`) / failed | 401; 404 на неверный формат или неизвестный id |
| `GET /api/v1/admin/status` | только ключ team; ключ jury → 403 | `AdminStatus` | 401, 403 |
| `POST /api/ui/jobs` | без ключа, лимиты на IP | 200 `{"job_id"}` | 413, 415, 422, 507, 429 + `Retry-After` |
| `GET /api/ui/jobs/{job_id}` | без ключа | `UiJobView` | 404 |
| `GET /api/ui/jobs/{job_id}/video.mp4` | без ключа | faststart H.264, Range (206), ETag | 404 до готовности |
| `GET /api/ui/jobs/{job_id}/frames/{name}` | без ключа | JPEG; `name` по `^\d{1,2}_(event\|zoom\|context)\.jpg$` | 404 |
| `GET /api/ui/catalog` | без ключа | `UiCatalog`, ETag = `config_hash`, 304 | — |
| `POST /api/ui/session` | только при `CG_UI_ACCESS_CODE` | HttpOnly SameSite=Strict cookie | — |
| `GET /api/ui/jobs/{job_id}/events` | — | ЗАРЕЗЕРВИРОВАН, не в v1 (SSE, P3) | — |
| `GET /health` | открыт | 200 `{"status":"ok"}` < 10 мс, без I/O | — |
| `GET /ready` | открыт | 200 или 503 с именами непройденных проверок | — |
| `GET /`, `/assets/*`, `/robots.txt`, `/favicon.svg` | открыт | SPA из `web/dist` | — |
| `GET /docs`, `/openapi.json` | открыт | Swagger с кнопкой Authorize для `X-API-Key` | — |

Заголовки кэша:
- API жюри: `Cache-Control: no-store`, лимитов нет, очередь не ограничена.
- `GET /api/ui/jobs/{id}`: `no-store`, пока идёт обработка; `private, max-age=60` после done.
- `video.mp4`: `private, max-age=3600`.
- `frames/{name}`: `private, max-age=31536000, immutable` (private — чтобы Cloudflare не кэшировал кадры с камеры).
- `index.html`: `no-cache`; `/assets/*`: `public, max-age=31536000, immutable`.

`/ready` = 200, только если: heartbeat воркеров моложе 10 с; модели загружены и прогреты (детектор, поза, 3 cls, VLM или его запасная квантизация); свободно памяти ≥ 3,0 ГБ (`CG_MIN_FREE_MEM_GB`); свободно диска ≥ 5 ГБ; очередь ниже лимита. В ответе `vlm_quant` и версия. Точка в подвале UI читает `/ready`.

Не публикуются: входные видео (никакого static mount), `var/` и `data/` (нет произвольных файловых маршрутов). Медиа отдаются только через проверенные маршруты выше.

Swagger UI грузит JS с CDN. В офлайн-контуре банка ставить `CG_DOCS_ENABLED=false` или раздавать ассеты Swagger локально. `/openapi.json` остаётся источником типов для web.

---

## 7. Стек

Версии закреплены. Пометка «проверить» означает, что значение не подтверждено и его нужно сверить до фиксации в lock-файле. Обоснования — в ADR.

### 7.1. Сервис (Python)

| Слой | Выбор | Версия | Лицензия |
|---|---|---|---|
| Python | CPython 3.12 (uv, python-build-standalone); `requires-python '>=3.12,<3.13'`; `.python-version` = 3.12. Та же минорная версия в `.venv`, `.venv-train` и Docker | 3.12.x (Docker `python:3.12-slim-trixie` = 3.12.15) | PSF-2.0 |
| Окружение и lock | uv: один корневой `pyproject.toml` и один универсальный `uv.lock`, `[tool.uv] environments` darwin-arm64, win32-AMD64, linux-x86_64. Extras: `ml` (ultralytics, torch, torchvision, lap, opencv-python), `mac` (coremltools, mlx, mlx-vlm, llguidance; маркеры darwin+arm64), `cuda` (transformers; маркер linux; индекс PyTorch CUDA через `tool.uv.sources`). Группа `dev` по умолчанию | uv 0.13.1 | MIT OR Apache-2.0 |
| Веб-фреймворк | FastAPI на Starlette, только lifespan. Явные зависимости (НЕ `fastapi[standard]`). `separate_input_output_schemas=False`; Literal для status, verdict, stage | fastapi 0.143.3, starlette 1.8.0 | MIT / BSD-3-Clause |
| Контракты | Pydantic v2, frozen, `extra='forbid'`, в `cashguard.contracts`. Оракул — замороженная JSON Schema жюри | pydantic 2.14.1 | MIT |
| Настройки и конфиги | pydantic-settings (префикс `CG_`, `~/.config/cashguard/.env`, SecretStr); PyYAML `safe_load` для `configs/*.yaml` | pydantic-settings 2.16.0, PyYAML 6.0.3 | MIT / MIT |
| ASGI-сервер | `uvicorn[standard]` (uvloop + httptools), 1 worker, `127.0.0.1:8000`, keep-alive 100 с | uvicorn 0.54.0, uvloop 0.23.0, httptools 0.9.0 | BSD-3-Clause / MIT OR Apache-2.0 / MIT |
| Приём загрузки | Сырой `request.stream()` → `StreamingFormDataParser` (FileTarget + sha256 + лимит байт) | streaming-form-data 2.1.0 | MIT |
| Очередь и состояние | stdlib sqlite3, WAL, CAS-переходы, `BEGIN IMMEDIATE…UPDATE…RETURNING` | SQLite из Python 3.12 (нужна ≥ 3.35 для RETURNING; проверить на Mac) | Public domain |
| Логи | structlog, JSON lines, RotatingFileHandler (10 МБ × 5) на процесс в `var/logs/{api,perception,vlm,supervisor}.jsonl`; `job_id` и stage через contextvars | structlog 26.1.0 | MIT OR Apache-2.0 |
| Системные метрики | psutil + `mx.get_active_memory` / `get_peak_memory` в vlm | psutil 7.2.2 | BSD-3-Clause |
| Видео | PyAV: программный H.264, `thread_type='AUTO'`, сетка PTS, `frame.rotation`; VideoToolbox hwaccel за флагом; remux в mp4 с `movflags=+faststart` | av 19.0.1 (включает FFmpeg с VideoToolbox) | BSD-3-Clause (FFmpeg внутри — LGPL; macOS-колесо включает GPL-части x264/x265) |
| Изображения | opencv-python: кропы, рамки cv2 с ASCII-подписями, JPEG (никогда `Results.plot()`); numpy жёстко закреплён | opencv-python 5.0.0.93, numpy 2.3.5 | Apache-2.0 / BSD-3-Clause |
| Детектор (14 классов) | YOLO26l detect, классы как в `training/configs/classes.yaml`, обучен на imgsz 864. Core ML: `nms=False`, `quantize=16`, batch 1, imgsz 864,480 (портрет) и 480,864 (ландшафт, ленивая загрузка). Экспорт и паритет — в venv СЕРВИСА | ultralytics 8.4.179 (проверить на PyPI; тот же пин в `uv.lock` и `training/requirements.txt`; запасной — 8.4.178 в обоих); веса ultralytics/assets v8.4.0 | AGPL-3.0 (явно разрешён организаторами; исходники сервиса публикуются) |
| Поза | YOLO26l-pose, готовая, Core ML 864x480. Только для треков в зонах стола, передачи, сейфа, ≤ 2 fps (на уровне fast — только в окнах кандидатов) | ultralytics 8.4.179 (проверить); `yolo26l-pose.pt` из assets v8.4.0 | AGPL-3.0 |
| Классификаторы состояний | YOLO26s-cls, imgsz 224; геометрия кропа — ровно как `data/crops/crop_spec.json` (region, pad, upscale, min_side), letterbox — только при `letterbox: true` в crop_spec, иначе предобработка Ultralytics как на val; задачи screen (active/locked/off), safe_lock (key/no_key), door (closed/open). Все кропы кадра — один вызов predict; 1,5 fps + триггеры смены состояния. Обучение с `erasing=0.0`, `auto_augment=None` | ultralytics 8.4.179 (проверить) | AGPL-3.0 |
| Трекинг | BoT-SORT через `configs/trackers/botsort_static.yaml` (`with_reid False`, `gmc_method none`, `track_high 0.4`, `track_low 0.1`, `new_track 0.5`, `match 0.8`, `fuse_score True`, `track_buffer 15`); передаётся явно, сбрасывается на задачу | встроен в ultralytics + lap 0.5.13 | AGPL-3.0 / BSD-2-Clause |
| Neural Engine | Core ML .mlpackage, compute units `CPU_AND_NE` (жёстко задано в CoreMLBackend Ultralytics; никогда не менять на ALL). Прогрев всех моделей до `/ready` | coremltools 9.0 | BSD-3-Clause |
| Torch | MPS-обучение (`.venv-train`), torch-бэкенд perception (CUDA на Windows/Linux), экспорт. Если конверсия в Core ML падает — изолированное окружение экспорта: `uv run --isolated --with torch==2.8.* --with numpy==2.3.5 --with coremltools==9.0 --with ultralytics==<pin>` | torch 2.14.1 / torchvision 0.29.1 (запасной для экспорта 2.8.x / 0.23.x) | семейство BSD-3-Clause |
| VLM | Qwen3.5-4B из mlx-community: 8-bit rev `1704583a` (5,16 ГБ); запасные 6-bit rev `fbb1092a` (4,11 ГБ) и 4-bit rev `0e7ffd5c` (3,06 ГБ), выбор по замеренной свободной памяти. `stream_generate`, `enable_thinking=False`, temperature 0, llguidance JSON-schema processor, `max_tokens` 96 (проверка) и 160 (keyframe pass), проверка дедлайна на каждом чанке. `mx.set_cache_limit(1 ГБ)`, `mx.set_memory_limit(7 ГБ)`, `mx.clear_cache()` на задачу | mlx 0.32.4, mlx-vlm 0.7.8, llguidance 1.9.1 | MIT / MIT / MIT; веса Apache-2.0 (унаследовано от Qwen/Qwen3.5-4B; у репозиториев mlx-community нет карточки, происхождение пишется в манифест) |
| VLM на Linux | transformers за тем же портом `VlmBackend` (extra `cuda`) | transformers 5.20.1 | Apache-2.0 |
| Загрузка моделей | `hf` CLI с закреплённым `--revision` в `models/hf/<repo>`; веса YOLO из ultralytics/assets v8.4.0 в `models/pretrained`. Это существующий `training/download_models.sh`, дополненный ревизиями, 6-bit и sha256-манифестом | huggingface-hub 2.2.0 | Apache-2.0 |

### 7.2. Обучение и разметка (вне lock-файла сервиса)

| Слой | Выбор | Версия | Лицензия |
|---|---|---|---|
| Пред-разметка | YOLO26x COCO + Grounding DINO base в `.venv-train` (этап A: `03_prelabel.py`) | IDEA-Research/grounding-dino-base, ревизия: проверить; transformers ≥ 5.0 в `.venv-train` (точную версию проверить) | Apache-2.0 (по заметке в репозитории; проверить по карточке модели) |
| Разметка | Label Studio Community в своём venv (`.venv-ls`), локальная раздача файлов, экспорт «YOLO with images», затем `05_labelstudio_import.py` | label-studio 1.23.2 | Apache-2.0 |
| Конкурент детектора | RF-DETR Medium в `.venv-train` на том же YOLO-датасете: resolution 704 или 768, batch 2 × grad_accum 8 на MPS. Продвигается, только если обходит YOLO26l на ≥ 3 mAP50 на отложенной сцене | rfdetr 1.11.2 (+ supervision 0.31.0) | Apache-2.0 (только чекпойнты N/S/M/L) / MIT |

### 7.3. Фронтенд

| Слой | Выбор | Версия | Лицензия |
|---|---|---|---|
| Фреймворк | React SPA на Vite. Без SSR и роутера: вид из `?job=<id>` и `?view=types`. Сборка в `web/dist`, раздаёт FastAPI с того же origin | react / react-dom 19.3.1, vite 8.3.4, @vitejs/plugin-react 6.1.2 | MIT |
| Язык | TypeScript, точный пин; проверка `tsc -b` отдельно от сборки Vite | typescript 5.9.3 | Apache-2.0 |
| Данные | TanStack Query — единственная библиотека состояния: `refetchInterval` 1000 мс при queued/running, `staleTime` Infinity после done. Загрузка через XHR с прогрессом | @tanstack/react-query 5.104.3 | MIT |
| Типы API | openapi-typescript (только типы) + openapi-fetch из закоммиченного `web/src/shared/api/openapi.json` (его делает `python -m cashguard.contracts.export`, без ML) | openapi-typescript 7.13.0, openapi-fetch 0.17.0 | MIT |
| UI-кит | Tailwind v4 через `@tailwindcss/vite`; компоненты shadcn CLI на radix-ui; lucide-react; sonner; clsx + tailwind-merge. Только системные шрифты. Русские строки в `src/shared/i18n/ru.ts` | tailwindcss 4.3.3, @tailwindcss/vite 4.3.3, shadcn 4.22.0, radix-ui 1.8.1, lucide-react 1.59.0, sonner 2.0.8, clsx 2.1.1, tailwind-merge 3.7.0 | MIT / ISC (lucide) |
| Просмотр кадров и плеер | yet-another-react-lightbox с плагинами Zoom и Captions (ленивая загрузка). Нативный `<video controls playsInline preload=metadata>` + свой `EventTimeline` (маркер в t/duration; клик — переход на max(0, t − 2)) | yet-another-react-lightbox 3.32.2 | MIT |
| Инструменты | Node LTS в `.node-version` и `engines` (engine-strict); pnpm через `packageManager`, всегда `--frozen-lockfile`; Biome для lint и format | node 24.22.0, pnpm 10.34.7, @biomejs/biome 2.5.15 | MIT / MIT / MIT OR Apache-2.0 |
| Тесты | Vitest + Testing Library + jsdom; Playwright E2E против FastAPI в режиме stub: channel `chrome` на Windows (кодеки H.264), WebKit и iPhone на Mac | vitest 5.0.5, @testing-library/react 16.3.3, jsdom 30.1.3, @playwright/test 1.64.1 | MIT / MIT / MIT / Apache-2.0 |

### 7.4. Качество, сборка, эксплуатация

| Слой | Выбор | Версия | Лицензия |
|---|---|---|---|
| Тесты бэкенда | pytest + pytest-timeout + anyio + httpx ASGITransport; jsonschema по замороженной схеме; schemathesis по приложению в процессе; syrupy-снапшоты описаний | pytest 9.1.1, pytest-timeout 2.4.0, anyio 4.15.1, httpx 0.28.1, jsonschema 4.26.0, schemathesis 4.31.1, syrupy 6.1.1 | MIT / MIT / MIT / BSD-3-Clause / MIT / MIT / MIT |
| Lint, типы, хуки | ruff (E,F,I,B,UP,SIM,PL,RUF,S, включая PLW1514); basedpyright (standard, strict для contracts и api); import-linter; prek с `.pre-commit-config.yaml`, запасной — pre-commit | ruff 0.17.1, basedpyright 1.40.2, import-linter 2.15, prek 0.5.5 (запасной pre-commit 4.7.0) | MIT / MIT / BSD-2-Clause / MIT |
| Запуск задач | just из колеса rust-just в группе dev: `uv run just <recipe>`; `[windows]` — PowerShell; `[macos]` — сервисы, обучение, perf | rust-just 1.59.0 | CC0-1.0 |
| Супервизия на Mac | launchd LaunchAgents `kz.cashguard.supervisor`, `kz.cashguard.tunnel`, `kz.cashguard.caffeinate`; supervisor (`cashguard up`) запускает api, perception, vlm | macOS ≥ 14 (15 рекомендуется для `mx.set_wired_limit`); свой код | встроено / свой |
| Публичный вход | Именованный Cloudflare Tunnel на домене команды: protocol http2, ingress `http://127.0.0.1:8000`, metrics `127.0.0.1:20241`, Bot Fight Mode выключен, без кэша на `/api`. Tailscale Funnel — параллельный резервный URL. Quick tunnel — только для дымовых тестов P0, жюри не отдаётся | cloudflared 2026.10.1, tailscale v1.104.1 | Apache-2.0 / BSD-3-Clause |
| Linux / банк | `deploy/linux/Dockerfile.cuda`, две стадии (сборка на uv + slim runtime с libgl1 и libglib2.0-0). `compose.yaml`: api, perception (`gpus: all`, torch), vlm (transformers) из одного образа, общий bind mount `var`, веса только на чтение в `/models` | `ghcr.io/astral-sh/uv:0.13.1-python3.12-trixie-slim`, `python:3.12-slim-trixie` (3.12.15) | MIT OR Apache-2.0 / PSF-2.0 |

Запрещённые обновления и пины, которые агенты не трогают: `fastapi[standard]`, TypeScript 7 (и 6 без согласования), react-router, MSW, pnpm 12, numpy ≠ 2.3.5. Список ведётся в `AGENTS.md`.

---

## 8. Бюджет памяти Mac

Все числа — оценка. `just perf` проверяет их на реальном прогоне. Требование: **на пике свободно ≥ 3 ГБ** из 16 ГБ.

| Компонент | ГБ (оценка) |
|---|---|
| macOS (все прочие приложения закрыты) | 4–5 |
| api | 0,2 |
| perception (Core ML модели, torch/ultralytics, очередь 16 кадров) | 1,5–2,0 |
| vlm (Qwen3.5-4B 8-bit + контекст + кэш MLX ≤ 1 ГБ) | 6,0–6,5 |
| cloudflared | 0,05 |
| **Итого** | **≈ 12–13,7** |

Ступени квантизации VLM (`CG_VLM_QUANT=auto`):

| Вариант | Веса | RSS процесса vlm (оценка) | Экономия против 8-bit |
|---|---|---|---|
| 8-bit (основной) | 5,16 ГБ | ≈ 6,0–6,5 ГБ | — |
| 6-bit | 4,11 ГБ | ≈ 5,0 ГБ | ≈ −1,05 ГБ |
| 4-bit | 3,06 ГБ | ≈ 4,0 ГБ | ≈ −2,1 ГБ |

Итог 12–13,7 ГБ оставляет 2,3–4 ГБ из 16 ГБ. На верхней границе оценки 8-bit требование не выполняет, поэтому 8-bit — не данность, а результат замера.

Правила:
- **Квант для окна жюри.** 8-bit остаётся, только если `just perf` в режиме SERVE (пачка из 5 роликов, включая Х-14) показал минимум свободной памяти ≥ 3 ГБ. Свободная память = min(psutil available, 16 − Σ RSS процессов сервиса − `mx.get_active_memory`) (формула — проверить). Иначе в `~/.config/cashguard/.env` для serve ставится `CG_VLM_QUANT=6bit`.
- Замер для кванта — в окне SERVE при закрытых Label Studio, браузерах и сессиях Claude Code и Codex на Mac. Предварительный `bench_mac.py` в P0 идёт при работающих Label Studio и агентах и квант не фиксирует.
- Автовыбор при старте (`CG_VLM_QUANT=auto`) — после загрузки 8-bit и прогрева максимального размера: если psutil available < 3,0 ГБ — 6-bit, затем 4-bit. Это проверка в покое, а не на пике.
- VLM-этап задачи запускается, только если свободно ≥ 3,0 ГБ (`CG_MIN_FREE_MEM_GB`, та же формула). Порог совпадает с требованием: если перед VLM свободно меньше 3 ГБ, на пике будет ещё меньше. В лог пишутся и psutil, и `mx.get_active_memory`: psutil занижает wired-память MLX.
- MLX: `set_cache_limit(1 ГБ)`, `set_memory_limit(7 ГБ)`, `clear_cache()` после каждой задачи.
- Кадры видео никогда не держатся в памяти все сразу: очередь декодера ≤ 16 кадров, хранилище кадров на диске.
- perception перезапускает себя при RSS > 3 ГБ или после 200 задач, только при пустой очереди.
- Видео длиннее 300 с получают адаптивный fps, чтобы выбранных кадров было ≤ 600.
- Во время проверки жюри на Mac не работает ничего, кроме сервиса и туннеля: ни браузера, ни IDE, ни обучения, ни оценки.
- Qwen3.5-9B не используется: с ним свободно лишь ≈ 1,5–2,5 ГБ (оценка из [detection_design.md](../analysis/detection_design.md)).

---

## 9. Бюджет времени и деградация

### 9.1. Цели

Все цели — оценка. `scripts/bench_mac.py` заменяет их замерами: предварительно в Ч+3,75 – 4,75 (DEV), окончательно в SERVE Ч+15 – 16 и `just perf` ([roadmap.md](roadmap.md), раздел 6.2); результаты пишутся в `configs/perf_budget.yaml`.

HTTP:
- подтверждение POST ≤ 0,5 с после последнего байта (запись потока, sha256 и fsync 29 МБ);
- GET жюри на origin ≤ 5 мс (готовый результат — FileResponse файла 0,3–1,5 МБ);
- опрос UI ≤ 2 мс на origin (одно чтение SQLite по индексу в WAL).

От конца загрузки до результата:

| Сценарий | Промежуточный (rules) | Финальный |
|---|---|---|
| Типичный ролик 15–25 с | ≤ 7–10 с | ≤ 25–35 с |
| Самый длинный ролик (бюджет на 145 с; Х-14 ≈ 142 с) | ≤ 25–30 с | ≤ 60–65 с |
| Пачка из 5 типичных роликов одновременно | — | последний ≈ 70–100 с |
| Пачка из 5 роликов по 145 с | — | последний ≈ 175–190 с |

Все задачи жюри должны завершаться за ≤ 240 с при лимите 300 с.

### 9.2. Бюджет по этапам

`configs/perf_budget.yaml`. `just perf` [macos] падает, если p95 любого этапа превышает бюджет больше чем на 20 %.

| Этап | Ролик 145 с | Типичный 20 с | Примечание |
|---|---|---|---|
| Запись загрузки + sha256 + fsync | ≤ 0,5 с | ≤ 0,5 с | Время передачи через туннель не входит |
| Лёгкий probe (API) | ≤ 0,05 с | ≤ 0,05 с | |
| Remux превью | ≤ 0,3 с | ≤ 0,3 с | Только UI, без перекодирования |
| Очередь → claim | ≤ 0,25 с | ≤ 0,25 с | Опрос 250 мс |
| Deep probe | ≤ 0,2 с | ≤ 0,2 с | |
| Декодирование, 3 fps | ≤ 6 с | ≤ 1 с | Декодируется каждый кадр (GOP 1 с, B-кадры); BGR только для ≈ 1 из 10 |
| Detect + track | ≤ 15 с | ≤ 3 с | Core ML ANE, статический batch 1, 864x480 |
| Pose | ≤ 8 с | ≤ 1,5 с | С гейтингом |
| Crop cls | ≤ 2 с | ≤ 0,5 с | С гейтингом, один батч на кадр |
| Хранилище кадров | (≈ 1 с, параллельно) | | Поток записи JPEG |
| Scene + rules + verdict | ≤ 1 с | ≤ 0,2 с | |
| Уточнение (опционально) | ≤ 1 с | ≤ 0,5 с | ≤ 5 кандидатов |
| Evidence + describe + render + валидация | ≤ 1,5 с | ≤ 0,5 с | |
| **ИТОГО промежуточный** | **≤ 28 с** | **≤ 7 с** | |
| VLM | ≤ 30 с (макс. 35) | ≤ 20 с | ≤ 4 групповых вызова × 3–5 с (≤ 3 тыс. токенов промпта, ≤ 96 на выходе) + 1 keyframe pass по 12–16 кадрам половинного разрешения, 6–10 с |
| Финальный render | ≤ 0,5 с | ≤ 0,5 с | |
| **ИТОГО финальный** | **≤ 60–65 с** | **≤ 30 с** | |
| Только правила (деградация) | ≤ 25 с | | |

### 9.3. Уровни деградации

Лестница: perception full → fast → min; VLM полностью → частично → пропущен. Ответ по правилам есть всегда.

Уровень perception (`PerceptionLevel`) выбирается на задачу по EWMA из `perf_stats`, длительности, очереди впереди и `vision_target`:

| Уровень | Частота кадров | Pose | Crop cls | Когда |
|---|---|---|---|---|
| `full` | 3 fps | ≤ 2 fps в зонах стола, передачи, сейфа | 1,5 fps + триггеры | По умолчанию |
| `fast` | 1,5 fps | только в окнах кандидатов | только триггеры | Вторая попытка после сбоя; давление очереди или дедлайна |
| `min` | 1 fps | нет | да (detect + cls) | Крайний случай; задача, не взятая из очереди к t0 + 200 с, сразу идёт на `min` |

VLM-этап (см. шаг 14 в 4.2):
- ставится в очередь, только если `now + est_vlm + 15 с < vlm_start_cutoff` и хватает памяти;
- внутри этапа дедлайн `vlm_stop` проверяется на каждой группе и на каждом потоковом чанке;
- пачка длинных видео поэтому завершается «только по правилам», а не с опозданием.

Причины деградации (`Degradation.reason`), видны в UI и в `admin/status`:

| Причина | Смысл |
|---|---|
| `perception_fast`, `perception_min` | perception выполнен на пониженном уровне |
| `vlm_deadline` | VLM не успевал до `vlm_start_cutoff` или остановлен по `vlm_stop` |
| `vlm_memory` | свободной памяти меньше порога |
| `vlm_backlog` | очередь vlm worker переполнена |
| `vlm_crash` | vlm worker упал или завис, отдан промежуточный результат |
| `vlm_disabled` | `CG_VLM_ENABLED=false` |
| `deadline_finalize` | наступил `finalize_at`, результат финализирован sweeper или ленивым GET |
| `perception_truncated` | наступил `perception_hard_stop` до конца perception; правила по уже обработанным кадрам |

### 9.4. Пачка из 5 роликов по 145 с (оценка)

- Perception ≈ 25 с на задачу подряд: готово на ≈ 25, 50, 75, 100, 125 с после загрузки.
- VLM идёт следом ≈ 30 с на задачу: готово на ≈ 55, 85, 115, 145, 175 с.
- Последняя задача готова на ≈ 175–190 с. Если оценки промахнутся, задачи 4–5 пропустят VLM из-за `vlm_start_cutoff` на 180 с.
- Пропускная способность памяти (200 ГБ/с) общая для ANE и MLX, поэтому выигрыш от перекрытия проверяется `tests/mac/test_overlap_throughput.py`, а не предполагается.

### 9.5. Ускорители по движкам

- **CPU, декодирование.** PyAV `thread_type='AUTO'` (по документации PyAV ≈ в 5 раз быстрее SLICE). Сетка PTS, BGR только для выбранных кадров. VideoToolbox hwaccel за флагом: замерить один раз и оставить быстрый путь. Поток-производитель с очередью на 16 кадров перекрывает ANE. Если профилирование покажет > 20 % потерь на GIL — декодер в отдельный spawn-процесс с кольцом `shared_memory`.
- **ANE.** Статический batch 1, FP16, голова без NMS. Прямоугольный вход 864x480 (≈ на 44 % меньше вычислений, чем 864x864, для кадров 464x848). Ландшафтный вариант по ориентации. Только `CPU_AND_NE`. Прогрев при старте; обновления macOS в окне жюри запрещены (сбрасывают кэш компиляции ANE).
- **CPU, правила и evidence.** Правила — чистый Python по ≈ 435 `FrameObservation` на 145-секундное видео, < 1 с. Evidence из хранилища кадров: без повторного декодирования и seek. JPEG ≈ 5 мс на кадр.
- **GPU, VLM.** Thinking выключен, temperature 0, короткий JSON по схеме (≈ 15–40 токенов), основное время — prefill. Группировка кандидатов ±2 с в один prefill. Порядок групп по влиянию на вердикт. Кадры контекста в масштабе ≈ 0,75 (≈ 230 визуальных токенов вместо ≈ 405). Мелкие кропы — одной мозаикой, а не набором крошечных картинок (каждая растягивается до минимума 65 536 пикселей). Процессоры схем собраны при старте. Путь `video=` и кэширование префикса — только эксперименты, не зависимости.
- **Сеть и UI.** Однопроходная потоковая загрузка с хэшем на лету. `jury.json` рендерится один раз. GZip только для JSON (base64 в `jury.json` сжимается ≈ на 25 %, если клиент принимает gzip). В UI нет base64. Превью faststart с Range. Каталог один раз с ETag. ≈ 80 КБ gzip начального JS. Мгновенное локальное воспроизведение во время загрузки. Кэш результатов по (sha256, pipeline_version, config_hash) + дедупликация в работе: повторная загрузка того же файла — мгновенно.

### 9.6. Измерение

- Каждая задача пишет `time.perf_counter` по этапам в `jobs.timings_json`, плюс пиковый RSS и пик MLX. Они обновляют `perf_stats` и видны в UI и `admin/status`.
- `just perf` [macos], только в режиме SERVE (supervisor `--profile prod` под launchd): ролик 145 с + 3 типичных, по одному и с перекрытием, нагрузка через `http://127.0.0.1:8000` с ключом team. Проверяет бюджеты и ≥ 3 ГБ свободной памяти (формула — раздел 8). Отказывается работать, если запущен `cashguard.cli up --profile dev` или обучение.
- `just jury-sim URL KEY N`: N=5 с Windows-ПК через телефонную точку доступа и публичный URL (синтетические ролики), опрос каждые 3 с. Условия: все done ≤ 240 с от своего POST, ноль 5xx, схема валидна. Затем N=10 (из них ≥ 3 ролика по 145 с): каждая задача done ≤ 300 с от своего POST (цель ≤ 270 с), лишние — `result_level=rules` или `perception_min`, ноль 5xx. Дважды один файл — мгновенный кэш.

---

## 10. Надёжность

### 10.1. Гарантии, видимые жюри

| Гарантия | Как обеспечена |
|---|---|
| **После 200 на POST задача не теряется** | 200 только после fsync `input.mov` и COMMIT строки. Любая ошибка раньше — ошибка клиенту для повтора. Повтор попадает в дедупликацию по sha256: в работе — связанная строка, готово — мгновенный ответ из `result_cache` |
| **Ответ за 5 минут** | Дедлайны от `t0`. Промежуточный результат по правилам пишется всегда сразу после perception. Финализация из трёх мест одним идемпотентным CAS: воркер при успехе, sweeper каждые 2 с, ленивый GET (работает, даже если sweeper умер). Уровень perception понижается под давлением |
| **Отказ одного компонента не роняет ответ** | Изоляция процессов, аренды с двумя heartbeat, requeue, SIGKILL зависших (таблица 10.3) |
| **Память ограничена** | Раздел 8 |
| **Ответ строго по схеме** | Валидация каждого `jury.json` до публикации, безопасный fallback, фиксированные формы processing и failed, глобальные JSON-обработчики ошибок |

### 10.2. Механизмы

- **Долговечные задачи.** SQLite WAL — единственная очередь. Файлы задачи пишутся атомарно (tmp → fsync → `os.replace`) **до** смены статуса в БД. Миграции через `PRAGMA user_version`.
- **Аренды и два heartbeat.** Воркер держит аренду `(lease_owner, lease_pid)`:
  - `alive_at` — пишет отдельный поток каждые 2 с;
  - `progress_at` — пишет основной цикл на каждом кадре или шаге.
  - Sweeper возвращает задачу в очередь, если `psutil.pid_exists(lease_pid)` = False или `alive_at` старше 20 с. Каждый воркер при старте также подбирает осиротевшие задачи своей роли.
- **Попытки.** Perception — 2, вторая на уровне fast. VLM — 1; при сбое отдаётся промежуточный результат. «Ядовитый» файл быстро падает на deep probe с `UNSUPPORTED_VIDEO` и не повторяется бесконечно.
- **Уровни дедлайнов** — раздел 4.1. Python-таймаут не прерывает заблокированный вызов Core ML или Metal, поэтому бюджет проверяется кооперативно: на кадре, на группе VLM, на потоковом чанке. Зависшие процессы убивает supervisor (нет прогресса > 60 с при `busy`).
- **Ленивая финализация.** `GET /api/v1/jobs/{id}` после `finalize_at` при наличии промежуточного результата сам выполняет CAS-финализацию.
- **Безопасный fallback-рендер.** При ошибке валидации: перекодировать evidence, зажать таймкоды в [0, duration], ≤ 3 кадров, заново применить инвариант вердикта, снова валидировать.
- **Детерминизм.** temperature 0, версионированные пороги, сортированный вывод, слияние одного кода в пределах 10 с, ≤ 5 нарушений. `pipeline_version` и `config_hash` ставятся на каждый результат (только UI и admin).
- **Формат.** Только точные ключи. Алиасы со слэшем (никогда 307). POST отвечает 200, не 202. `timecode_sec` — Python float с округлением до 0,01. `image_base64` — сырой JPEG (`/9j/…`).

### 10.3. Изоляция сбоев

| Сбой | Обнаружение | Исход |
|---|---|---|
| Падение API | supervisor перезапускает за ≤ 5 с; 3 неудачные пробы `/health` — рестарт | Загрузки в этот момент получают 502 или reset, клиент повторяет. Задачи в очереди и в работе не затронуты: состояние в SQLite |
| Падение perception | pid аренды мёртв | Повтор как попытка 2 на уровне fast. Новый воркер прогревается из кэша компиляции ANE |
| Зависание perception | `progress_at` старше 60 с | SIGKILL (после проверки cmdline), дальше как при падении |
| Metal OOM, падение или зависание VLM | то же | Задача финализируется из промежуточного результата: `result_level=rules`, деградация `vlm_crash`. VLM пропускается, пока воркер прогревается |
| Перезагрузка Mac, сбой питания | LaunchAgents стартуют только после входа (FileVault) — пункт чек-листа | Задачи в работе возвращаются в очередь. Просроченные финализируются из промежуточного результата или получают failed, если его нет |
| Обрыв туннеля | cloudflared переподключается сам; supervisor проверяет metrics `/ready` и уведомляет | Обработка продолжается. Резерв Tailscale Funnel работает параллельно. Второй коннектор на тот же именованный туннель при живом Mac **не запускать**: коннекторы балансируют нагрузку, и опросы попадут на машину без задачи |
| Мало диска | проверка при загрузке | 507 на загрузке. Ежечасная очистка. Хранилища кадров удаляются после финала |
| Часы | — | Дедлайны — wall-clock epoch между процессами; бюджеты внутри процесса — monotonic |

### 10.4. Наблюдаемость

- `/health` — только liveness. `/ready` — строгая готовность с именами непройденных проверок.
- `/api/v1/admin/status` (ключ team).
- JSON-логи по процессам с `job_id`, ротация; `job_events` на задачу; тайминги этапов и пик памяти на задачу.
- `just status`: очередь, воркеры, память, туннель, последние задачи. `just trace <id>`: слияние логов одной задачи.
- Локальные уведомления через osascript. Точка готовности/деградации в подвале UI.

### 10.5. Защита контрактов от «упрощений» агентов

- `tests/api/test_auth_before_body.py` проверяет, что итератор тела не потребляется при 401. Это блокирует замену на `UploadFile`.
- Контрактные тесты и schemathesis (нет 5xx, ключ на каждом маршруте `/api/v1`) — до каждого слияния.
- import-linter запрещает ML-импорты в `api`, `jobs`, `core`, `contracts`.
- Тесты проверяют, что: yaml трекера передаётся явно; таймкоды из PTS (синтетический VFR-фикстур); поворот применяется (синтетический повёрнутый фикстур).
- Закреплённые версии и запрещённые обновления — в `AGENTS.md`.

### 10.6. Репетиции

Чек-лист окна жюри — [jury_mode.md](jury_mode.md), эксплуатация Mac — [runbook_mac.md](runbook_mac.md). Кратко:
- Заморозка (Ч+29,5–30, [roadmap.md](roadmap.md)): тег `jury-v1`; `uv sync --locked --extra ml --extra mac`; `pnpm build`; проверка манифеста моделей; сверка результатов eval-full (recall по видео 1.0), perf, холодного старта без сети и jury-sim на коммите-кандидате; `CG_RETENTION_HOURS=0` и профиль UI «окно жюри»; `gen-key` для ключей jury и team (человек).
- T−2 ч (от начала окна жюри): питание от сети, крышка открыта, Ethernet; Low Power Mode выключен; автообновления выключены; Time Machine на паузе; все прочие приложения закрыты, сессии Claude Code и Codex на Mac закрыты; перезагрузка и ручной вход.
- T−1 ч: `just services-restart`, дождаться `/ready`, один прогревочный ролик; с телефона через мобильную сеть: `/health` 200, POST без ключа → 401, curl из ТЗ доходит до done, одна загрузка через UI работает.
- Во время окна: руки прочь, смотреть `just status`.
- После окна: очистить `var/`, сменить ключ жюри.
- Внесение сбоев, дважды: `kill -9` perception посреди задачи, затем VLM; SIGSTOP perception; отключить сеть на 30 с; 10 параллельных загрузок; загрузка .txt (ожидается 415); повёрнутый HEVC-ролик с телефона; ролик 145 с внутри пачки; перезагрузка с входом.

### 10.7. Резерв

- Docker-образ Linux CUDA проверен в режиме «только правила» на Windows-ПК с RTX 3050.
- Запускать **только** если Mac мёртв и его коннектор туннеля остановлен.
- Адрес Tailscale Funnel сдаётся как резервный.

---

## 11. Безопасность и гигиена данных

Доступ и периметр:
- Ключи: `secrets.token_urlsafe(32)` — один для jury, один для team. Хранятся в `~/.config/cashguard/.env` (chmod 600). Генерация: `just gen-key`.
- Проверка ключа: sha256 + `hmac.compare_digest` против каждого ключа, без раннего выхода; **до** чтения тела ([ADR-0004](adr/0004-auth-before-body-streaming-upload.md)).
- Логи пишут имя principal (`jury`, `team`, `ui`), никогда сам ключ, base64 или кадры.
- Привязка только к `127.0.0.1`. IP клиента из `CF-Connecting-IP`, доверие — только если пир `127.0.0.1`.
- Id задач — uuid4 hex, проверяются регуляркой. Пути строятся на сервере и проверяются `is_relative_to(var)`; кадры — `is_relative_to(job_dir/evidence)`.
- UI: token bucket на IP; ключ жюри никогда не ограничивается. Опциональный код доступа UI (cookie HttpOnly, SameSite=Strict), по умолчанию выключен (FAQ 27).
- `TrustedHostMiddleware` не используется: ошибка в списке хостов вернула бы жюри 400.
- Cloudflare: Bot Fight Mode выключен, Under Attack выключен, `/api` не кэшируется. Кадры отдаются с `Cache-Control: private`.
- `/robots.txt` с `Disallow: /`, `<meta name=robots content=noindex>` в `index.html`.

Данные (ТЗ §10, FAQ 6):
- `input.mov` жюри удаляется после perception. Хранилища кадров — после финального результата. Папки задач — через 24 ч; в окне жюри автоочистка выключена, папки удаляются вручную после окна. После мероприятия очищается всё.
- Видео жюри и UI никогда не попадают в `var/signals`, `data/`, обучение или оценку. Тестовые видео нельзя использовать для обучения, публиковать или передавать третьим лицам (ТЗ §10).
- `data/`, `var/`, `models/` (целиком, включая манифесты), `runs/`, `result_*.json` в `.gitignore`; хук `check-added-large-files` (800 КБ) блокирует видео и веса. Полный ответ jury API `scripts/smoke_jury.sh` пишет в `var/smoke/`, в терминал выводится только сводка.
- Не использовать пути iCloud или OneDrive.

Агенты и облако:
- В коде пайплайна нет вызовов облачных AI.
- Облачные агенты не открывают кадры датасета и жюри: правила deny в `.claude/settings.json` (`Read(./data/**)`, `Read(./var/**)`, `Read(./models/**)`, `Read(**/*.mov)`) и `AGENTS.md`. Трассы replay содержат только числа. Кадры смотрят люди.
- Разметка только локально (Label Studio), без облачных сервисов разметки.

Лицензии:
- Ultralytics AGPL-3.0 в сетевом сервисе обязывает публиковать полный исходный код. Организаторы разрешили YOLO (17.10.2026, см. [case_analysis.md](../analysis/case_analysis.md), раздел 4). Для коммерческого использования банком нужен открытый код или Enterprise-лицензия Ultralytics; альтернатива — RF-DETR (Apache-2.0). Ограничение пишется в README и [limitations.md](limitations.md).
- Колёса PyAV включают GPL x264/x265.
- У репозиториев mlx-community с Qwen нет карточки лицензии; происхождение Apache-2.0 записывается в манифест.
- Модели и датасеты с некоммерческими лицензиями не используются (FAQ 24, 34).

---

## 12. Локальность и перенос на Linux

### 12.1. Локальность (ТЗ §7)

- Окружение воркеров: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `YOLO_OFFLINE=1`, `YOLO_AUTOINSTALL=false`, заранее заполненный `YOLO_CONFIG_DIR`, `yolo settings sync=False`. Применяет `core/env.py` (`apply_offline_env`) и supervisor.
- Рамки рисует cv2, а не `Results.plot()` (тот скачивает шрифт Arial).
- Qwen грузится из локальной папки на закреплённой ревизии. Отсутствующий файл — громкая ошибка, а не скачивание.
- Воркеры при старте сверяют sha256 моделей с `configs/models.yaml` и отказываются работать при несовпадении.
- OpenTelemetry FastAPI выключен. В UI нет CDN, веб-шрифтов и аналитики.
- **Условие релиза:** curl из ТЗ на 3 видео с выключенным Wi-Fi (туннель лежит) проходит до done.

### 12.2. Перенос в контур банка (Linux + CUDA)

Тот же код, тот же lock, то же разделение на процессы. Отличаются только бэкенды за портами ([ADR-0012](adr/0012-contracts-first-ports-replay.md)).

| Аспект | Mac (прод хакатона) | Linux + CUDA (банк) |
|---|---|---|
| Упаковка | нативный uv venv | `deploy/linux/Dockerfile.cuda`: стадия сборки `ghcr.io/astral-sh/uv:0.13.1-python3.12-trixie-slim`, runtime `python:3.12-slim-trixie` (3.12.15) + libgl1, libglib2.0-0 |
| Процессы | launchd + supervisor | `deploy/linux/compose.yaml`: сервисы api, perception, vlm из одного образа; restart policy Docker; есть `deploy/linux/systemd/*.service` |
| Perception | `CG_VISION_BACKEND=coreml` (ANE) | `CG_VISION_BACKEND=torch`, `gpus: all` |
| VLM | `CG_VLM_BACKEND=mlx` | `CG_VLM_BACKEND=transformers` (transformers 5.20.1, extra `cuda`). Какие веса Qwen3.5-4B брать для transformers (не MLX-сборку) и на какой ревизии — проверить; записывается в `configs/models.yaml` |
| Декодирование | PyAV | PyAV (тот же код) |
| Транскод превью для HEVC/повёрнутых | `h264_videotoolbox` | VideoToolbox нет; способ транскода на Linux — проверить (в архитектуре не определён) |
| Хранилище | `~/cashguard/var` | общий bind mount `var/` (WAL работает между контейнерами на локальной ФС одного хоста) |
| Модели | `models/` | `/models` только на чтение |

Проверка переносимости: образ собирается и проходит режим «только правила» на Windows-ПК с RTX 3050 (`just docker-build`). Этот же образ — аварийный резерв (раздел 10.7).

---

## 13. Что сознательно не используем

| Не используем | Почему |
|---|---|
| Celery, RQ, arq, Redis | Лишний демон и память на 16 ГБ Mac; SQLite WAL даёт долговечность без них ([ADR-0003](adr/0003-sqlite-wal-queue-state.md)) |
| Huey (SqliteHuey) | Таймауты на SIGALRM не останавливают вызовы Metal |
| Procrastinate / Postgres | Лишний демон |
| SQLAlchemy, SQLModel, aiosqlite | Три таблицы понятнее агентам в сыром SQL |
| `multiprocessing.Queue` | Не долговечна; `qsize()` бросает исключение на macOS |
| FastAPI `BackgroundTasks`, один процесс со всеми моделями | Нет изоляции сбоев Metal, перезапуск API перезагружает Qwen |
| gunicorn, fork; `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` | Fork небезопасен после старта Metal и ObjC runtime |
| supervisord, honcho, pm2; по LaunchAgent на процесс | Два пути супервизии; launchd не обнаруживает зависания |
| Docker на macOS | Нет Metal и ANE в контейнере |
| `fastapi[standard]` | Лишние зависимости; явный список надёжнее |
| `UploadFile` / `Form` в обработчике загрузки, `await request.body()` | FastAPI разбирает форму до зависимостей: 29 МБ неаутентифицированных данных на диск до проверки ключа |
| `TrustedHostMiddleware` | Ошибка в списке хостов вернёт жюри 400 |
| jinja2 | Непроверенная зависимость; шаблоны — `str.format_map` |
| orjson, msgspec | Сериализация pydantic-core достаточна; `jury.json` рендерится один раз |
| SSE и WebSockets в v1 | Quick tunnel не поддерживает SSE; опрос раз в секунду выглядит живым ([ADR-0007](adr/0007-ui-polling-sse-deferred.md)) |
| Лишние поля в JSON жюри (legal_reference, signals) | Автоматический разбор может сломаться (FAQ 18, наша трактовка) |
| `Results.plot()` Ultralytics | Скачивает шрифт из сети |
| `ComputeUnit.ALL`, `CPU_AND_GPU`, инференс YOLO через `device=mps` | YOLO займёт GPU, нужный Qwen; ALL и CPU_AND_GPU падают из-за ошибки MPSGraph |
| Трекер по умолчанию (TrackTrack в 8.4.x), ReID по внешности | Только явный BoT-SORT без ReID; лица не распознаются (FAQ 29) |
| Таймкоды из `frame_index / fps`, OpenCV `VideoCapture`, seek PyAV для evidence | VFR, B-кадры, edit list, moov в конце — время «плывёт» ([ADR-0011](adr/0011-pts-timecodes-frame-store.md)) |
| Только VLM, без правил | Грубые таймкоды, нестабильные типы, пропуск мелочей ([ADR-0010](adr/0010-qwen-vlm-bounded-verifier.md)) |
| Qwen3.5-9B | Нарушает правило ≥ 3 ГБ свободной памяти |
| Режим thinking, свободный текст + regex, `mlx_vlm.server` по HTTP | Медленно, непредсказуемо, лишний сервер |
| Свободная генерация описаний VLM | ТЗ требует описание без неподтверждённых предположений; шаблон из фактов |
| RF-DETR в проде на Mac | Конкурировал бы с MLX за GPU; остаётся опциональным конкурентом в обучении |
| Quick tunnel (trycloudflare) для жюри, ngrok free, проброс портов, VPS-прокси | Нестабильный адрес, нет SSE; лишние зависимости и риски ([ADR-0013](adr/0013-public-ingress-tunnel.md)) |
| react-router 8, MSW 3, TypeScript 7, pnpm 12, orval, hey-api | Риски совместимости; TS 7 ломает openapi-typescript ([ADR-0014](adr/0014-frontend-stack.md)) |
| CDN, веб-шрифты, аналитика в UI | Офлайн-контур банка |
| Облачная разметка (Roboflow и т. п.) | Данные хакатона нельзя выгружать (FAQ 6) |
| Распознавание лиц | Запрещено (FAQ 29) |
| Звук | Анализ строго визуальный (FAQ 28) |

---

## 14. Структура репозитория

Сокращённое дерево. Полное дерево с назначением каждой папки, соглашения об именах и порядок добавления типа нарушения — [structure.md](structure.md).

Метки владельцев: [S0] интегратор, [S1] платформа и оценка, [S2] веб, [S3] perception и обучение (Mac), [S4] сцена и правила A, [S5] правила B, вердикт, нормы, [S6] VLM, эксплуатация Mac, сигналы, [GEN] генерируется, руками не править.

```
datathon/                                  корень (Windows: C:\Users\zhasy\datathon; Mac: ~/cashguard)
├─ AGENTS.md                               [S0] канонические правила агентов (<150 строк, <32 KiB)
├─ CLAUDE.md                               [S0] строка 1 "@AGENTS.md", дальше заметки только для Claude
├─ README.md                               [S0] для жюри (ТЗ §8): URL UI/API, curl, ключ, запуск, архитектура, модели, ограничения, AGPL
├─ LICENSE                                 AGPL-3.0
├─ pyproject.toml  uv.lock  .python-version [S0] Python 3.12; extras ml/mac/cuda; группа dev; конфиги ruff/basedpyright/importlinter/pytest
├─ justfile                                [S0] setup dev up check test schemas eval-replay loso extract-signals* sync-signals
│                                               eval-full* perf* robustness* jury-sim e2e build-web deploy* services-* mode*
│                                               train-a* train-c* status trace gen-key docker-build   (* = [macos])
├─ .pre-commit-config.yaml .gitattributes (eol=lf) .gitignore (+var/, .claude/worktrees/) .editorconfig .env.example
├─ .claude/settings.json                   [S0] permissions.deny Read(./data/**), Read(./var/**), Read(./models/**), Read(./runs/**/*.jpg), Read(**/*.mov), Read(**/*.mp4)
├─ src/cashguard/
│  ├─ contracts/                           [S0] чистые типы; ничего внутреннего не импортирует
│  │  ├─ jury.py ui.py jobs.py video.py perception.py scene.py findings.py catalog.py health.py
│  │  ├─ ports.py                          Protocol: VideoSource, PerceptionBackend, FrameStore, Rule, SignalRule, VlmBackend, ProgressFn, Clock
│  │  ├─ export.py                         python -m cashguard.contracts.export [--check]
│  │  └─ schemas/                          jury_job.schema.json (ЗАМОРОЖЕН) + generated/*.schema.json [GEN]
│  ├─ core/                                [S1] settings logging paths ids budget atomic memory env versions
│  ├─ jobs/                                [S1] db repo cache perfstats retention
│  ├─ api/                                 [S1] без ML (av разрешён)
│  │  ├─ main.py                           create_app(), lifespan, JSON-обработчики ошибок, GZip, /assets + index
│  │  ├─ deps.py upload.py ingest_service.py ratelimit.py sweeper.py views.py preview.py
│  │  └─ routes/                           jury.py (/api/v1) ui.py (/api/ui) health.py (/health, /ready) admin.py
│  ├─ workers/                             [S1] __main__.py base.py perception_worker.py warmup.py supervisor.py ; vlm_worker.py [S6]
│  ├─ pipeline/
│  │  ├─ runner.py                         [S0] оркестрация этапов, режимы stub|replay|full (сигнатуры заморожены на P0)
│  │  ├─ stub.py                           [S1] детерминированный фейковый результат
│  │  ├─ ingest/                           [S1] probe.py preview.py
│  │  ├─ decode/                           [S3] pyav_source.py frame_store.py window.py
│  │  ├─ perception/                       [S3] orchestrator.py plan.py gating.py crops.py trace_io.py export.py (новый путь: экспортёр сервиса)
│  │  │  └─ backends/                      coreml.py torch.py replay.py stub.py null.py
│  │  ├─ scene/                            [S4] zones roles sessions states events presence build
│  │  ├─ rules/
│  │  │  ├─ base.py registry.py context.py [S0→S4] @register_rule, pkgutil discovery, помощники
│  │  │  ├─ leave/                         [S4] pc_unlocked safe_key_left valuables_left safe_open left_during_service unattended_presence
│  │  │  ├─ access/                        [S4] door_not_closed outsider_in_cash_room third_person_during_service storage_unescorted
│  │  │  ├─ cashops/                       [S5] prev_cash_on_desk mixed_client_cash no_client_id recount_without_client cash_withheld
│  │  │  │                                      no_auth_check payout_without_recount change_before_prepared cash_concealed cash_transfer_outside_op
│  │  │  ├─ collection/                    [S5] collector_not_verified bag_cash_no_recount
│  │  │  ├─ discipline/                    [S5] personal_items food_drink phone_cashier
│  │  │  └─ signals/                       [S6] по файлу на код SIG_* (13)
│  │  ├─ verdict/                          [S5] scoring.py aggregate.py decide.py
│  │  ├─ vlm/                              [S6] questions grouping schema keyframes quant prompts/*.yaml
│  │  │  └─ backends/                      mlx.py transformers.py replay.py null.py
│  │  ├─ evidence/                         [S3] select.py draw.py jpeg.py
│  │  ├─ legal/                            [S5] catalog.py describe.py
│  │  └─ render/                           [S1] jury.py (валидация + fallback) ui.py
│  ├─ eval/                                [S1] labels run_eval metrics loso robustness perf jury_sim report
│  └─ cli.py                               [S1] cashguard: up, worker, export-schemas, extract-signals, eval, bench, gen-key, purge
├─ configs/                                см. раздел 5.12
├─ training/                               [S3] СУЩЕСТВУЮЩИЙ; свой .venv-train; сервис его не импортирует
│  ├─ README.md setup_mac.sh download_models.sh requirements.txt
│  ├─ configs/                             classes.yaml pipeline.yaml train.yaml external_datasets.example.yaml
│  ├─ kassa_train/                         common.py weights.py
│  └─ scripts/                             00_check_env … 12_train_cls; 13_train_rfdetr (запланирован)
├─ web/                                    [S2] package.json pnpm-lock.yaml .node-version vite.config.ts … index.html (lang=ru, noindex) AGENTS.md
│  ├─ public/                              favicon.svg robots.txt
│  ├─ src/                                 main.tsx App.tsx pages/ features/ shared/ (api [GEN], ui, lib, i18n/ru.ts) styles/
│  └─ e2e/                                 upload.spec.ts result.spec.ts mobile.spec.ts fixtures/ (только синтетика)
├─ tests/
│  ├─ contract/                            [S0] замороженная схема, дрейф OpenAPI, цитаты норм дословно, полнота каталога
│  ├─ api/                                 [S1] auth-before-body, 401/404/413/415/422/429/507, слэш, жизненный цикл, 5 параллельно, dedupe, lazy finalize, schemathesis
│  ├─ jobs/ workers/                       [S1] гонка claim, истечение аренды, идемпотентность CAS, kill -9 → всё равно done, падение VLM → отдан промежуточный
│  ├─ unit/                                core/ jobs/ scene/[S4] rules/<CODE>_test.py[владелец] verdict/ legal/[S5] evidence/[S3] render/[S1] vlm/[S6]
│  ├─ mac/                                 [S3/S6] marker mac: паритет Core ML, размещение ANE, офлайн-загрузка MLX, ≥3 ГБ свободно, перекрытие ANE‖GPU
│  └─ fixtures/                            timelines/<CODE>_{pos,neg}_*.json, jury_example.json (ТЗ), synthetic_video.py (testsrc 464x848: H.264, HEVC, повёрнутый)
├─ eval/baselines/main.json                [S1] закоммиченный базовый уровень метрик
├─ deploy/
│  ├─ macos/                               [S6] launchd/kz.cashguard.{supervisor,tunnel,caffeinate}.plist.tmpl install.py
│  ├─ cloudflared/config.yml.tmpl          [S6] UUID туннеля, http2, metrics, ingress → 127.0.0.1:8000, http_status:404
│  └─ linux/                               [S1] Dockerfile.cuda compose.yaml systemd/*.service
├─ scripts/                                [S1/S6] port.py gen_key.py make_synthetic_mov.py sync_signals.py smoke_jury.sh bench_mac.py purge_var.py render_docs.py
├─ docs/                                   README.md[S0] architecture.md[S0] structure.md[S0] pipeline.md[S0+] api.md[S1] ui.md[S2]
│                                          violation_catalog.md[S0, по PR S4/S5/S6] violation_types.md[GEN] legal_sources.md[S5] limitations.md[S0]
│                                          runbook_mac.md[S6] jury_mode.md[S6] training.md[S3] eval.md[S1] roadmap.md[S0] adr/0001…0014-*.md[S0]
├─ analysis/                               существующее исследование, не меняется: case_analysis.md detection_design.md labels_typed.csv normative/*.txt
├─ data/    (gitignored, только Mac)       raw/open/videos, frames/, prelabels/, labels/, yolo/{pseudo,reviewed}, crops/
├─ models/  (gitignored целиком)           pretrained/*.pt, hf/Qwen3.5-4B-{8,6,4}bit/, detector|pose|cls_screen|cls_safe_lock|cls_door/vNNN/{best.pt,*.mlpackage,manifest.json}
├─ runs/    (gitignored)                   запуски обучения Ultralytics
└─ var/     (gitignored)                   db/cashguard.sqlite3(-wal,-shm), jobs/<id>/…, signals/, eval/, logs/, yolo/
```

Предусловие: ветка `training-plan` (`training/` с `kassa_train` и скриптами) влита в `main` до P0.

---

## 15. Расхождения с текущим кодом training/

Код `training/` пишет живая сессия обучения; после мержа P0-S0-01 его ведёт S3, и только аддитивными правками по согласованию с автором. Скрипты `training/scripts/00–12` и `run_stage_*.sh` не переписываются. Расхождения с этой архитектурой закрывает экспортёр сервиса `src/cashguard/pipeline/perception/export.py` (новый путь) и небольшие аддитивные PR ([roadmap.md](roadmap.md), P0-S3-02, P0-S3-04, P1-S3-05).

| Где | Сейчас | Как закрываем | Источник цели |
|---|---|---|---|
| `training/scripts/10_export_coreml.py` | `nms=True` для detect, без `quantize` (точность выбирает экспорт), один размер входа | скрипт не меняется; выпуск для сервиса — экспортёр сервиса: `nms=False` (голова YOLO26 без NMS), FP16 (имя аргумента в ultralytics 8.4.179 — проверить), batch 1 | [ADR-0009](adr/0009-perception-neural-engine.md) |
| `10_export_coreml.py` | pose по умолчанию imgsz 640; квадрат, если не задан `--imgsz H W` | экспортёр сервиса: detect и pose 864x480 и 480x864; cls 224 | ADR-0009 |
| `10_export_coreml.py` | `models/<task>/<name>.mlpackage` + общий `models/manifest.json` | экспортёр сервиса пишет `models/<role>/vNNN/{best.pt,*.mlpackage,manifest.json}`, роли detector, pose, cls_screen, cls_safe_lock, cls_door; продвижение через `configs/models.yaml`. Выход C4–C5 сервис игнорирует | раздел 14 |
| Запуск экспорта | `run_stage_c.sh` выполняет C4–C5 в `.venv-train` | C4–C5 остаются как есть; экспорт для сервиса и паритет — интерпретатором сервиса `.venv/bin/python` в режиме DEV; паритет `yolo val` ≤ 1 mAP50; MLComputePlan | [ADR-0001](adr/0001-repository-shape.md) |
| `training/requirements.txt` | `ultralytics>=8.4.179`, `torch>=2.7`, numpy без пина, `transformers>=5.0` | аддитивный PR P0-S3-02: точные пины ultralytics/torch, как в `uv.lock`; numpy 2.3.5 | ADR-0001 |
| `training/download_models.sh` | без `--revision`, без 6-bit, без sha256 | аддитивный PR P0-S3-02: ревизии 8-bit `1704583a`, 6-bit `fbb1092a`, 4-bit `0e7ffd5c`; загрузка 6-bit; sha256 | раздел 7 |
| `training/configs/train.yaml` | `workers: 4` (MPS молча ставит 0); `batch: 8` для yolo26l на 864 | при свопе — batch 4 или `PROFILE=full_m`; выбор чекпойнта по независимому `yolo val`; сначала smoke на yolo26n | раздел 17 |
| `training/configs/train.yaml`, `cls.common` | нет `erasing`, `auto_augment` | аддитивный PR P1-S3-05: `erasing: 0.0`, `auto_augment: null` | — |
| `11_extract_crops.py` | фрагмент масштабируется до `min_side`, без letterbox | скрипт не меняется; `pipeline/perception/crops.py` повторяет `data/crops/crop_spec.json` один к одному; letterbox — только при `letterbox: true` в crop_spec | [training.md](training.md), раздел 11 |
| `training/scripts/13_train_rfdetr.py` | файла нет | опциональный конкурент (ночью), не на критическом пути; номер согласовать с сессией обучения | раздел 7.2 |

---

## 16. Связь с критериями оценки ТЗ

| Критерий (ТЗ §9) | Баллы | Что в архитектуре на него работает |
|---|---|---|
| Качество обнаружения | 25 | Правила с τ под LOSO recall 1.0; VLM только понижает; промежуточный результат — надмножество, безопасное для recall |
| Качество локализации | 15 | Таймкоды из PTS; evidence из хранилища кадров (метка = реальный кадр); `timecode_policy` по коду; опциональное уточнение ±1 с на 10 fps |
| Качество классификации | 15 | Тип задаёт правило, а не VLM; стабильные `title_ru` из каталога |
| Обоснованность правил | 15 | Нормы только из раздела 7 [case_analysis.md](../analysis/case_analysis.md); цитаты сверяются с `analysis/normative` тестом; формулировки «может свидетельствовать» для косвенных норм; утратившие силу акты не цитируются |
| Устойчивость | 10 | Зоны по объектам; экспорт портрет/ландшафт; LOSO с отложенной сценой L4; `just robustness`; своя съёмка с других ракурсов |
| Готовность продукта | 10 | Прогрессивный UI, мгновенное превью, Range-видео, кадры с лайтбоксом, нормы в карточках; долговечные задачи и дедлайны |
| Техническое решение | 10 | Три процесса на трёх движках, порты и бэкенды, офлайн-окружение, Docker для Linux + CUDA, ADR |

---

## 17. Риски и открытые вопросы

Полный перечень ограничений — [limitations.md](limitations.md). Главные архитектурные риски:

| Риск | Смягчение |
|---|---|
| Все цифры производительности и памяти — оценки (perception ≈ 25 с и VLM ≈ 30 с на 145 с; ≈ 405 визуальных токенов на кадр; ≈ 6–6,5 ГБ на 8-bit) | bench_mac предварительно в Ч+3,75 – 4,75, окончательно в SERVE Ч+15 – 16 ([roadmap.md](roadmap.md), раздел 6.2); `deadlines.yaml`, `perf_budget.yaml` и квантизация — по замерам. Если detect + pose не укладываются — ужесточить гейт позы и снизить до 2 fps (только конфиг) |
| Общая пропускная способность памяти ANE и MLX | `tests/mac/test_overlap_throughput.py`; лестница дедлайнов всё равно гарантирует ответ по правилам |
| Мало памяти на 16 ГБ; psutil занижает wired-память MLX | 8-bit только по замеру `just perf` в SERVE (≥ 3 ГБ на пике), иначе 6-bit; лимиты MLX, авто-квантизация, гейт ≥ 3,0 ГБ перед VLM, рециклинг perception, ничего лишнего во время проверки (агентские сессии на Mac закрыты) |
| Хрупкий экспорт Core ML: coremltools 9.0 проверен на torch ≈ 2.7/2.8, а lock — 2.14.1; numpy строго 2.3.5; операции YOLO26 могут уйти с ANE на CPU; обновление macOS сбрасывает кэш ANE | Изолированное окружение экспорта на torch 2.8; паритет ≤ 1 mAP50; MLComputePlan; прогрев до `/ready`; автообновления выключены |
| Дрейф версий между `.venv-train` и сервисом; источники расходятся, какая версия ultralytics последняя стабильная (8.4.178 или 8.4.179) | Один точный пин в обоих окружениях; экспорт только из venv сервиса; версии в манифесте модели; откат обоих на 8.4.178 при регрессии |
| Обучение на MPS: утечка памяти и своп на M2 Pro 16 ГБ; mAP валидации в цикле может отличаться от отдельного `val` | batch 4 при свопе; чекпойнт по независимому `yolo val`; smoke на yolo26n |
| Другой ракурс в финале — главный риск точности | Зоны по объектам, две ориентации, аугментации, LOSO, своя съёмка (FAQ 7). Recall на незнакомой раскладке всё равно может упасть |
| Неизвестные типы нарушений (FAQ 9) | Keyframe pass по умолчанию только в сигналы; `OTHER_VISIBLE_IRREGULARITY` в вердикт — только с подтверждением треками и выше высокого τ |
| Ложные срабатывания на «похожих» чистых роликах (Х-8, Х-10, Х-43, Х-45, Х-5, Х-14) | τ под recall 1.0, контроль срабатываний на чистых в eval-гейте, неоднозначное — в сигналы, понижение VLM |
| Юридическая обоснованность: ряд норм косвенные; для FOOD_DRINK прямой нормы нет; трактовка NO_AUTH_CHECK спорна (ПКО п.41 допускает машинку с ≥ 2 защитными признаками) | Формулировка «может свидетельствовать», честное `strength=none`, тест дословных цитат, не цитировать утратившие силу НБРК № 58/2001, № 250/2012, № 48/2018, № 254/2012 |
| Неизвестна семантика автоматического разбора жюри | Умолчания: русские названия, null и [] при processing, 200 везде, норма в description, без лишних ключей. Подтвердить на grill-me и, если можно, у организаторов |
| Туннель и сеть: нужен домен; лимиты Funnel не документированы; площадка может блокировать UDP; время загрузки жюри входит в 300 с; Bot Fight Mode может блокировать curl | http2, резерв Funnel, дедлайны от t0 с запасом 60 с, jury-sim с телефонной точки |
| Эксплуатация Mac: сон, FileVault после перезагрузки, автообновления, память под Chrome/IDE | Чек-лист jury_mode, caffeinate, питание и Ethernet, человек рядом с Mac, две репетиции со сбоями |
| Агенты «упрощают» критичное: `UploadFile`, пропуск `tracker=`, таймкоды из индекса кадра, SSE вместо опроса, обновление TS 7 / react-router 8 / pnpm 12 | Отдельный тест на каждое, import-linter, пины и запреты в `AGENTS.md` |
| Конкуренция за GIL в perception | Замерить; при потере перекрытия > 20 % — декодер в отдельный процесс |
| Единая точка отказа — Mac | Резерв: Docker CUDA «только правила» на RTX 3050, запуск только после остановки коннектора Mac |

Открытые вопросы (grill-me), в скобках — умолчание:
1. Есть ли домен для именованного туннеля? [купить дешёвый; иначе Funnel основной]
2. Нужен ли код доступа в UI? [нет; лимиты по IP]
3. `type` для жюри: русские названия или коды? [русские]
4. Норма внутри `description`? [да]
5. Влияют ли находки keyframe pass на вердикт? [только с подтверждением и выше высокого τ]
6. Кто человек-интегратор?
7. Кто физически дежурит у Mac и есть ли Ethernet?
8. Публиковать ли репозиторий из-за AGPL? [да, после мероприятия; без секретов и данных]
9. Можно ли агентам смотреть кадры датасета (вопрос организаторам)? [по умолчанию нет]
10. Имя продукта и пакета [CashGuard / `cashguard`]

---

## 18. Допущения

- Прод и обучение — MacBook Pro M2 Pro 16 ГБ на macOS ≥ 14 (15 рекомендуется ради `mx.set_wired_limit`). `sw_vers` проверяется на P0: torch 2.14, mlx 0.32 и av 19 имеют колёса только для macOS 14+ arm64.
- В окно жюри Mac выделен: питание от сети, крышка открыта, по возможности Ethernet, работают только сервис и туннель, рядом человек, который войдёт после перезагрузки.
- Тестовые видео похожи на обучающие: .mov сверху, H.264 High ≈ 29,76–30 fps VFR, 464x848 портрет, moov в конце, без поворота, 11–145 с, ≈ 29 МБ. Финальное видео с другого ракурса может отличаться ориентацией или кодеком — это покрывают ландшафтный экспорт и транскод.
- Жюри вызывает ровно пути ТЗ §10 с `X-API-Key`, шлёт multipart-поле `file` как curl, опрашивает GET до done и считает 5 минут от POST. Может прийти 5 и больше видео сразу (FAQ 12).
- Умолчания до grill-me: `title_ru` как `type`, норма в `description`, `verdict` null и `violations` [] при processing/failed, HTTP 200 на POST, без лишних ключей.
- Домен для именованного туннеля есть к концу P0. Иначе основным становится Tailscale Funnel, и через него проверяются 5 параллельных загрузок по 29 МБ.
- Ветка `training-plan` влита в `main` до P0; раскладка данных и моделей (`data/raw/open/videos`, `models/pretrained`, `models/hf`) остаётся как есть.
- Люди проверяют ≈ 600–800 ключевых кадров в Label Studio (3–5 человеко-часов) параллельно с агентами. Агенты и облачные сессии кадры не смотрят.
- Grounding DINO base доступен под Apache-2.0 только для пред-разметки (проверить карточку). Веса и датасеты с некоммерческими лицензиями не используются (FAQ 34).
- Windows-ПК переведён на Node 24.22.0 и pnpm 10.34.x, uv 0.13.1; worktree лежат на C: рядом с кэшем uv.
- UI и объяснения — на русском (FAQ 26 оставляет выбор команде). UI открыт с лимитами по IP (FAQ 27); ключ жюри защищает только `/api/v1`.
- Предобученные веса — ultralytics/assets v8.4.0; репозитории Qwen MLX закреплены на ревизиях `1704583a`, `fbb1092a`, `0e7ffd5c`. После загрузки интернет для обработки не нужен.
- Машинное время критического пути — 14–18 ч ([detection_design.md](../analysis/detection_design.md)); календарь команды — около 40 ч ([roadmap.md](roadmap.md), раздел 6). RF-DETR-M — опционально, не на критическом пути (пред-разметка → проверка людьми → обучение на Mac → пороги LOSO).
- SQLite в Python 3.12 от uv поддерживает RETURNING (≥ 3.35) на всех трёх платформах — проверить. `var/` на локальной, не синхронизируемой ФС.

---

## 19. Связанные документы

| Документ | О чём | Владелец |
|---|---|---|
| [README.md](README.md) | Индекс документации и соответствие разделу 8 ТЗ | S0 |
| [adr/README.md](adr/README.md) | Индекс архитектурных решений 0001–0014 | S0 |
| [structure.md](structure.md) | Полное дерево репозитория, имена, генерируемые файлы, как добавить тип нарушения | S0 |
| [pipeline.md](pipeline.md) | Поэтапная спецификация пайплайна: приём, perception, сцена, правила, вердикт, VLM, evidence, рендер | S0, разделы S1, S3–S6 |
| [api.md](api.md) | Полный контракт API жюри и UI, коды ошибок, примеры | S1 |
| [ui.md](ui.md) | Экраны, состояния, компоненты веб-интерфейса | S2 |
| [violation_catalog.md](violation_catalog.md) | Все коды нарушений и сигналов: название для жюри, признаки, правило, таймкод, норма | S0 (правки по PR S4, S5, S6) |
| `violation_types.md` | Перечень типов для жюри, генерируется `scripts/render_docs.py` из `configs/catalog/` (появится позже) | GEN |
| [legal_sources.md](legal_sources.md) | Нормативные источники и принцип сопоставления с видео | S5 |
| [limitations.md](limitations.md) | Ограничения и известные ошибки | S0 |
| [runbook_mac.md](runbook_mac.md) | Эксплуатация Mac: установка сервисов, режимы, туннель | S6 |
| [jury_mode.md](jury_mode.md) | Чек-лист окна проверки жюри | S6 |
| [training.md](training.md) | Обучение и экспорт моделей | S3 |
| [eval.md](eval.md) | Оценка, LOSO, jury-sim | S1 |
| [roadmap.md](roadmap.md) | Фазы P0–P5, гейты G0–G5 (каноническая нумерация; соответствие нумерации G0–G3 синтеза архитектуры — roadmap, раздел 3), календарь | S0 |
| [../analysis/case_analysis.md](../analysis/case_analysis.md) | Анализ кейса, раздел 7 — проверенные нормы РК | — |
| [../analysis/detection_design.md](../analysis/detection_design.md) | ML-стек, правила по типам, покрытие 55 пунктов | — |
| [../training/README.md](../training/README.md) | Этапы обучения A–D | S3 |
