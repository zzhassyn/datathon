# CashGuard — правила для агентов

> **Назначение:** единые правила для Claude Code и Codex во всех worktree. Читать первым, до любого изменения.
> **Владелец:** S0 (интегратор) · **Статус:** черновик v1, 17.10.2026

## Продукт за 5 строк
- CashGuard — локальный AI-сервис (ForteBank Datathon 26): видео кассы `.mov` → вердикт `violation`/`no_violation`, тип, таймкод, 1–3 кадра-доказательства, описание и норма РК.
- Пайплайн: PyAV (таймкоды из PTS) → YOLO26 detect/pose/cls на Core ML (Neural Engine) + BoT-SORT → сцена → правила → Qwen3.5-4B (mlx-vlm) только понижает баллы → рендер.
- Процессы: `api` (FastAPI, uvicorn, 1 процесс) + `perception` + `vlm` под супервизором `cashguard up`; очередь и состояние — SQLite WAL в `var/`.
- Хост: MacBook Pro M2 Pro 16 ГБ, он же машина обучения. Режимы `serve|train|dev` не совмещаются, на пике свободно ≥ 3 ГБ. Переключение — `just mode`; обучение на Mac запускается только через `just train-a` / `just train-c`: они отказываются стартовать, если работает сервис ([docs/runbook_mac.md](docs/runbook_mac.md), раздел 6).
- UI — React SPA из `web/` на том же origin. Публичный URL — named Cloudflare Tunnel, резерв — Tailscale Funnel. Разработка — Windows, параллельные worktree.

## Контракт жюри (заморожен)
- `POST /api/v1/analyze` (multipart, поле `file`) → 200 `{"job_id"}`; `GET /api/v1/jobs/{job_id}` → `processing|done|failed`. Ключ — заголовок `X-API-Key`.
- Оракул: `src/cashguard/contracts/schemas/jury_job.schema.json` (ТЗ §10, `additionalProperties: false`). Модели: `src/cashguard/contracts/jury.py`. Пример: `tests/fixtures/jury_example.json`.
- Схему меняет только человек. Никаких лишних ключей: сигналы, нормы отдельным полем, отладка в ответ жюри не попадают.
- Цель — ответ ≤ 240 с от прихода запроса (лимит жюри 300 с). Подробно: [docs/api.md](docs/api.md), [ADR-0004](docs/adr/0004-auth-before-body-streaming-upload.md), [ADR-0006](docs/adr/0006-frozen-jury-contract.md).

## Команды (появятся в P0)
Всё запускается как `uv run just <recipe>`; на Windows рецепты идут через PowerShell, `[macos]` — только на Mac.

| Рецепт | Что делает |
|---|---|
| `setup` | окружения: `uv sync`, `pnpm install --frozen-lockfile` |
| `dev` | супервизор `--profile dev`: api с `--reload` на порту worktree + worker `--role all` (stub/replay) |
| `up` | супервизор `cashguard up` |
| `check` | шлюз мержа: ruff, basedpyright, lint-imports, `pytest -m 'not mac and not slow'`, `biome ci`, `tsc -b`, проверка дрейфа схем |
| `test` | pytest |
| `schemas` | `python -m cashguard.contracts.export`: JSON Schema + `web/src/shared/api/openapi.json` |
| `eval-replay`, `loso` | правила по 40 трассам против `eval/baselines/main.json`; подбор τ по фолдам L1–L5 |
| `e2e`, `build-web` | Playwright против API в stub-режиме; сборка `web/dist` |
| `jury-sim URL KEY N` | N параллельных прогонов curl из ТЗ через публичный URL |
| `status`, `trace <id>` | очередь, воркеры, память; склеенный лог одного job |
| `gen-key`, `docker-build`, `sync-signals` | ключи API; образ `deploy/linux`; трассы с Mac на Windows по LAN/Tailscale |
| `[macos]` `extract-signals`, `eval-full`, `perf`, `robustness` | трассы и полная оценка на Mac, бюджеты, устойчивость |
| `[macos]` `deploy`, `services-*`, `mode serve\|train\|dev` | LaunchAgents, туннель, переключение режимов (`mode train\|dev` = `launchctl bootout` + `disable`, `mode serve` = `enable` + `bootstrap`) |
| `[macos]` `train-a`, `train-c` | обёртки над `training/run_stage_{a,c}.sh` под `caffeinate -i`; отказываются стартовать, если `pgrep -f 'cashguard.cli up'` что-то находит |

## Участники команды (с 17.10.2026)
Сессии S0–S6 ниже — это области кода. Люди распределены так; при расхождении прав этот раздел.

