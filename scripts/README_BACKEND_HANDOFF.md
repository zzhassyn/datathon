# CashGuard S1 — продолжение ИИ-разработчиком на другом компьютере

Обновлено: 17.10.2026, Asia/Almaty. Этот документ предназначен для новой сессии
ChatGPT/Codex или другого ИИ-инструмента с доступом к файлам, терминалу и Git.
Предыдущая переписка и доступ к исходному компьютеру не требуются.
Backend-разработка приостановлена; пользователь поручил сохранить код в GitHub
для продолжения на другой машине. Контекст и незавершённые задачи приведены ниже.

## 1. Получить код из GitHub

**Репозиторий:** https://github.com/zzhassyn/datathon
**Ветка с backend:** `s1/backend-core-jobs` (она ещё не слита в `main`).
**Основа реализации:** `41d7620` из `main`.
**Точка входа:** `scripts/README_BACKEND_HANDOFF.md` в этой ветке.

На новом компьютере нужны Git, uv и доступ к этому репозиторию. Выполнить в
выбранной папке (имя каталога можно изменить):

```bash
git clone --branch s1/backend-core-jobs https://github.com/zzhassyn/datathon.git cashguard-backend
cd cashguard-backend
git status --short --branch
git log -6 --oneline
git fetch origin --prune
```

Все дальнейшие команды выполнять **из корня клона** `cashguard-backend`.
Не использовать пути старого компьютера. В Git передаются source, tests, scripts
и конфигурация дедлайнов. `.venv`, `var`, SQLite jobs, API keys, видео и модели
не передаются и для synthetic stub не нужны: окружение и тестовые видео создаются
заново. Веса YOLO/Qwen не скачивать для запуска этой итерации.

Если репозиторий уже есть, сначала проверить его изменения и worktree. Для чистого
checkout можно `git switch --track origin/s1/backend-core-jobs`, если локальной
ветки ещё нет. При существующей ветке — `git switch s1/backend-core-jobs` и
`git pull --ff-only`. Не перезаписывать чужие незакоммиченные изменения.

Перед разработкой прочитать `AGENTS.md`, `CLAUDE.md` (если применим), roadmap и
README модулей. Затем сравнить ветку с актуальным `origin/main`: если S0 уже
выпустил контракты, адаптировать эту реализацию к ним. Не делать force push,
не мержить в main: интеграцию выполняет S0. Общие файлы и контракты не менять
самостоятельно. Последующий push — только в согласованную backend-ветку.

### Поручение новой ИИ-сессии

> Продолжи CashGuard backend роли S1 из ветки `s1/backend-core-jobs` репозитория
> `zzhassyn/datathon`. Сначала прочитай `AGENTS.md` и этот handoff целиком.
> Установи окружение по разделу 5, проверь tests, затем выполняй раздел 7.
> Не начинай заново и не подключай реальные модели вместо stub. Соблюдай ownership
> S0/S2/S3, frozen Jury Schema, локальную обработку и запрет чтения банковских видео.
> Докладывай по-русски: что проверено, что изменено, какие блокеры остались.

## 2. Задание и границы

Пользователь — S1: Platform/API/Jobs/Evaluation. Нужен первый локально работающий
FastAPI + SQLite WAL backend со **синтетическим детерминированным stub**, пока
YOLO/Qwen не обучены. Порядок: P0-S1-01 → 02 → 03 → 04 → P1-S1-01 → P1-S1-02.

Менять только собственные файлы из `AGENTS.md`. В этой итерации не изменялись
`contracts/**`, frozen schema, `web/**`, `frontend/**`, `training/**`, ML-модули,
общие конфигурации, `pyproject.toml`, `uv.lock`, `justfile`, корневой `README.md`,
`AGENTS.md`, `CLAUDE.md`, ADR. Исходные банковские видео не открывались и никуда
не отправлялись. Используется только явно сгенерированная синтетика.

Важные инварианты:

