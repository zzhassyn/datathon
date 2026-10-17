# Структура репозитория

> **Назначение:** полное дерево монорепозитория с владельцами и назначением папок, соглашения об именах, что генерируется и что игнорируется, пути на Windows и Mac, порядок добавления типа нарушения и сигнала.
> **Владелец:** S0 (интегратор) · **Статус:** черновик v1, 17.10.2026

Источник дерева — раздел FOLDER_TREE итоговой архитектуры. Связанные документы: [README.md](README.md) (индекс), [architecture.md](architecture.md), [../AGENTS.md](../AGENTS.md).

## Принципы

- У каждой папки один владелец (сессии S0–S6, см. [roadmap.md](roadmap.md)). Чужие файлы меняются только через PR владельцу.
- Один файл на правило, на запись каталога, на бэкенд. Правила находятся автоматически (`@register_rule` + `pkgutil`), центрального списка нет. Поэтому параллельные сессии почти не правят одни и те же файлы.
- Контракты (`src/cashguard/contracts/`) меняются первыми, отдельным маленьким PR к S0.
- Сгенерированные файлы руками не правятся.
- В каждой папке кода лежит `README.md`: назначение, владелец, какие файлы планируются, ссылка на документ.
- В P0 (карточка P0-S0-02) в модулях `src/cashguard/<module>/` появляются `AGENTS.md` — короткие правила зоны для агентов. Codex читает `AGENTS.md` от корня до своей рабочей папки.

## Метки владельцев

| Метка | Сессия | Где работает |
|---|---|---|
| S0 | интегратор: человек + ведущая сессия Claude Code | Windows |
| S1 | платформа и оценка | Windows |
| S2 | web (Codex или Claude) | Windows |
| S3 | perception и обучение | сессия на Mac + Windows для чистого кода |
| S4 | сцена + правила A (`leave`, `access`) | Windows, только replay-данные |
| S5 | правила B (`cashops`, `collection`, `discipline`) + вердикт + тексты норм | Windows, только replay-данные |
| S6 | VLM + эксплуатация Mac + сигналы | Mac для MLX и деплоя, иначе Windows |
| GEN | генерируется, руками не правится | — |

При 4 сессиях объединяются S4+S5 (все правила) и S3+S6 (вся работа на Mac).

## Дерево

Пометка «(P0)» — файл создаётся в фазе P0 ([roadmap.md](roadmap.md)). Сейчас в репозитории есть документация, `analysis/`, `training/`, каркас папок с `README.md` и `__init__.py`, замороженная `src/cashguard/contracts/schemas/jury_job.schema.json` и `tests/fixtures/jury_example.json`.