| Участник | Отвечает за | Сессии и папки |
|---|---|---|
| **Backend и интеграция** | API, очередь, воркеры, оркестрация пайплайна, сцена из наблюдений, **применение правил, вердикт, описание и ответ**, eval; общие контракты, зависимости, мерж в `main` | S0 + S1 + код S4/S5: `contracts/`, `core/`, `jobs/`, `api/`, `workers/`, `cli.py`, `pipeline/{runner.py,stub.py,ingest,scene,rules,verdict,legal,render}`, `eval/`, `configs/` (кроме `perception.yaml`, `trackers/`, `vlm.yaml`), `tests/` (кроме `tests/mac/`, `tests/unit/evidence/`, `tests/fixtures/timelines/`) |
| **Frontend** | веб-интерфейс по UI-контракту и примерам `tests/fixtures/ui/*.json` | S2: `web/**`, `docs/ui.md` |
| **ML и наблюдения** (участник №1) | обучение и выпуск моделей, **наблюдения модели**: объекты, координаты, время, треки, состояния фрагментов (`PerceptionTrace`), кадры-доказательства, VLM-бэкенд, Mac и туннель | S3 + ML-часть S6: `training/**`, `pipeline/{decode,perception,evidence}/`, `pipeline/vlm/`, `workers/vlm_worker.py`, `configs/{perception,vlm}.yaml`, `configs/trackers/`, `deploy/{macos,cloudflared}/`, `tests/mac/`, `tests/unit/evidence/` |
| **События и нормы** (участник №2) | **проверенные примеры событий** (pos/neg на каждый код), основания правил и нормы, ручная проверка разметки | `tests/fixtures/timelines/`, нормы в `configs/catalog/*.yaml` через PR интегратору, `docs/{legal_sources,violation_catalog}.md` |

Детектор сам по себе не выдаёт нарушение: наблюдения (ML) → примеры событий и основания (участник №2) → применение правил, вердикт и ответ (backend). Формат наблюдений — `contracts/perception.py`, меняется только через интегратора.

**Ветки.** `codex/forte-frigate-ui` — исходный снимок дизайна (не меняется); `codex/frontend-integration` — frontend; `s1/backend-core-jobs` — backend; `main` — только проверенные совместимые изменения. Каждый работает в своём клоне или worktree; два агента не меняют один checkout. Контракты — маленьким отдельным PR раньше зависимых изменений. Мерж в `main` — только интегратор, fast-forward. Опубликованные ветки не переписываются (`push --force` запрещён): для переноса на новую базу интегратор делает новую ветку-кандидат, старую сохраняет. Контрольные точки K0–K4 — [docs/roadmap.md](docs/roadmap.md).

## Владение папками
Правьте только свои папки. Чужое — через PR владельцу; контракты и зависимости — через S0. Пути пакетов (`contracts/`, `core/`, `pipeline/`, `eval/` и т. п.) — от `src/cashguard/`; `configs/`, `tests/`, `docs/`, `deploy/`, `scripts/`, `web/`, `training/`, `eval/baselines/` — от корня.

| Сессия | Папки и файлы |
|---|---|
| S0 интегратор | `contracts/**`, `pipeline/runner.py`, `pipeline/rules/{base,registry,context}.py`, `configs/catalog/**` v0, `configs/legal/`, `configs/models.yaml`, `tests/contract/`, `pyproject.toml`, `uv.lock`, `justfile`, `AGENTS.md`, `CLAUDE.md`, `.pre-commit-config.yaml`, `README.md`, `docs/{README,architecture,pipeline,limitations,roadmap,structure,violation_catalog}.md` (карточки кодов в `violation_catalog.md` правит только S0 по PR владельцев кодов), `docs/adr/` |
| S1 платформа и оценка | `core/`, `jobs/`, `api/`, `workers/` (кроме `vlm_worker.py`), `cli.py`, `pipeline/{stub.py,ingest,render}`, `eval/`, корневой `eval/baselines/`, `configs/{deadlines,perf_budget}.yaml`, `tests/{api,jobs,workers}`, `tests/unit/{core,jobs,render}`, `deploy/linux/`, `scripts/` (кроме `bench_mac.py`, `sync_signals.py`), `docs/{api,eval}.md` |
| S2 web | `web/**` вместе с `web/e2e/`, `docs/ui.md` |
| S3 perception и обучение (Mac) | `pipeline/{decode,perception,evidence}/` (включая экспортёр сервиса `pipeline/perception/export.py`), `configs/perception.yaml`, `configs/trackers/`, `training/**` (после P0-S0-01; только аддитивные правки по согласованию с автором сессии обучения, скрипты `00–12` и `run_stage_*.sh` не переписываются), правки `configs/models.yaml` через PR к S0, `tests/mac/`, `tests/unit/evidence/`, `docs/training.md` |
| S4 сцена + правила A | `pipeline/scene/`, `pipeline/rules/{leave,access}/`, `configs/scene.yaml`, их `configs/rules/*.yaml` и `configs/catalog/*.yaml` после v0, `tests/fixtures/timelines/`, `tests/unit/scene/` |
| S5 правила B + вердикт + нормы | `pipeline/rules/{cashops,collection,discipline}/`, `pipeline/{verdict,legal}/`, их `configs/rules/*.yaml`, нормы в `configs/catalog/*.yaml`, `tests/unit/{verdict,legal}/`, `docs/legal_sources.md` |
| S6 VLM + Mac-ops + сигналы | `pipeline/vlm/`, `workers/vlm_worker.py`, `configs/vlm.yaml`, `pipeline/rules/signals/`, `configs/catalog/signals.yaml` после v0, `configs/rules/SIG_*.yaml`, `deploy/{macos,cloudflared}/`, `scripts/{bench_mac,sync_signals}.py`, `tests/unit/vlm/`, `docs/{runbook_mac,jury_mode}.md` |