- Jury API только `/api/v1/*`, `X-API-Key` **до первого чтения тела**.
- Request streaming, без `UploadFile`, `Form`, `request.body()` и ML-импортов в API.
- Оракул: `src/cashguard/contracts/schemas/jury_job.schema.json`.
- Публичные статусы `processing/done/failed`; внутренние `queued/running`.
- Каждый POST получает новый job_id даже при дедупликации.
- UI отдельный `/api/ui/*`, URL кадров вместо base64; Jury jobs открытым UI не видны.
- Непомеченное видео в stub не превращается в ложный `no_violation`.
- Все subprocess свежие интерпретаторы, multiprocessing `spawn`; без fork/ML.
- Дедлайны от приёма запроса; время stub не является benchmark реального AI.

## 3. Что было в репозитории и что отсутствует

При проверке исходной базы `41d7620` GitHub содержал `main`, `product-docs`, `training-plan`; открытых
PR не найдено. Другой backend-ветки не было. `main` содержал документацию и
каркас `src`, но не содержал:

- `pyproject.toml`, `uv.lock`, `justfile`, `.python-version`;
- Python `contracts/{jury,jobs,video,ui,health,findings,ports,...}.py`;
- `pipeline/runner.py`, реестр/реальные правила, YAML-каталог;
- готовое новое приложение `web/`, OpenAPI/TS types.

По прямому разрешению пользователя реализованы независимые части через
**внутренние адаптеры**, не через самодельные файлы `contracts/`:
`jobs.repo.JobRecord`, `core.budget.DeadlineTiers`, `ingest.probe.VideoInfo`,
dict UI responses по документированной форме. Это нужно заменить/связать с
Python-контрактами S0 после их появления. Полную приёмку roadmap не объявлять
закрытой до общей интеграции и гейтов.

## 4. Реализовано

### Core и Jobs — P0-S1-01

`src/cashguard/core/{settings,logging,paths,ids,budget,atomic,memory,env,versions}.py`:
Pydantic Settings/SecretStr, CG_ env, безопасные пути, UUID, monotonic budget,
atomic fsync/replace, offline env, structlog/redaction/rotation, psutil и hashes.
`configs/deadlines.yaml` валидируется при запуске API/worker/supervisor.

`src/cashguard/jobs/{db,repo,cache,perfstats,retention}.py`:
WAL, `PRAGMA user_version` migrations, thread/process-local connections,
`BEGIN IMMEDIATE` и `UPDATE ... RETURNING`, CAS claim/finalize, lease heartbeat,
повторы, dedup/link, full-only cache, EWMA, terminal retention.

Последнее усиление этой итерации: **migration v2 с `lease_token` на каждую
попытку**. Одного worker name мало: supervisor повторно использует имя после
рестарта. Токен передаётся в progress, heartbeat, provisional, finalize/fail.
Запоздавший worker не может затронуть новую попытку. Изменение внутреннее,
в Jury JSON поле не добавляется. Проверить этот участок первым после продолжения.

Retention удаляет только terminal jobs, сохраняет родителей linked jobs,
сначала удаляет строку, затем папку, повторно чистит orphan/trash.
`CG_RETENTION_HOURS=0` выключает очистку.

### API и ingestion — P0-S1-02

`api/{main,deps,upload,ingest_service,sweeper,views,preview,ratelimit}.py`,
`api/routes/{jury,ui,health,admin}.py`, `pipeline/ingest/{probe,preview}.py`.

- Sha256 + constant-time comparison всех ключей; auth-before-body тестируется
  прямым ASGI receive, который падает, если его вызвали при 401.
- Streaming multipart с backpressure, sha256, лимитами тела и multipart headers;
  413 по Content-Length и реальному потоку; cleanup на ошибке/отмене.
- Fsync/rename файла до DB commit и 200. Оба jury endpoint имеют slash aliases.
- 401/404/413/415/422/507; disk full в mkdir, metadata и SQLite также 507.
- **Исправлена cancellation race**: thread COMMIT дожидается завершения,
  committed flag ставится внутри операции; отмена не удаляет durable input.