```
datathon/                                  корень (Windows: C:\Users\zhasy\datathon; Mac: ~/cashguard)
├─ AGENTS.md                               [S0] правила для агентов (<150 строк, <32 КиБ)
├─ CLAUDE.md                               [S0] строка 1 "@AGENTS.md", затем заметки для Claude Code
├─ README.md                               [S0] для жюри (ТЗ §8): адреса, curl, ключ, запуск, архитектура, модели, ограничения, AGPL
├─ LICENSE                                 [S0] AGPL-3.0 (P0)
├─ pyproject.toml uv.lock .python-version  [S0] проект сервиса: Python 3.12; extras ml/mac/cuda; группа dev;
│                                               настройки ruff, basedpyright, importlinter, pytest (P0)
├─ justfile                                [S0] рецепты (P0), список — в ../AGENTS.md
├─ .pre-commit-config.yaml .gitattributes .gitignore .editorconfig .env.example   [S0] (P0, кроме .gitignore)
├─ .claude/settings.json                   [S0] permissions.deny: Read(./data/**), Read(./var/**), Read(./models/**), Read(./runs/**/*.jpg),
│                                               Read(**/*.mov), Read(**/*.mp4) (P0); на Mac ещё ~/.claude/settings.json с абсолютными путями (человек)
├─ src/cashguard/                          пакет сервиса (src-layout)
│  ├─ cli.py                               [S1] `cashguard`: up, worker, export-schemas, extract-signals, eval, bench, gen-key, purge
│  ├─ contracts/                           [S0] чистые типы, ничего внутреннего не импортируют
│  │  ├─ jury.py ui.py jobs.py video.py perception.py scene.py findings.py catalog.py health.py
│  │  ├─ ports.py                          Protocol: VideoSource, PerceptionBackend, FrameStore, Rule, SignalRule, VlmBackend, ProgressFn, Clock
│  │  ├─ export.py                         `python -m cashguard.contracts.export [--check]`: JSON Schema + openapi.json для web
│  │  └─ schemas/
│  │     ├─ jury_job.schema.json           ЗАМОРОЖЕН, написан руками по ТЗ §10; меняет только человек
│  │     └─ generated/*.schema.json        [GEN]
│  ├─ core/                                [S1] settings.py logging.py paths.py ids.py budget.py atomic.py memory.py env.py versions.py
│  ├─ jobs/                                [S1] db.py repo.py cache.py perfstats.py retention.py
│  ├─ api/                                 [S1] без ML (av можно): main.py deps.py upload.py ingest_service.py ratelimit.py
│  │  │                                         sweeper.py views.py preview.py
│  │  └─ routes/                           jury.py (/api/v1) ui.py (/api/ui) health.py (/health, /ready) admin.py (/api/v1/admin/status)
│  ├─ workers/                             [S1] __main__.py (--role perception|vlm|all) base.py perception_worker.py warmup.py supervisor.py;
│  │                                            vlm_worker.py [S6]
│  ├─ pipeline/
│  │  ├─ runner.py                         [S0] оркестрация этапов, режимы stub|replay|full (сигнатуры заморожены в P0)
│  │  ├─ stub.py                           [S1] детерминированный фейковый результат; исход violation/clean — по метатегу контейнера
│  │  │                                         comment=cg-stub:<verdict> (пишет scripts/make_synthetic_mov.py), имя файла игнорируется
│  │  ├─ ingest/                           [S1] probe.py (читает и метатег cg-stub, использует только режим stub) preview.py (remux / транскодирование videotoolbox)
│  │  ├─ decode/                           [S3] pyav_source.py (сетка PTS, потоки AUTO, поворот, ограниченная очередь) frame_store.py window.py
│  │  ├─ perception/                       [S3] orchestrator.py plan.py gating.py trace_io.py
│  │  │                                         crops.py (повторяет data/crops/crop_spec.json; letterbox — только при letterbox: true)
│  │  │                                         export.py (новый путь: экспортёр сервиса → models/<role>/vNNN/, паритет, MLComputePlan)
│  │  │  └─ backends/                      coreml.py torch.py replay.py stub.py null.py
│  │  ├─ scene/                            [S4] zones.py roles.py sessions.py states.py events.py presence.py build.py
│  │  ├─ rules/                            base.py registry.py context.py [S0 → S4]
│  │  │  ├─ leave/                         [S4] pc_unlocked safe_key_left valuables_left safe_open left_during_service unattended_presence
│  │  │  ├─ access/                        [S4] door_not_closed outsider_in_cash_room third_person_during_service storage_unescorted
│  │  │  ├─ cashops/                       [S5] prev_cash_on_desk mixed_client_cash no_client_id recount_without_client cash_withheld
│  │  │  │                                      no_auth_check payout_without_recount change_before_prepared cash_concealed cash_transfer_outside_op
│  │  │  ├─ collection/                    [S5] collector_not_verified bag_cash_no_recount
│  │  │  ├─ discipline/                    [S5] personal_items food_drink phone_cashier
│  │  │  └─ signals/                       [S6] по одному файлу на каждый из 13 кодов SIG_*
│  │  ├─ verdict/                          [S5] scoring.py aggregate.py decide.py
│  │  ├─ vlm/                              [S6] questions.py grouping.py schema.py keyframes.py quant.py
│  │  │  ├─ prompts/*.yaml
│  │  │  └─ backends/                      mlx.py transformers.py replay.py null.py
│  │  ├─ evidence/                         [S3] select.py draw.py jpeg.py
│  │  ├─ legal/                            [S5] catalog.py (загрузка и проверка YAML каталога и актов) describe.py (str.format_map + фраза о норме)
│  │  └─ render/                           [S1] jury.py (проверка по замороженной схеме + безопасный fallback) ui.py
│  └─ eval/                                [S1] labels.py run_eval.py metrics.py loso.py robustness.py perf.py jury_sim.py report.py
├─ configs/
│  ├─ catalog/<CODE>.yaml                  [S0 v0 → владелец кода] title_ru, group, verdict_impact, timecode_policy, template_ru,
│  │                                            required_facts, vlm_question_ru, legal
│  ├─ catalog/signals.yaml                 [S0] 13 кодов SIG_*, формулировки «не наблюдается…»
│  ├─ legal/acts.yaml                      [S0] ПКО № 231, ПОП № 14 (+№ 88), ПОО № 49, ТИБ АРРФР № 53; утратившие силу — «не цитировать»
│  ├─ rules/<CODE>.yaml                    [владелец кода] τ, длительности, гистерезис (подбор LOSO)
│  ├─ scene.yaml perception.yaml vlm.yaml  [S4] / [S3] / [S6]
│  ├─ deadlines.yaml perf_budget.yaml      [S1]
│  ├─ models.yaml                          [S0, правит S3] роль → версия, путь, sha256, imgsz, бэкенд по платформе, ревизия и квант VLM
│  └─ trackers/botsort_static.yaml         [S3]
├─ training/                               [S3] СУЩЕСТВУЕТ; своя .venv-train; сервис его не импортирует
│  ├─ README.md setup_mac.sh download_models.sh requirements.txt run_stage_a.sh run_stage_c.sh
│  ├─ configs/                             classes.yaml (14 классов, порядок фиксирован) pipeline.yaml train.yaml external_datasets.example.yaml
│  ├─ kassa_train/                         common.py (пути, X/Х, дубли) weights.py (resolve_weight)
│  └─ scripts/                             00_check_env … 12_train_cls; 13_train_rfdetr (план, номер согласовать с сессией обучения);
│                                               выход 10_export_coreml (C4–C5) сервис не читает: модели выпускает pipeline/perception/export.py
├─ web/                                    [S2]
│  ├─ package.json pnpm-lock.yaml .node-version .npmrc vite.config.ts tsconfig*.json biome.json components.json
│  │  playwright.config.ts index.html (lang=ru, noindex) AGENTS.md
│  ├─ public/                              favicon.svg robots.txt (Disallow: /)
│  ├─ src/
│  │  ├─ main.tsx App.tsx                  провайдеры; выбор вида по ?job= и ?view=types (без роутера)
│  │  ├─ pages/                            UploadPage.tsx JobPage.tsx TypesPage.tsx
│  │  ├─ features/                         upload/ progress/ verdict/ violations/ evidence/ player/ signals/ recent/ health/
│  │  ├─ shared/                           api/ (openapi.json [GEN], schema.d.ts [GEN], client.ts, queries.ts) ui/ (shadcn)
│  │  │                                         lib/ (format, urlState, cn) i18n/ru.ts
│  │  └─ styles/index.css
│  └─ e2e/                                 upload.spec.ts result.spec.ts mobile.spec.ts fixtures/ (только синтетические ролики)
├─ tests/
│  ├─ contract/                            [S0] замороженная схема жюри, дрейф OpenAPI, дословность цитат, полнота каталога
│  ├─ api/                                 [S1] ключ до тела, коды 401/404/413/415/422/429/507, слэш в конце, жизненный цикл, schemathesis
│  ├─ jobs/  workers/                      [S1] гонка claim, истечение lease, идемпотентность CAS, kill -9 посреди задачи
│  ├─ unit/                                core/ jobs/ render/ [S1], scene/ [S4], rules/<CODE>_test.py [владелец], verdict/ legal/ [S5],
│  │                                            evidence/ [S3], vlm/ [S6]
│  ├─ mac/                                 [S3/S6] маркер mac: паритет Core ML и .pt, размещение на ANE, офлайн-загрузка MLX, ≥ 3 ГБ свободно
│  └─ fixtures/                            timelines/<CODE>_{pos,neg}_*.json [S4 + владелец правила], jury_example.json [S0],
│                                               synthetic_video.py [S1]
├─ eval/baselines/main.json                [S1] закоммиченная базовая линия метрик; PR правил прикладывают только дифф, перегенерирует S1
├─ deploy/
│  ├─ macos/                               [S6] launchd/kz.cashguard.{supervisor,tunnel,caffeinate}.plist.tmpl install.py
│  ├─ cloudflared/config.yml.tmpl          [S6] UUID туннеля, protocol http2, metrics, ingress → 127.0.0.1:8000, http_status:404
│  └─ linux/                               [S1] Dockerfile.cuda compose.yaml systemd/*.service
├─ scripts/                                [S1/S6] port.py gen_key.py make_synthetic_mov.py sync_signals.py smoke_jury.sh bench_mac.py
│                                               purge_var.py render_docs.py
├─ docs/                                   документы, индекс — README.md; adr/0001…0014
├─ analysis/                               исследование, не правится: case_analysis.md detection_design.md labels_typed.csv normative/*.txt
├─ data/    (gitignored, только Mac)       raw/open/videos, frames/, prelabels/, labels/, yolo/{pseudo,reviewed}, crops/
├─ models/  (gitignored целиком)           pretrained/*.pt, hf/Qwen3.5-4B-{8,6,4}bit/, <role>/vNNN/{best.pt,*.mlpackage,manifest.json}
├─ runs/    (gitignored)                   запуски обучения Ultralytics; числовые отчёты агенты читать могут, картинки — нет
└─ var/     (gitignored)                   db/cashguard.sqlite3(-wal,-shm), jobs/<id>/…, signals/, eval/, logs/, yolo/, smoke/ (ответы smoke_jury.sh)
```

