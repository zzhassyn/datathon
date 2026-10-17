# CashGuard backend — состояние и продолжение работы

Обновлено: 17.10.2026. Владелец: участник «Backend и интеграция» (S0+S1, см. `AGENTS.md`).
Документ для любого человека или ИИ-агента, который продолжает backend на другой машине.

## 1. Код

- Репозиторий: https://github.com/zzhassyn/datathon
- Объединённая ветка: `codex/fullstack-integration`. Исходная `s1/backend-core-jobs` сохранена. В `main` эта интеграция не объединена.
- Контракты (`src/cashguard/contracts/**`), каталог (`configs/catalog`, `configs/legal`),
  `pyproject.toml`, `uv.lock`, `justfile` уже в `main` (контрольная точка K0).

```bash
git clone --branch codex/fullstack-integration https://github.com/zzhassyn/datathon.git cashguard
cd cashguard
git pull --ff-only
```

Без force push. Если опубликованную ветку нужно переделать — новая кандидатная ветка.

## 2. Окружение

Python 3.12 и uv. Второй lock и `pip install` не используются: только `uv.lock`.

```bash
uv sync --locked                              # Windows / Linux, без ML
uv sync --locked --extra ml --extra mac       # Mac (сервис с моделями)
pnpm -C web install --frozen-lockfile
uv run just check                             # Python + web + дрейф схем
uv run just build-web
```

`.venv`, `var/`, SQLite, ключи, видео и модели в Git не передаются и для stub не нужны.

## 3. Запуск в демонстрационном режиме (stub)

```bash
export CG_PIPELINE_MODE=stub
export CG_DATA_DIR="$PWD/var"
export CG_API_KEYS='jury:local-synthetic-test,team:local-team-test'
uv run just dev 8127                          # supervisor: API + worker
```

PowerShell: `$env:CG_PIPELINE_MODE='stub'`, `$env:CG_DATA_DIR="$PWD\var"`,
`$env:CG_API_KEYS='jury:local-synthetic-test,team:local-team-test'`, затем `uv run just dev 8127`.

Во втором терминале:

```bash
uv run python scripts/make_synthetic_mov.py var/synthetic/violation.mov --verdict violation
uv run python scripts/make_synthetic_mov.py var/synthetic/clean.mov --verdict clean
CG_PYTHON=.venv/Scripts/python.exe bash scripts/smoke_jury.sh http://127.0.0.1:8127 local-synthetic-test var/synthetic/violation.mov
curl -sS http://127.0.0.1:8127/ready
```

На Mac/Linux `CG_PYTHON` не нужен. Ключи выше — публичные тестовые, только для синтетики.
Реальные ключи создаёт человек: `uv run just gen-key`.

Stub решает исход только по метатегу `comment=cg-stub:clean|violation`. Видео без метатега
получает `failed`, а не «нарушений нет». Каждый ответ UI в этом режиме содержит
`pipeline_mode: "stub"` и деградацию «Демонстрационный режим: AI-модели не подключены…».

## 4. Что реализовано

| Часть | Модули | Суть |
|---|---|---|
| Core | `core/*` | настройки `CG_*`, безопасные пути, атомарная запись с fsync, бюджет времени, structlog |
| Очередь | `jobs/*` | SQLite WAL, CAS-захват, `lease_token` на попытку, lease heartbeat, повторы, дедуп, retention |
| Jury API | `api/routes/jury.py` | `/api/v1/*`, ключ проверяется до чтения тела, потоковый multipart, 401/404/413/415/422/507 |
| UI API | `api/routes/ui.py`, `api/views.py` | загрузка без ключа жюри, опрос, `video.mp4` с Range 206, кадры по URL, каталог с ETag/304 |
| Каталог | `pipeline/legal/catalog.py` | 26 типов и 13 сигналов из `configs/catalog`, нормы с `applies_if`; при пустом основании — `legal_note_ru` |
| Рендер | `pipeline/render/*` | ответ жюри по замороженной схеме, ответ UI проверяется `UiJobView`; не прошёл — честный `failed` |
| Воркеры | `workers/*`, `pipeline/stub.py` | supervisor, spawn, рестарт с backoff, watchdog, корректное завершение |
| Оценка | `eval/*` | метрики видео/событий/типа/таймкода; отсутствующий результат не считается TN |

`/ready` в stub: `pipeline_mode: "stub"`, `models_loaded: false`, готовность без моделей.
`full` и `replay` пока завершаются ошибкой сразу: stub не маскируется под модели.

## 5. Контрольные точки