Тесты правила `tests/unit/rules/<CODE>_test.py` — у владельца правила. Кто из людей ведёт какую сессию — раздел «Участники команды» выше.

У S3 и S6 по две сессии (Mac и Windows). Файлы между ними поделены:

| Сессия | Файлы |
|---|---|
| S3-Mac | `pipeline/perception/backends/coreml.py`, `pipeline/perception/export.py`, `tests/mac/test_coreml_*`, прогоны обучения |
| S3-Win | `pipeline/decode/`, `pipeline/perception/{orchestrator,plan,gating,crops,trace_io}.py`, `pipeline/perception/backends/{torch,replay,stub,null}.py`, `pipeline/evidence/` |
| S6-Mac | `pipeline/vlm/backends/mlx.py`, `workers/vlm_worker.py`, `deploy/macos/`, `scripts/bench_mac.py`, `tests/mac/test_mlx_*` |
| S6-Win | остальные файлы S6 |

Общие файлы:
- `eval/baselines/main.json` правит только S1: PR правил прикладывают дифф `just eval-replay`, а S1 после каждого мержа перегенерирует базовую линию одним последовательным PR.
- `configs/rules/<CODE>.yaml` правит только владелец кода; τ_final S5 публикует таблицей по `eval-full` (P3-S5-01), владельцы переносят значения сами.
- `configs/catalog/<CODE>.yaml`: шаблоны и пороги — владелец кода в P1–P2, нормы — S5 только в P3-S5-01.
Worktree: `git worktree add ../cg-wt/<slug> -b <session>/<slug> main`, у каждого свой `var/` и порт (`scripts/port.py`, 8100–8199). Мерж — только S0, fast-forward.

## Закреплённые версии
| Слой | Версии |
|---|---|
| Python | CPython 3.12 (`>=3.12,<3.13`), uv 0.13.1, одна версия в `.venv`, `.venv-train` и Docker |
| Backend | fastapi 0.143.3, starlette 1.8.0, pydantic 2.14.1, pydantic-settings 2.16.0, uvicorn 0.54.0, streaming-form-data 2.1.0, structlog 26.1.0, psutil 7.2.2 |
| Видео и ML | av 19.0.1, numpy **2.3.5** (жёстко), opencv-python 5.0.0.93, ultralytics 8.4.179 (проверить; откат на 8.4.178 сразу в обоих окружениях), torch 2.14.1 / torchvision 0.29.1, coremltools 9.0, mlx 0.32.4, mlx-vlm 0.7.8, llguidance 1.9.1 |
| Модели | Qwen3.5-4B mlx-community: 8-bit rev `1704583a`, 6-bit `fbb1092a`, 4-bit `0e7ffd5c`; YOLO-веса из ultralytics/assets v8.4.0 |
| Web | node 24.22.0, pnpm 10.34.7, typescript **5.9.3**, react 19.3.1, vite 8.3.4, tailwindcss 4.3.3, @tanstack/react-query 5.104.3, openapi-typescript 7.13.0, openapi-fetch 0.17.0, @biomejs/biome 2.5.15, vitest 5.0.5, @playwright/test 1.64.1 |
| Инструменты | ruff 0.17.1, basedpyright 1.40.2, import-linter 2.15, prek 0.5.5, rust-just 1.59.0, pytest 9.1.1 |

**Запрещено:** TypeScript 7.x (нет compiler API, ломает openapi-typescript) и 6.x (нужен override peer), react-router, msw, pnpm 12, npm/yarn вместо pnpm, `fastapi[standard]`, Python 3.13, любой numpy кроме 2.3.5, Celery/RQ/arq/Redis/Huey/Postgres, SQLAlchemy/SQLModel/aiosqlite, gunicorn и fork, jinja2 (шаблоны — `str.format_map`), orjson/msgspec, `TrustedHostMiddleware`, SSE и WebSockets в v1, `Results.plot()`, `OBJC_DISABLE_INITIALIZE_FORK_SAFETY`, Core ML `ALL`/`CPU_AND_GPU`, Docker на macOS. Новая зависимость — только через S0.