Файлы задачи в `var/jobs/<id>/`: `input.mov` (удаляется после perception), `preview.mp4` (только UI), `meta.json`, `frames/` (удаляется после финального результата), `trace.json.gz`, `candidates.json`, `analysis.json`, `evidence/`, `jury.json`, `ui_result.json`. Вся папка удаляется через `CG_RETENTION_HOURS` (по умолчанию 24 ч). В окне жюри очистка выключена ([jury_mode.md](jury_mode.md), чек-лист заморозки).

Скрипты `training/run_stage_*.sh`, `training/scripts/` и `training/kassa_train/weights.py` сейчас пишутся в ветке `training-plan`. До P0 эта ветка сливается в `main` (условие из [roadmap.md](roadmap.md)).

## Рецепты `just`

`justfile` появится в P0 (владелец S0). Рецепты запускаются как `uv run just <recipe>`; `[macos]` — только на Mac. Что делает каждый — в [../AGENTS.md](../AGENTS.md), раздел «Команды».

| Где | Рецепты |
|---|---|
| везде | `setup`, `dev`, `up`, `check`, `test`, `schemas`, `eval-replay`, `loso`, `sync-signals`, `jury-sim`, `e2e`, `build-web`, `status`, `trace`, `gen-key`, `docker-build` |
| `[macos]` | `extract-signals`, `eval-full`, `perf` (только в режиме SERVE), `robustness`, `deploy`, `services-*` (например `services-install`, `services-restart`; полный список — в `justfile`), `mode serve\|train\|dev`, `train-a`, `train-c` (отказываются стартовать при работающем сервисе) |