- PyAV local mov demuxer; неизвестный первый atom (`uuid`) допустим, если контейнер
  читается. Deep validation в stub worker проверяет duration/dimensions/первый кадр.
- GET lazy finalize, immutable attempt files, отдельный job_id для linked JSON.
  Повреждённый saved Jury JSON не выдаётся как done: возвращается schema-valid failed.
- `/health`, `/ready`, team-only `/api/v1/admin/status`, sweeper+retention.

### Rendering — P0-S1-03

`pipeline/render/{jury,ui}.py`: frozen JSON Schema, strict base64, JPEG framing,
finite timestamps/bounds, equality event frame/timecode, сортировка, максимум
5 нарушений и 3 кадра. Safe fallback не выдаёт ложный clean: невосстановимое
evidence даёт `failed`. UI fragment содержит URL, не base64.

JPEG validator проверяет структуру, а не весь entropy stream. Действительность
stub JPEG обеспечивается encode из PyAV decoded synthetic frame и decode-тестом.
Реальный S3 evidence должен использовать согласованный encode/reencode interface.

### Workers/stub/CLI — P0-S1-04

`workers/{__main__,base,perception_worker,warmup,supervisor}.py`, `pipeline/stub.py`,
`cli.py`, `scripts/{port,gen_key,make_synthetic_mov}.py`, `scripts/smoke_jury.sh`.

- `comment=cg-stub:clean|violation`, только metadata, не имя файла.
- Синтетический H.264 MOV; valid JPEG из реального декодированного кадра и PTS.
- Тексты violation явно говорят «stub/синтетический», никаких выдуманных норм.
- Supervisor, restart backoff, progress/liveness отдельно, watchdog, graceful
  shutdown с дочерним reload-процессом. Падение worker восстанавливает задачу.
- Immutable per-attempt `jobs/<id>/attempt-<uuid>/{jury,ui_result,analysis}.json`
  и `evidence/01_event.jpg`; CAS публикует пути. Не перезаписывать canonical результат.
- Предварительный результат финализируется после 225 с; при отсутствии результата
  к 250 с честный failed, не фиктивный done. Реальный hard-stop частичного inference
  ждёт S0/S3 runner.
- `full/replay` намеренно fail fast, stub не маскируется под реальные модели.

### UI API — доступная часть P1-S1-01

Upload/poll, H.264 faststart preview + HTTP Range 206, private JPEG routes,
path/symlink escape checks, пустой catalog+ETag/304, normal/jury rate profiles,
429/Retry-After, опциональный session cookie. Jury API не rate-limited.

UI adapters ещё не валидируются отсутствующей `UiJobView`. Catalog пуст, потому что
нет S0 YAML. HEVC/rotation отмечаются needs_transcode, **transcoding не реализован**.
LRU UI cache, полноценные stages и generated OpenAPI требуют следующей интеграции.

### Evaluation — независимая часть P1-S1-02

`eval/{labels,metrics,run_eval,report}.py`: allowlisted label columns, X/Х,
дубли X-47→X-4 и X-48→X-5, video/event/type/timecode metrics, baseline comparison,
numeric CLI и injection seam для evaluator. Missing results не засчитываются как TN.
Числовая проверка labels: 40 unique / 21 positive / 22 events / 19 clean.

`docs/eval.md` и README обновлены. Полный replay 40 traces, LOSO, baseline `main.json`
**не созданы**: нет runner/rules/owner fixtures. Нельзя подставлять gold labels в
prediction и объявлять это качеством AI.

## 5. Установка и запуск на новой машине

Исходная проверка выполнена на macOS arm64, CPython **3.12.1**, uv **0.13.1**.
На другой машине требуется CPython **3.12.x**, не 3.13. Native Windows и Linux
пока не проверены; переносимый installer выбирает соответствующий путь Python.
Для Windows практичный вариант — WSL2; native PowerShell команды приведены ниже.

### Установка — одна команда для всех ОС

