# ADR-0002. Три процесса под кроссплатформенным Python-супервизором, которого держит launchd

> Назначение: зафиксировать модель процессов сервиса и способ их супервизии на Mac, Windows и Linux.
> Владелец: S0 (документ); реализуют S1 (`workers/supervisor.py`, `workers/base.py`) и S6 (`deploy/macos/`). Статус: черновик v1, 17.10.2026.

## Статус

Принято.

## Контекст

- У M2 Pro три вычислительных блока: Neural Engine, GPU и CPU. YOLO работает на ANE через Core ML, Qwen — на GPU через MLX, декодирование и правила — на CPU.
- Metal и MLX могут упасть по OOM или зависнуть внутри C-вызова. Python-таймаут такой вызов не прерывает. Остановить его можно только убийством процесса.
- Загрузка Qwen занимает 10–30 с. Перезапуск API не должен её вызывать.
- `fork` небезопасен после инициализации Metal и ObjC runtime.
- launchd умеет перезапускать упавший процесс, но не видит зависаний.
- Тот же запуск нужен на Windows-ПК разработки и на Linux в контуре банка.
- Жюри может прислать 5 и больше видео сразу (FAQ 12). Перекрытие задач важно для лимита 5 минут.

## Решение

1. **Три процесса**, каждый — свежий интерпретатор:
   - `api`: `uvicorn cashguard.api.main:app --host 127.0.0.1 --port 8000 --workers 1 …`. Без ML-импортов (`av` разрешён).
   - `perception`: `python -m cashguard.workers --role perception`. Core ML на ANE + CPU.
   - `vlm`: `python -m cashguard.workers --role vlm`. MLX на GPU, модель только из главного потока.
2. **Supervisor** `src/cashguard/workers/supervisor.py` (около 150 строк, stdlib + psutil, без веб- и ML-импортов), запуск `cashguard up --profile prod|dev`:
   - применяет офлайн-окружение;
   - запускает детей через `subprocess.Popen([sys.executable, '-m', ...])`, никогда не делает fork; каждая точка входа вызывает `multiprocessing.set_start_method('spawn')`;
   - порядок старта: api, perception, vlm;
   - рестарт упавшего с backoff 1, 2, 4 … 30 с;
   - SIGKILL воркера, если `alive_at` старше 20 с, или `progress_at` старше 60 с при `busy`, или старше 300 с при `warming`; перед SIGKILL сверяется cmdline процесса;
   - проба `/health` API каждые 5 с, рестарт после 3 неудач;
   - проба metrics `/ready` cloudflared каждые 15 с, при сбое — лог и уведомление osascript;
   - при остановке пересылает SIGTERM и ждёт 30 с.
3. **Mac: три LaunchAgents** в `gui/<uid>`, шаблоны в `deploy/macos/launchd/*.plist.tmpl`, установка `just services-install`:
   - `kz.cashguard.supervisor`: `~/cashguard/.venv/bin/python -m cashguard.cli up --profile prod`; абсолютные пути, никогда `uv run`; RunAtLoad, KeepAlive, ProcessType=Interactive, ThrottleInterval 5, ExitTimeOut 30, NumberOfFiles 8192;
   - `kz.cashguard.tunnel`: cloudflared ([ADR-0013](0013-public-ingress-tunnel.md));
   - `kz.cashguard.caffeinate`: `/usr/bin/caffeinate -ims`.
4. **Linux:** `deploy/linux/compose.yaml` — сервисы api, perception, vlm из одного образа; restart policy Docker вместо цикла супервизора; эквивалентные юниты в `deploy/linux/systemd/`.
5. **Dev:** тот же supervisor с `--profile dev`: api с `--reload` на порту worktree, один воркер `--role all` с бэкендами stub/replay.
6. Воркеры — одна задача за раз. Perception сам перезапускается между задачами при RSS > 3 ГБ или после 200 задач.

## Рассмотренные альтернативы

| Альтернатива | Почему отклонена |
|---|---|
| Один процесс со всеми моделями | Metal OOM роняет всё, включая приём загрузок. Перезапуск API перезагружает Qwen. Нет перекрытия задач на разных движках |
| FastAPI `BackgroundTasks` | Те же проблемы. Задачи живут в процессе API и теряются при его рестарте |
| LaunchAgent на каждый процесс плюс отдельный watchdog-агент | Два пути супервизии: launchd на Mac и свой в dev. Не переносится на Windows и Linux. launchd сам не видит зависаний |
| supervisord, honcho, pm2 | Внешняя зависимость. Не знают про heartbeat в SQLite и не убивают процесс по отсутствию прогресса |
| gunicorn с pre-fork | Fork небезопасен после старта Metal. HTTP не узкое место: хватает одного uvicorn-воркера |

## Последствия

Плюсы:
- Perception задачи N+1 идёт параллельно с VLM задачи N.
- Падение или зависание MLX убивает только vlm worker. Промежуточный результат уже на диске ([ADR-0005](0005-deadline-tiers-provisional-degradation.md)).
- Перезапуск API — меньше 2 с, Qwen не перезагружается.
- Один код супервизии на macOS, Windows и Linux.

Минусы и цена:
- Процессы общаются только через SQLite и файлы задачи. Задержка claim — до 250 мс (интервал опроса).
- Нужен аккуратный код heartbeat и SIGKILL. Проверка cmdline защищает от убийства чужого процесса с переиспользованным pid.
- LaunchAgents стартуют только после входа пользователя (FileVault). Ручной вход после перезагрузки — пункт чек-листа [jury_mode.md](../jury_mode.md).
- В plist нельзя `uv run`: только абсолютный путь к Python из venv.
- Бюджет памяти считается по трём процессам ([architecture.md, раздел 8](../architecture.md#8-бюджет-памяти-mac)).

Что проверяет решение:
- `tests/workers`: `kill -9` посреди задачи → задача всё равно done; падение VLM → отдан промежуточный результат.
- Репетиции со сбоями: SIGSTOP perception, `kill -9` обоих воркеров, перезагрузка со входом.
- import-linter: в `api`, `jobs`, `core`, `contracts` нет ML-импортов.

Связанные решения: [ADR-0003](0003-sqlite-wal-queue-state.md), [ADR-0005](0005-deadline-tiers-provisional-degradation.md), [ADR-0009](0009-perception-neural-engine.md), [ADR-0010](0010-qwen-vlm-bounded-verifier.md). Обзор: [architecture.md, раздел 3](../architecture.md#3-процессная-модель).