## Границы импортов

Проверяет import-linter в `uv run just check`.

| Правило | Что значит |
|---|---|
| Слои | `api → jobs → core → contracts` и `workers → pipeline → contracts` |
| Без ML | `api`, `jobs`, `core`, `contracts` не импортируют torch, ultralytics, coremltools, mlx, mlx_vlm, cv2 (av в `api` можно) |
| Чистый Python | `pipeline.scene`, `rules`, `verdict`, `legal`, `render` — только Python + numpy, работают в replay на Windows |
| Независимость правил | подпакеты `rules.*` не импортируют друг друга |
| Обучение отдельно | никто не импортирует `training/` и `kassa_train` |

## Что генерируется, что пишется руками

| Файл | Как получается | Правило |
|---|---|---|
| `src/cashguard/contracts/schemas/generated/*.schema.json` | `uv run just schemas` | руками не править; дрейф ловит `just check` |
| `web/src/shared/api/openapi.json` | `uv run just schemas` (`python -m cashguard.contracts.export`, без ML) | коммитится, руками не править |
| `web/src/shared/api/schema.d.ts` | openapi-typescript из `openapi.json` | коммитится, руками не править |
| `docs/violation_types.md` | `scripts/render_docs.py` из `configs/catalog/` | руками не править |
| `uv.lock` | `uv lock` | при конфликте взять версию `main`, затем `uv lock` |
| `web/pnpm-lock.yaml` | `pnpm install` | при конфликте — `pnpm install` |
| `eval/baselines/main.json` | вывод `cashguard eval --update-baseline` (имя флага — проверить с S1) | коммитится; PR правил прикладывают только дифф `just eval-replay`, базовую линию после мержа перегенерирует S1 одним последовательным PR |
| `models/<role>/vNNN/manifest.json` | экспортёр сервиса `pipeline/perception/export.py` | не коммитится: `models/` в `.gitignore` целиком; версии, пути и sha256 — в `configs/models.yaml` |
| `web/dist/`, `var/**` | сборка и работа сервиса | в `.gitignore` |