Из корня клона, после установки uv:

```text
uv run --no-project --python 3.12 python scripts/setup_backend.py
```

uv получит Python 3.12, если его нет. Скрипт создаст `.venv` и установит только
документированные backend/test dependencies, затем проверит импорт `cashguard`.
При существующей `.venv` он проверяет Python 3.12, не удаляет её автоматически.
macOS/Linux также могут использовать `bash scripts/setup_backend.sh` — это wrapper.

Это временный bootstrap S1 до `pyproject.toml` / `uv.lock` S0. Pins:
fastapi 0.143.3, starlette 1.8.0, pydantic 2.14.1, pydantic-settings 2.16.0,
uvicorn 0.54.0, streaming-form-data 2.1.0, structlog 26.1.0, psutil 7.2.2,
av 19.0.1, numpy 2.3.5, PyYAML 6.0.3, jsonschema 4.26.0, httpx 0.28.1,
pytest 9.1.1, ruff 0.17.1. Транзитивные зависимости пока не закреплены общим lock.
При появлении S0 packaging перейти на `uv sync --locked`, не создавать второй lock.

### macOS / Linux / WSL2

```bash
export PYTHONPATH="$PWD/src"
.venv/bin/python -m pytest tests/api tests/jobs tests/workers tests/unit/core tests/unit/jobs tests/unit/render tests/unit/eval -q
.venv/bin/ruff check src/cashguard/core src/cashguard/jobs src/cashguard/api src/cashguard/workers src/cashguard/pipeline/ingest src/cashguard/pipeline/render src/cashguard/pipeline/stub.py src/cashguard/eval src/cashguard/cli.py tests/api tests/jobs tests/workers tests/unit/core tests/unit/jobs tests/unit/render tests/unit/eval scripts/*.py
export CG_PIPELINE_MODE=stub
export CG_DATA_DIR="$PWD/var"
export CG_API_KEYS='jury:local-synthetic-test,team:local-team-test'
.venv/bin/python -m cashguard.cli up --profile dev --port 8127
```

Во втором терминале, тоже из корня клона:

```bash
export PYTHONPATH="$PWD/src"
.venv/bin/python scripts/make_synthetic_mov.py var/synthetic/violation.mov --verdict violation
.venv/bin/python scripts/make_synthetic_mov.py var/synthetic/clean.mov --verdict clean
bash scripts/smoke_jury.sh http://127.0.0.1:8127 local-synthetic-test var/synthetic/violation.mov
bash scripts/smoke_jury.sh http://127.0.0.1:8127 local-synthetic-test var/synthetic/clean.mov
curl -sS http://127.0.0.1:8127/health
curl -sS http://127.0.0.1:8127/ready
```

### Native Windows / PowerShell — ещё не проверено на Windows

```powershell
$env:PYTHONPATH = Join-Path $PWD 'src'
.\.venv\Scripts\python.exe -m pytest tests/api tests/jobs tests/workers tests/unit/core tests/unit/jobs tests/unit/render tests/unit/eval -q
$env:CG_PIPELINE_MODE = 'stub'
$env:CG_DATA_DIR = Join-Path $PWD 'var'
$env:CG_API_KEYS = 'jury:local-synthetic-test,team:local-team-test'
.\.venv\Scripts\python.exe -m cashguard.cli up --profile dev --port 8127
```

Во втором PowerShell терминале из корня клона:

```powershell
$env:PYTHONPATH = Join-Path $PWD 'src'
.\.venv\Scripts\python.exe scripts/make_synthetic_mov.py var/synthetic/violation.mov --verdict violation
.\.venv\Scripts\python.exe scripts/make_synthetic_mov.py var/synthetic/clean.mov --verdict clean
New-Item -ItemType Directory -Force var/smoke | Out-Null
curl.exe -sS -X POST http://127.0.0.1:8127/api/v1/analyze -H 'X-API-Key: local-synthetic-test' -F 'file=@var/synthetic/violation.mov' -o var/smoke/accepted.json
$job = (Get-Content var/smoke/accepted.json | ConvertFrom-Json).job_id
curl.exe -sS "http://127.0.0.1:8127/api/v1/jobs/$job" -H 'X-API-Key: local-synthetic-test' -o var/smoke/result.json
$r = Get-Content var/smoke/result.json | ConvertFrom-Json
$r | Select-Object job_id,status,verdict
```