| Точка | Содержание | Статус |
|---|---|---|
| K0 | контракт UI, OpenAPI, эталонные ответы, каталог, режим и честная готовность | сделано |
| K1 | загрузка из браузера: прогресс, отмена, ошибки 413/415/507 на русском | сделано; 1148d10 |
| K2 | полный результат и медиа: HEVC, поворот, PTS, отсутствующие и повреждённые файлы | сделано на синтетике; c6ae275 |
| K3 | перезапуск, зависший worker, сроки, диск, предварительные события, восстановление | сделано; 302b77c |
| K4 | реальный пайплайн: наблюдения ML → правила → вердикт; replay-трассы | НЕ закрыта; адаптер результата, исполнение runner, вердикт и описания подготовлены |

## 6. Инварианты

- API жюри `/api/v1/*` не меняется; схема — `contracts/schemas/jury_job.schema.json`.
- Ключ жюри не встраивается во frontend; UI ходит в `/api/ui/*`.
- Нормы — только из `configs/catalog` и `configs/legal`. Нет основания — так и пишем.
- ML не импортируется в API-процесс (import-linter). Модели — в воркерах.
- Видео банка не открываются агентами и никуда не отправляются; облачных AI-вызовов нет.
- `web/` ведёт frontend, `training/` — ML. Изменения контрактов — отдельным PR раньше зависимых.

## 7. Известные ограничения на Windows

- Тесты symlink требуют Developer Mode: без него пропускаются с причиной.
- Тест SIGSTOP работает только на POSIX.
- Shell-smoke — через Git Bash с `CG_PYTHON`, HTTP-проверки — `curl.exe`, не алиас PowerShell.


## 8. Продолжение K4 — обязательные работы

Готовый адаптер: `pipeline/render/analysis.py::render_analysis` и
`PerceptionWorker.publish_analysis`. Он принимает утверждённый `AnalysisResult`,
проверяет ID/sha256, интервалы, первый кадр, JPEG/размеры и безопасные пути;
сохраняет immutable attempt и завершает задачу через lease/CAS. UI получает все
события и сигналы, jury — первые пять по времени. Нормы берутся из результата,
который должна сформировать legal-стадия. Адаптер не устанавливает применимость нормы.

**Это не реализация распознавания или правил.** Для закрытия K4 ещё необходимы:

1. ML: perception/evidence adapters, обученные веса, проверенные PTS/координаты,
   экспорт/manifest и числовые replay-трассы. Одних весов недостаточно.
2. Backend: реализация сборки сцены и конкретных правил. `load_stages` и
   `run_perception_stage` уже реализованы через явные фабрики и замороженные порты,
   `verdict.decide` и `legal.describe` проверены на синтетических входах.
   Необходима регистрация настоящего набора через `runtime.stage_factory` при старте
   каждого spawned worker; без него `load_stages` честно завершается ошибкой.
   Реальные pos/neg примеры и применимость норм нужны для проверки правил.
3. VLM: реализация worker/планировщика и budget fallback, прогрев и признаки готовности.
   Нельзя просто снять guards в `api/main.py`, `workers/supervisor.py`, `warmup.py`:
   иначе готовность и heartbeat прогресса будут недостоверными.
4. Прогресс runner уже связан с job/worker heartbeat через текущий lease; ещё нужна
   передача предварительного rules-результата в VLM через очередь. Текущий
   `process_pipeline` выполняет зарегистрированные стадии и публикует результат,
   VLM-планировщик пока отсутствует.
5. Сквозной прогон на отложенных роликах: результат + preview + несколько событий +
   четыре секунды контекста + соответствующие JPEG + нормы; замер времени и памяти.
   Проверки с внедрённым контрактным результатом этот пункт не закрывают.

Новые публичные API-поля для реализованных K0–K3 и адаптера K4 не понадобились.
Frozen jury schema не менялась. Шаги выше выполняются до включения full/replay.

18.10.2026: первый backend-срез K4 выполняется только с явно предоставленными
адаптерами. Сохраняются числовые `trace.json.gz`, `scene.json`, `candidates.json`,
`decisions.json` и проверенные файлы результата. Неполная трасса, неподключённые или
упавшие правила, неверные PTS/evidence не превращаются в чистый вердикт. Все пороги
включённых кодов задаются явно. Статус полной K4 по-прежнему открыт.

## 9. Проверено в интеграционной ветке

17.10.2026, macOS: `uv run just check` — 675 Python-тестов, 32 Vitest-теста,
ruff/format, basedpyright, 7 границ импортов, экспорт 14 схем, Biome, tsc и TS drift.
`pnpm build` — начальный JS 98,17 КБ gzip. Playwright — 48/48 (Chrome desktop/mobile,
WebKit desktop/iPhone) против объединённого stub-сервера на порту 8131.
HEVC/повороты/VFR, публикация типизированных результатов — отдельные Python-тесты
на синтетике. Реальная точность ML, latency полного пайплайна и Windows в этом
прогоне не проверялись. Неустранимых падений тестов нет; остаётся предупреждение
Starlette о будущем переходе TestClient с httpx на httpx2, версии зависимостей не менялись.