Пишется руками и заморожено: `src/cashguard/contracts/schemas/jury_job.schema.json` — только с одобрения человека.
Компоненты shadcn в `web/src/shared/ui/` создаёт CLI shadcn, после этого ими владеет и правит S2.
Всё остальное пишется руками.

## Что не попадает в git

| Шаблон | Почему |
|---|---|
| `data/`, `*.mov`, `*.mp4`, `*.zip` | данные хакатона нельзя публиковать и хранить вне хакатона (FAQ 6) |
| `runs/`, `models/` (целиком, включая манифесты), `*.pt`, `*.safetensors` | веса и запуски обучения |
| `result_*.json` | полные ответы jury API с base64-кадрами; `scripts/smoke_jury.sh` пишет их в `var/smoke/` |
| `var/`, `*.sqlite3*` | состояние сервиса: задачи, видео жюри, трассы, логи |
| `.env` (`.env.example` коммитится) | ключи API |
| `.claude/worktrees/` | worktree агентов Claude Code |
| `web/dist/`, `dist/`, `node_modules/` | сборка web и зависимости |
| `.venv*/`, `__pycache__/`, кэши линтеров и pytest | окружения и кэши |
| `label-studio-data/`, `.label-studio/` | локальная база Label Studio |

Следствия:
- Синтетические ролики для тестов (`.mov`, `.mp4`) не коммитятся. Их создают `tests/fixtures/synthetic_video.py` и `scripts/make_synthetic_mov.py` во время тестов.
- Хук `check-added-large-files` (800 КБ) в `.pre-commit-config.yaml` блокирует случайный коммит видео и весов (P0).

## Пути: Windows и Mac