Повторять GET до done/failed. На Windows использовать именно `curl.exe`, а не
PowerShell alias. Shell smoke требует Git Bash/WSL; для Git Bash указать
`CG_PYTHON` на `.venv/Scripts/python.exe`. Тест SIGSTOP пропускается вне POSIX;
symlink tests требуют Developer Mode/разрешений Windows. Не скрывать их сбои как
успешную Windows-проверку. chmod 600 не заменяет Windows ACL.

Во всех ОС:

- Тесты запускать именно `python -m pytest` из корня: есть импорт
  `scripts.make_synthetic_mov` (namespace package).
- Smoke JSON/base64 сохранять локально в `var/smoke`, не печатать кадры в терминал.
- Приведённые ключи публичные тестовые, только для локальной синтетики.
  Реальные ключи создавать локально через `scripts/gen_key.py`, не переносить
  секреты старого компьютера и не коммитить `.env`.
- Ctrl+C останавливает supervisor. Одна БД/CG_DATA_DIR — один supervisor.
- Все tests создают собственную синтетику; реальные записи банка не нужны.

## 6. Проверки на момент остановки

**Проверка backend на исходном Mac после остановки параллельных изменений:
137 passed, 1 warning, 7.68 s.** Ruff всего S1-кода и тестов, `git diff --check` и
`bash -n scripts/setup_backend.sh scripts/smoke_jury.sh` прошли.

Перед отправкой ветки отдельно проверен **чистый clone только закоммиченных
файлов**, без прежних `.venv`, runtime и данных. Команда bootstrap из раздела 5
создала новое окружение; импорт `cashguard` успешен. Повторный прогон:
**137 passed, 1 warning, 8.47 s**; Ruff и shell syntax прошли. Проверка выполнена
на том же Mac в отдельном каталоге: это подтверждает комплектность Git-файлов,
но не является проверкой на Linux/Windows или другом физическом компьютере.

Исторические промежуточные проверки (итоговый прогон выше приоритетнее):

- 52 tests render/eval, включая реальный JPEG decode.
- 20 tests Core/Jobs, включая два multiprocessing spawn claim/finalize.
- 12 worker tests, включая SIGKILL/SIGSTOP, восстановление и исчерпание попыток.
- 93 tests API+render+eval вместе прошли до последних Core/Jobs изменений.
- 11 regression integrity tests: cancellation after COMMIT, corrupted canonical
  и linked JSON, ENOSPC/SQLITE_FULL и повреждённый UI.
- API тесты проверяют auth-before-body, streaming sizes, fsync-before-commit,
  unknown uuid atom, dedup, restart, lazy finalize, UI Range/ETag/limits/privacy,
  пять параллельных загрузок и отсутствие ML imports.

После этого добавлены lease_token, настоящий jury profile validator, ограничение
multipart headers, upload deadline/import isolation тесты. Именно поэтому итоговый
повторный прогон важнее прежних чисел.

**Не выполнено:** живой сервер+curl smoke, browser Playwright E2E, `uv run just check`,
basedpyright/import-linter/schema-export/schemathesis. Последние гейты зависят от
отсутствующих общих файлов S0. Starlette TestClient выдаёт deprecation warning
для httpx; новый httpx2 самостоятельно не добавлялся.

## 7. Обязательные следующие шаги

1. Прочитать итоговый статус ниже, проверить git state, затем новые изменения S0.
2. Запустить полный S1 pytest и Ruff. Если появились ошибки последнего lease-token
   изменения: сначала согласовать все call sites и миграцию, не отключать fencing.