## Жёсткие правила
1. **Никаких облачных AI-вызовов** в коде обработки видео: ни LLM, ни VLM, ни распознавания; кадры, видео и их описания наружу не уходят. Воркеры работают офлайн (`HF_HUB_OFFLINE=1`, `YOLO_OFFLINE=1`). Нарушение — дисквалификация.
2. **Никакого распознавания лиц** (FAQ 29). Роли — по зонам и поведению, BoT-SORT без ReID.
3. **Процесс API не импортирует ML**: `api`, `jobs`, `core`, `contracts` не импортируют torch, ultralytics, coremltools, mlx, mlx_vlm, cv2 (av можно). Проверяет import-linter.
4. **Не читать** `data/`, `var/`, `models/`, картинки в `runs/` (`runs/**/*.jpg`), `*.mov`, `*.mp4`, кадры датасета и жюри. Числовые отчёты в `runs/` (`results.csv`, `timing.json`, `eval_*.md/json`) читать можно. Агент работает с кодом, метриками и числовыми replay-трассами. Не коммитить `data/`, `var/`, `models/`, `runs/`. Полный ответ jury API с base64-кадрами не выводить в терминал: `scripts/smoke_jury.sh` пишет его в `var/smoke/`. На Mac относительные deny-правила не закрывают `~/cashguard/...` из worktree `~/cg-wt/<slug>`, поэтому человек ставит пользовательский `~/.claude/settings.json` с абсолютными путями ([docs/roadmap.md](docs/roadmap.md), раздел 4.3).
5. **Не править сгенерированное**: `contracts/schemas/generated/*`, `web/src/shared/api/{openapi.json,schema.d.ts}`, `docs/violation_types.md`. `uv.lock` и `pnpm-lock.yaml` только перегенерировать.
6. **Контракты первыми**: изменение в `contracts/` — отдельный маленький PR к S0, мержится первым; зависимые ветки потом делают rebase.
7. **Не «упрощать»**: обработчик загрузки принимает только `request: Request` (ключ до тела, без `UploadFile`); таймкоды из PTS, не `frame_index/fps`; трекер всегда явно (`configs/trackers/botsort_static.yaml`); опрос, не SSE; дочерние процессы — `spawn`; VLM только понижает балл.
8. **Нормы не выдумывать**: акты и пункты — только из [analysis/case_analysis.md](analysis/case_analysis.md) §7, цитаты — дословно из `analysis/normative/*.txt`. Утратившие силу НБРК № 58 (2001), № 250 (2012), № 48 (2018), № 254 (2012) не цитировать. Косвенная норма — «Может свидетельствовать о несоблюдении…».
9. `training/` — зона S3 (после P0-S0-01; только аддитивные правки по согласованию с автором сессии обучения), `analysis/` — только чтение. Сервис не импортирует `training/` и `kassa_train`; модели для сервиса выпускает экспортёр `pipeline/perception/export.py`.
10. Тексты для пользователя и описания — на русском; идентификаторы, пути, команды — на английском. Непроверенное число помечать «оценка» или «проверить».

## Definition of Done
- После rebase на `main` зелёный `uv run just check`.
- Новый код покрыт тестами в своей папке `tests/`. Mac-тесты помечены `@pytest.mark.mac`; в PR сказано, что не проверено на Mac.
- Правило: pos/neg фикстуры в `tests/fixtures/timelines/`, запись в `configs/catalog/<CODE>.yaml` с дословной цитатой нормы, snapshot шаблона описания, порядок — в [docs/structure.md](docs/structure.md).
- PR правила прикладывает diff `just eval-replay` против `eval/baselines/main.json`: LOSO recall по видео 1.0, нет новых срабатываний вердиктных кодов на 19 чистых роликах, precision, точность типа и медиана |Δt| в допуске. Саму базовую линию PR правил не меняет: её перегенерирует S1 после мержа.
- Изменены только свои файлы, нет новых зависимостей без S0, сгенерированное перегенерировано.
- Обновлены README папки и нужный `docs/*.md`.

## Где документация
- [docs/README.md](docs/README.md) — индекс; [docs/structure.md](docs/structure.md) — дерево, владельцы, имена, как добавить тип нарушения или сигнал.
- Перед правкой папки прочитайте её `README.md` и `AGENTS.md`, если он есть (`web/AGENTS.md`, `src/cashguard/<module>/AGENTS.md`). Codex читает `AGENTS.md` только от корня до своей рабочей папки.
- Исследование: [analysis/](analysis/) (`case_analysis.md`, `detection_design.md`, `labels_typed.csv`, `normative/`). Обучение: [training/README.md](training/README.md).