| Что | Windows (разработка) | Mac (боевой хост и обучение) |
|---|---|---|
| Репозиторий | `C:\Users\zhasy\datathon` | `~/cashguard`. Не `~/Desktop`, `~/Documents`, `~/Downloads` и не iCloud: TCC блокирует launchd, iCloud ломает SQLite WAL |
| Worktree агентов | `../cg-wt/<slug>` (то есть `C:\Users\zhasy\cg-wt\<slug>`), на диске C: рядом с кэшем uv | `~/cg-wt/<slug>` для сессий S3 и S6; модели и данные только в `~/cashguard`, поэтому в worktree `CG_MODELS_DIR=$HOME/cashguard/models`, `KASSA_DATA_DIR=$HOME/cashguard/data` ([roadmap.md](roadmap.md), раздел 4.3) |
| Python сервиса | `.venv\Scripts\python.exe` | `.venv/bin/python`; launchd вызывает абсолютный `~/cashguard/.venv/bin/python`, не `uv run` |
| Окружение обучения | не нужно | `.venv-train/` (`training/requirements.txt`); Label Studio — `.venv-ls/` |
| Данные | нет; агенты их не читают | `data/raw/open/videos/*.mov` |
| Модели | не нужны (`stub`, `replay`) | `models/pretrained/`, `models/hf/<repo>`, `models/<role>/vNNN/` |
| Состояние | `var/` в каждом worktree | `~/cashguard/var` |
| Replay-трассы | `var/signals/` из `just sync-signals` | `var/signals/` из `just extract-signals` |
| Секреты | файл `.env` вне git; точное место решает S1 в `core/settings.py` (проверить) | `~/.config/cashguard/.env` (права 600), путь в `CG_ENV_FILE` |
| Порт API | 8100–8199 по пути worktree (`scripts/port.py`); Vite на `:5173` проксирует `/api` | `127.0.0.1:8000` |
| Режим пайплайна | `CG_PIPELINE_MODE=stub\|replay`, `CG_VISION_BACKEND=stub\|replay\|torch`, `CG_VLM_BACKEND=null\|replay` | `full`, `coreml`, `mlx` (в SERVE; в DEV — только для G2 и `eval-full`) |
| Логи | `var/logs/*.jsonl` | `var/logs/*.jsonl`; падения супервизора — `~/Library/Logs/cashguard/supervisor.{out,err}.log` |
| Автозапуск | нет | `~/Library/LaunchAgents/kz.cashguard.*.plist` из `deploy/macos/launchd/*.plist.tmpl` |
| Туннель | нет | `~/.cloudflared/config.yml` из `deploy/cloudflared/config.yml.tmpl`, метрики `127.0.0.1:20241` |
| Настройки Ultralytics | — | `YOLO_CONFIG_DIR=~/cashguard/var/yolo` |
| Оболочка `just` | PowerShell | sh |

Окончания строк: `sh`, `plist`, `justfile`, `py`, `yaml` хранятся с LF (`.gitattributes`, P0). Скрипт с CRLF bash на macOS не выполнит.
Проверить: `training/README.md` клонирует репозиторий в `./datathon`, а сервису нужен `~/cashguard`. Согласовать S3 и S6.

## Соглашения об именах