3. Запустить supervisor и **реальный curl smoke** для violation и clean; затем
   перезапустить и повторить GET уже готовой задачи. Не печатать base64.
4. Проверить `/ready`, worker logs и завершение reload child после Ctrl+C.
   Logging уже wired в CLI/worker; отдельный reload child может потребовать
   configure_logging в create_app. API errors сейчас stdlib log, не весь runtime
   поток приведён к structlog JSON.
5. Дополнить актуальными фактами `docs/api.md` и README ingest. В этой сессии
   `docs/api.md` пока описывает целевой дизайн, а не текущую степень готовности.
   Этот handoff фиксирует implementation state; README API содержит ссылку сюда.
6. Получить S0 contracts/runner/packaging, подключить модели response/ports,
   schema export, typecheck/import gates. Не править чужие файлы самостоятельно.
7. Продолжать небольшими коммитами поверх переданной ветки. Подготовить PR для S0
   после общей интеграции; не мержить самостоятельно.

Не считать полную P0/P1 приёмку закрытой по одним unit tests.
`pipeline_version='stub-v1'`/`config_hash='default'` пока временные defaults;
связать их с утверждённой конфигурацией и git/model manifests при интеграции S0.

## 8. Конкретные зависимости от S0

- Python contracts, runner, catalog/acts, import-linter и shared config.
- `jsonschema==4.26.0` требуется **runtime renderer**, не только dev dependency.
- Согласовать служебный `lease_token` в job record; не добавлять его в Jury API.
- Закрепить `/ready` stub без моделей и nullable `vlm_quant` при disabled VLM.
- Утвердить UI dict adapter/UiJobView, empty stub catalog и поля stub violation.
- Уточнить ownership `tests/fixtures/synthetic_video.py`: roadmap назначает S1,
  актуальная таблица AGENTS общий fixtures не перечисляет. Генератор реализован
  в разрешённом `scripts/make_synthetic_mov.py`; чужие fixtures не менялись.
- Разрешить противоречия: ADR-0004 §6 запрещает неизвестный atom, но API/roadmap
  требуют `uuid`→200; ADR-0012 говорит имя файла, roadmap требует comment metadata.
  Реализация следует прямому запросу пользователя и явной приёмке roadmap.

## 9. Handoff S2: frontend расхождения

Remote frontend-ветки уже не было при fetch. Проверен **локальный**
`codex/forte-video-frontend` at `e45dd2b`, папка `frontend/`.
`main/web` пока scaffold, работающего нового frontend нет.

- Прототип `frontend/src/api/index.ts` использует `/api/v1/analyze` и `/api/v1/jobs`
  с API key из памяти страницы. Новая архитектура требует `/api/ui/jobs` без Jury key.
- Прототип читает `processing` + base64 `evidence_frames`; UI endpoints дают
  queued/running и `violations[].evidence[].url`, stages и preview URL.
- Прототип допускает только localhost URL и ожидает CORS. Архитектура — same-origin,
  dev proxy `/api` в Vite, без расширения Jury API под frontend.
- В прототипе fetch upload; текущая docs/ui.md требует XHR progress.
- Нужно дождаться S0 UiJobView/OpenAPI и подключить S2 приложение, затем Playwright.

Файлы S2 не изменялись, сообщения коллегам/чужим чатам не отправлялись.
Эти замечания подготовлены для передачи владельцу S2.

## 10. Итог безопасной остановки

**137 tests passed**, включая окончательные migration v2 / lease-token call sites,
CAS races, UI profile и API regression cases. Ruff/check whitespace/shell syntax —
зелёные. Единственное предупреждение — deprecation httpx TestClient в Starlette.

Код передаётся через GitHub-ветку `s1/backend-core-jobs` с логическими коммитами.
Merge в main и PR не выполнялись. Живой curl smoke остаётся следующим шагом.
Работа не требует исходного Mac, его `.venv`, БД или локальной переписки: новый
инженер/ИИ начинает с clone (раздел 1), установки (раздел 5) и задач (раздел 7).