| Что | Правило | Пример |
|---|---|---|
| Код нарушения | `UPPER_SNAKE_CASE`, член `ViolationCode` | `PC_UNLOCKED` |
| Код сигнала | `SIG_` + `UPPER_SNAKE_CASE`, член `SignalCode` | `SIG_SEAL_NOT_INSPECTED` |
| Группа правил | `leave`, `access`, `cashops`, `collection`, `discipline`, `signals` | — |
| Файл правила | `src/cashguard/pipeline/rules/<group>/<code в нижнем регистре>.py` | `rules/leave/pc_unlocked.py` |
| Файл сигнала | `src/cashguard/pipeline/rules/signals/<код в нижнем регистре>.py` | `rules/signals/sig_seal_not_inspected.py` |
| Каталог | `configs/catalog/<CODE>.yaml`; сигналы — все в `configs/catalog/signals.yaml` | `configs/catalog/PC_UNLOCKED.yaml` |
| Параметры правила | `configs/rules/<CODE>.yaml` | `configs/rules/PC_UNLOCKED.yaml` |
| Тест правила | `tests/unit/rules/<CODE>_test.py` | `tests/unit/rules/PC_UNLOCKED_test.py` |
| Фикстура правила | `tests/fixtures/timelines/<CODE>_{pos,neg}_<slug>.json` | `PC_UNLOCKED_neg_screen_locked.json` |
| Бэкенд | `backends/<name>.py`, имя = значение `CG_VISION_BACKEND` или `CG_VLM_BACKEND` | `perception/backends/coreml.py` |
| Переменная окружения | `CG_` + `UPPER_SNAKE_CASE` | `CG_PIPELINE_MODE` |
| Поля с русским текстом | суффикс `_ru` | `title_ru`, `template_ru`, `description_ru` |
| Модели Pydantic | `PascalCase`, frozen, `extra='forbid'` | `JuryJobResponse` |
| Роли моделей | `detector`, `pose`, `cls_screen`, `cls_safe_lock`, `cls_door`; версии `vNNN` | `models/detector/v001/` |
| Варианты Core ML | `det_864x480`, `det_480x864`, `pose_864x480`, `pose_480x864` (альбомные — ленивая загрузка), `cls_*` | — |
| Идентификатор задачи | uuid4, 32 hex-символа | — |
| Кадры-доказательства | `evidence/{i}_{event\|zoom\|context}.jpg` | `evidence/0_event.jpg` |
| Хранилище кадров | `frames/{pts_ms}.jpg` + `frames/index.json` | — |
| Replay-трасса | `var/signals/<sha16>__<models_hash>.json.gz` | — |
| Логи | `var/logs/{api,perception,vlm,supervisor}.jsonl` | — |
| Ветка и worktree | `<session>/<slug>`, `../cg-wt/<slug>` | `s4/door-not-closed` |
| ADR | `docs/adr/NNNN-<slug>.md` | — |
| LaunchAgents | `kz.cashguard.{supervisor,tunnel,caffeinate}` | — |
| Web | компоненты `PascalCase.tsx`, страницы `*Page.tsx`, хуки `useX`; русские строки только в `web/src/shared/i18n/ru.ts` | `ViolationCard.tsx`, `useJob` |
| Маркеры pytest | `mac` — только на Mac, `slow` — долгие | `@pytest.mark.mac` |
| Скрипты обучения | `NN_<действие>.py`, существующая нумерация | `08_train_detect.py` |

## Как добавить новый тип нарушения

Порядок важен: сначала контракт, потом каталог и правило, потом проверка на чистых роликах.

| Шаг | Файл | Кто |
|---|---|---|
| 1. Код в перечислении | `src/cashguard/contracts/catalog.py` (`ViolationCode`) — отдельный маленький PR, мержится первым | S0 |
| 2. Запись каталога | `configs/catalog/<CODE>.yaml`: `title_ru` (строка `type` для жюри, стабильная), `group`, `verdict_impact`, `timecode_policy` (`event`, `exit`, `state_start`, `omission_due`), `template_ru`, `required_facts`, `vlm_question_ru`, `legal`, `owner` | владелец кода |
| 3. Норма | `legal` в той же записи: `act_code` из `configs/legal/acts.yaml`, `clause`, `quote_ru` дословно из `analysis/normative/*.txt`, `url`, `strength` (`direct`, `indirect`, `none`). Только нормы из [../analysis/case_analysis.md](../analysis/case_analysis.md) §7; нет нормы — `strength: none`. Код, влияющий на вердикт, всегда имеет хотя бы одну запись `legal` с `act_code`; `clause` можно опустить только со ссылкой на FAQ 31 | владелец кода, S5 проверяет |
| 4. Пороги | `configs/rules/<CODE>.yaml`: τ, длительности, гистерезис | владелец кода |
| 5. Правило | `src/cashguard/pipeline/rules/<group>/<code>.py`: класс с `@register_rule`, протокол `Rule` (`code`, `version`, `verdict_impact`, `evaluate(ctx) -> list[Candidate]`). Ключи `facts` = `required_facts`; `EvidenceRequest` для кадров; `vlm_group` — по желанию | владелец группы |
| 6. Фикстуры | `tests/fixtures/timelines/<CODE>_pos_*.json` и `<CODE>_neg_*.json` (только числа, без кадров) | владелец кода |
| 7. Тесты | `tests/unit/rules/<CODE>_test.py` + snapshot описания (syrupy). Контрактный тест в `tests/contract/` проверит полноту: правило, норма, шаблон | владелец кода |
| 8. Новый признак сцены | новое `StateSubject` или `SceneEventKind` — изменение `contracts/scene.py` (PR к S0), затем `pipeline/scene/` | S0, S4 |
| 9. Новый класс детектора | `training/configs/classes.yaml`, переобучение, изменение `DetClass` в `contracts/perception.py` | S3, S0 |
| 10. Вопрос VLM | `vlm_question_ru` в каталоге; группировка — `pipeline/vlm/grouping.py` | S6 |
| 11. Проверка | `uv run just eval-replay`: diff против `eval/baselines/main.json`. LOSO recall по видео 1.0, ни одного нового срабатывания на 19 чистых роликах; иначе тип идёт как сигнал | владелец кода |
| 12. Документы | [violation_catalog.md](violation_catalog.md) (правит S0 по PR владельца кода); `docs/violation_types.md` перегенерировать (`scripts/render_docs.py`); [legal_sources.md](legal_sources.md), если появилась новая норма; [limitations.md](limitations.md), если есть оговорки | владелец кода, S5, S0 |

Интерфейс менять не нужно: он берёт типы из `GET /api/ui/catalog`. Новая группа требует подписи в `web/src/shared/i18n/ru.ts` (S2).
Таймкод для нарушения «по бездействию» ставится на момент, когда шаг уже должен был случиться (`timecode_policy: omission_due`, FAQ 10).

## Как добавить новый сигнал

Сигнал — наблюдение для проверяющего. Он не влияет на вердикт, не попадает в ответ жюри и показывается только в блоке интерфейса «Наблюдения (не влияют на вердикт)».

1. Код `SIG_*` в `SignalCode` (`src/cashguard/contracts/catalog.py`) — PR к S0.
2. Запись в `configs/catalog/signals.yaml`: `title_ru` с формулировкой «не наблюдается…». Поля `legal` нет: в v1 у сигналов норм нет ([legal_sources.md](legal_sources.md), раздел 4).
3. Файл `src/cashguard/pipeline/rules/signals/<код в нижнем регистре>.py`: класс с `@register_rule`, протокол `SignalRule` (`code`, `evaluate(ctx) -> list[Signal]`).
4. Пороги, если нужны, — в `configs/rules/<SIG_CODE>.yaml` в том же формате, что у правил (проверить с S0 при первом сигнале).
5. Фикстуры `tests/fixtures/timelines/<SIG_CODE>_{pos,neg}_*.json` и тест `tests/unit/rules/<SIG_CODE>_test.py`.
6. Раздел сигналов в [violation_catalog.md](violation_catalog.md) (правит S0 по PR S6).

Перевод сигнала в нарушение — это новый тип нарушения по порядку выше. Главное условие: ноль срабатываний на 19 чистых роликах.

## Как добавить бэкенд или конфиг

- **Бэкенд perception или VLM:** файл в `pipeline/perception/backends/` или `pipeline/vlm/backends/`, реализует протокол из `contracts/ports.py`. Новое значение `CG_VISION_BACKEND` или `CG_VLM_BACKEND` — изменение контракта настроек через S0.
- **Новый YAML в `configs/`:** Pydantic-модель для проверки при старте (через S0). Файл входит в `config_hash`, поэтому меняет ключ кэша результатов.
- **Новая зависимость:** только через S0, в `pyproject.toml` или `web/package.json`, с перегенерацией lock-файла.
